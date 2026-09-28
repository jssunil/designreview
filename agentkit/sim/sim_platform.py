"""
SimPlatform: replays a SeatFixture offline, for the agent AND the harness.

It presents both surfaces the code uses:
  - agent side: invoke_tool / offers / seat_catalog / initialize (like SeatRpcClient)
  - harness side: `.rest` (list_all / get / raw) and `.mcp` (itself), so it can
    stand in for GroundTruthReader.

Replay is exact: a call whose (tool, canonical args) was not captured raises
SeatCallFailure(kind="unclassified", "not in fixture ...") -- a gap in the
fixture is loud, never a silent empty result.

Faults (strings), for testing how the agent and harness behave when things go wrong:
  drop_tool:<tool>               the tool is absent from the catalogue and refuses (not_in_seat)
  fail_once:<tool>:<kind>        the next call of <tool> fails with <kind> (e.g. flaky, missing)
  fail_always:<tool>:<kind>      every call of <tool> fails with <kind>
  edit_text:<old>=><new>         after apply_edits(), every recorded result has <old> replaced
                                 by <new> -- "another team edited the record during the run"
Call faults fire only while `armed` is True; the harness arms them around the
agent's run so its own ground-truth reads are not disturbed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from agentkit.sim.fixture import SeatFixture, canonical
from agentkit.transport import KINDS, NOT_IN_SEAT, UNCLASSIFIED, SeatCallFailure

FAULT_KINDS = ("drop_tool", "fail_once", "fail_always", "edit_text")


def parse_fault(fault: str) -> Tuple[str, List[str]]:
    kind, _, rest = fault.partition(":")
    if kind not in FAULT_KINDS:
        raise ValueError(f"unknown fault {fault!r}; kinds: {', '.join(FAULT_KINDS)}")
    if kind == "edit_text":
        old, sep, new = rest.partition("=>")
        if not sep or not old:
            raise ValueError(f"edit_text fault needs '<old>=><new>': {fault!r}")
        return kind, [old, new]
    parts = rest.split(":") if rest else []
    need = {"drop_tool": 1, "fail_once": 2, "fail_always": 2}[kind]
    if len(parts) != need or not all(parts):
        raise ValueError(f"fault {fault!r} needs {need} argument(s)")
    if kind in ("fail_once", "fail_always") and parts[1] not in KINDS:
        raise ValueError(f"fault {fault!r}: unknown failure kind {parts[1]!r}")
    return kind, parts


def _replace_text(obj: Any, old: str, new: str) -> Any:
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, dict):
        return {k: _replace_text(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_text(v, old, new) for v in obj]
    return obj


class _SimRest:
    def __init__(self, platform: "SimPlatform"):
        self._p = platform

    def list_all(self, entity: str, **params: Any) -> Any:
        return self._p._replay("rest", f"list_all:{entity}", params)

    def get(self, entity: str, record_id: str) -> Any:
        return self._p._replay("rest", f"get:{entity}", {"id": record_id})

    def raw(self, path: str, **params: Any) -> Any:
        return self._p._replay("rest", f"raw:{path}", params)


class SimPlatform:
    def __init__(self, fixture: Union[SeatFixture, str, Path], faults: Iterable[str] = (),
                 extra_read_only: Iterable[str] = ()):
        self.fixture = fixture if isinstance(fixture, SeatFixture) else SeatFixture.load(Path(fixture))
        self._calls = self.fixture.index()
        self.faults = [parse_fault(f) for f in faults if f]
        self.fired: set = set()
        self.armed = True
        self.edits_applied = False
        self.calls: List[Tuple[str, str, Dict[str, Any]]] = []  # (channel, op, args) as seen
        self.extra_read_only = frozenset(extra_read_only)
        self.rest = _SimRest(self)
        self.mcp = self
        self.dropped = {args[0] for kind, args in self.faults if kind == "drop_tool"}

    # ---- agent / harness MCP surface -------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        return {"protocolVersion": "sim", "serverInfo": {"name": "SimPlatform", "tenant": self.fixture.tenant}}

    def seat_catalog(self, refresh: bool = False) -> Dict[str, dict]:
        return {t: {} for t in self.fixture.tools if t not in self.dropped}

    def offers(self, tool: str) -> bool:
        return tool in self.seat_catalog()

    def invoke_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        if name in self.dropped:
            self.calls.append(("mcp", name, dict(arguments or {})))
            raise SeatCallFailure(f"{name}: tool not available to this seat (sim: dropped)", NOT_IN_SEAT, tool=name)
        return self._replay("mcp", name, arguments or {})

    # ---- replay ------------------------------------------------------------------------

    def _call_fault(self, op: str) -> Optional[str]:
        if not self.armed:
            return None
        for i, (kind, args) in enumerate(self.faults):
            if kind == "fail_always" and args[0] == op:
                return args[1]
            if kind == "fail_once" and args[0] == op and i not in self.fired:
                self.fired.add(i)
                return args[1]
        return None

    def _replay(self, channel: str, op: str, args: Dict[str, Any]) -> Any:
        self.calls.append((channel, op, dict(args)))
        tool = op if channel == "mcp" else op.partition(":")[2]
        fault_kind = self._call_fault(op if channel == "mcp" else tool)
        if fault_kind:
            raise SeatCallFailure(f"{op}: injected fault ({fault_kind})", fault_kind, tool=op)
        rec = self._calls.get((channel, op, canonical(args)))
        if rec is None:
            raise SeatCallFailure(f"{op}({canonical(args)}): not in fixture -- re-capture it", UNCLASSIFIED, tool=op)
        if rec.error_kind:
            raise SeatCallFailure(rec.error or f"{op}: {rec.error_kind}", rec.error_kind, tool=op)
        return json.loads(json.dumps(rec.result))  # a fresh copy every time

    # ---- scripted drift ------------------------------------------------------------------

    def apply_edits(self) -> int:
        """Fire every edit_text fault: rewrite recorded results in place."""
        n = 0
        for kind, args in self.faults:
            if kind != "edit_text":
                continue
            old, new = args
            for key, rec in self._calls.items():
                if rec.result is not None:
                    changed = _replace_text(rec.result, old, new)
                    if changed != rec.result:
                        self._calls[key] = rec.model_copy(update={"result": changed})
                        n += 1
        self.edits_applied = True
        return n

    def tools_called(self) -> List[str]:
        return [op for ch, op, _ in self.calls if ch == "mcp"]
