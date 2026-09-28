"""
Platform fixtures: every MCP and REST exchange a set of tasks needs, recorded
once against the live platform and replayed offline by SimPlatform.

Capture is generic: RecordingSeat / RecordingRest wrap the real clients and
note each call (tool or entity, canonical arguments, result or error kind).
Running the harness's own tasks and ground-truth probes through them yields
exactly the calls an offline run will make -- nothing is hand-written.

The fixture file is JSON, validated by a pydantic model on load.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentkit.config import ConfigError, validation_message
from agentkit.record.durable_io import dump_record, load_record
from agentkit.transport import SeatCallFailure

FIXTURE_FORMAT = "agentkit-seat-fixture-v1"


def canonical(args: Optional[Dict[str, Any]]) -> str:
    return json.dumps(args or {}, sort_keys=True, default=str)


class RecordedCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["mcp", "rest"]
    op: str  # mcp: tool name; rest: "list_all:<Entity>" | "get:<Entity>" | "raw:<path>"
    args: str  # canonical JSON of the arguments
    result: Any = None
    error_kind: Optional[str] = None
    error: Optional[str] = None


class SeatFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["agentkit-seat-fixture-v1"] = FIXTURE_FORMAT
    tenant: str
    captured_at: str
    note: str = ""
    tools: List[str] = Field(default_factory=list)
    calls: List[RecordedCall] = Field(default_factory=list)

    def index(self) -> Dict[tuple, RecordedCall]:
        return {(c.channel, c.op, c.args): c for c in self.calls}

    def save(self, path: Path) -> Path:
        return dump_record(Path(path), self.model_dump())

    @classmethod
    def load(cls, path: Path) -> "SeatFixture":
        try:
            return cls.model_validate(load_record(Path(path)))
        except ValidationError as e:
            raise ConfigError(validation_message(e, str(path))) from None
        except FileNotFoundError:
            raise ConfigError(f"fixture not found: {path}") from None


class _Recorder:
    def __init__(self) -> None:
        self.calls: Dict[tuple, RecordedCall] = {}

    def note(self, channel: str, op: str, args: Dict[str, Any], fn):
        key = (channel, op, canonical(args))
        try:
            result = fn()
        except SeatCallFailure as e:
            self.calls[key] = RecordedCall(channel=channel, op=op, args=key[2], error_kind=e.kind, error=str(e)[:300])
            raise
        self.calls[key] = RecordedCall(channel=channel, op=op, args=key[2], result=result)
        return result


class RecordingSeat:
    """Wraps a SeatRpcClient; records every tools/call and the catalogue."""

    def __init__(self, client: Any, recorder: _Recorder):
        self._client, self._rec = client, recorder
        self.extra_read_only = getattr(client, "extra_read_only", frozenset())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def invoke_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        return self._rec.note("mcp", name, arguments or {}, lambda: self._client.invoke_tool(name, arguments))


class RecordingRest:
    def __init__(self, rest: Any, recorder: _Recorder):
        self._rest, self._rec = rest, recorder

    def list_all(self, entity: str, **params: Any) -> Any:
        return self._rec.note("rest", f"list_all:{entity}", params, lambda: self._rest.list_all(entity, **params))

    def get(self, entity: str, record_id: str) -> Any:
        return self._rec.note("rest", f"get:{entity}", {"id": record_id}, lambda: self._rest.get(entity, record_id))

    def raw(self, path: str, **params: Any) -> Any:
        return self._rec.note("rest", f"raw:{path}", params, lambda: self._rest.raw(path, **params))


def capture_fixture(tasks: List[Any], tenant: str, *, note: str = "") -> SeatFixture:
    """Run each task (template answers, no LLM) and its ground-truth probes
    against the live tenant through recording clients."""
    from agentkit.harness.ground_truth import GroundTruthReader, capture, keys_for
    from agentkit.harness.run_one import run_task
    from agentkit.registry import load_pack
    from agentkit.transport import RestReader, SeatRpcClient

    rec = _Recorder()
    tools: set = set()
    with tempfile.TemporaryDirectory() as tmp:
        for task in tasks:
            registry = load_pack(task.pack)
            agent = SeatRpcClient.for_tenant(tenant, extra_read_only=registry.read_only)
            agent.initialize()
            tools |= set(agent.seat_catalog())
            run_task(task, tenant, Path(tmp) / task.id, task.id, skip_llm=True, registry=registry,
                     client=RecordingSeat(agent, rec))
            harness_mcp = SeatRpcClient.for_tenant(tenant, client_name="agentkit-harness")
            harness_mcp.initialize()
            reader = GroundTruthReader(rest=RecordingRest(RestReader.for_tenant(tenant), rec),
                                       mcp=RecordingSeat(harness_mcp, rec))
            capture(registry, reader, keys_for(task.verifiers, registry))
    return SeatFixture(tenant=tenant, captured_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       note=note, tools=sorted(tools), calls=list(rec.calls.values()))
