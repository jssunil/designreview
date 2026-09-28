"""
JournaledSeatClient: every MCP tool call a run made, as the transport saw it.

Wraps a SeatRpcClient and appends one JSON line per `invoke_tool` to
runs/<run_id>/tool_journal.jsonl -- tool, arguments, outcome (ok / error
kind), a small result summary, timing. This is the verifiers' source for
"which tools did the run actually touch" (e.g. "a dry run made no write
call"), so that claim never rests on the agent's report of itself.

Only tool arguments are logged -- never the bearer token, login body or
headers. Long argument strings are truncated.
"""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from agentkit.record.durable_io import JsonlAppender, read_jsonl
from agentkit.transport.seat_rpc import SeatCallFailure, is_read_tool

JOURNAL_FILE = "tool_journal.jsonl"
MAX_ARG_CHARS = 500
MAX_IDS = 50


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_ARG_CHARS:
        return value[:MAX_ARG_CHARS] + f"...(+{len(value) - MAX_ARG_CHARS} chars)"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def summarise_result(payload: Any) -> Any:
    """Keep journals small: list pages become {total, count, ids}; single
    records become {id}; anything else becomes its type name."""
    if isinstance(payload, dict):
        rows = payload.get("data")
        if isinstance(rows, list):
            return {"total": payload.get("total"), "count": len(rows),
                    "ids": [r.get("id") for r in rows[:MAX_IDS] if isinstance(r, dict)]}
        if "id" in payload:
            return {"id": payload.get("id")}
        return {"keys": sorted(payload)[:20]}
    if isinstance(payload, list):
        return {"count": len(payload)}
    return {"type": type(payload).__name__}


class JournaledSeatClient:
    def __init__(self, client: Any, path: Path, extra_read_only: tuple = ()):
        self._client = client
        self.path = Path(path)
        self._log = JsonlAppender(self.path)
        self._seq = itertools.count(1)
        self._seq_lock = threading.Lock()
        self.extra_read_only = frozenset(extra_read_only) | frozenset(getattr(client, "extra_read_only", ()))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @property
    def count(self) -> int:
        return len(read_jsonl(self.path))

    def invoke_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        with self._seq_lock:
            seq = next(self._seq)
        entry = {"seq": seq, "tool": name, "args": _clip(arguments or {}),
                 "read": is_read_tool(name, self.extra_read_only), "started_at": time.time()}
        t0 = time.time()
        try:
            result = self._client.invoke_tool(name, arguments)
        except SeatCallFailure as e:
            self._log.append({**entry, "ok": False, "error_kind": e.kind, "error": str(e)[:300],
                              "seconds": round(time.time() - t0, 3)})
            raise
        except Exception as e:
            self._log.append({**entry, "ok": False, "error_kind": "internal",
                              "error": f"{type(e).__name__}: {e}"[:300], "seconds": round(time.time() - t0, 3)})
            raise
        self._log.append({**entry, "ok": True, "error_kind": None, "result": summarise_result(result),
                          "seconds": round(time.time() - t0, 3)})
        return result


def read_journal(run_dir_or_file: Path) -> list:
    p = Path(run_dir_or_file)
    return read_jsonl(p / JOURNAL_FILE if p.is_dir() else p)
