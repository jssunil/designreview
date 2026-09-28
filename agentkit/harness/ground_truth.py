"""
Ground truth: what the platform itself says, captured by the harness with
its OWN login -- before and after each agent run -- and saved to disk.

Probe keys are strings "kind:arg" (e.g. "versions:<file_id>"); the pack
registers one reader per kind. An observation that fails is recorded with
its error kind rather than raised: "missing" is itself ground truth for a
refusal task. FLAKY reads are retried (every probe is a read).

GroundTruthReader bundles two fresh sessions for the probes to use:
`rest` (REST, a different code path from the agent's MCP client) and `mcp`
(a separate MCP login, for computed endpoints that REST can't GET).
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from agentkit.registry import Registry
from agentkit.transport import FLAKY, RestReader, SeatCallFailure, SeatRpcClient

OBSERVE_ATTEMPTS = 3
OBSERVE_BACKOFF_S = 1.0


@dataclass
class GroundTruthReader:
    rest: Any
    mcp: Any

    @classmethod
    def for_tenant(cls, tenant: str) -> "GroundTruthReader":
        mcp = SeatRpcClient.for_tenant(tenant, client_name="agentkit-harness")
        mcp.initialize()
        return cls(rest=RestReader.for_tenant(tenant), mcp=mcp)


def observe(registry: Registry, reader: Any, key: str, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    kind, _, arg = key.partition(":")
    probe = registry.probes.get(kind)
    if probe is None:
        return {"error_kind": "internal", "error": f"no probe registered for {kind!r}"}
    for attempt in range(OBSERVE_ATTEMPTS):
        try:
            return {"value": probe(reader, arg)}
        except SeatCallFailure as e:
            if e.kind == FLAKY and attempt < OBSERVE_ATTEMPTS - 1:
                sleep(OBSERVE_BACKOFF_S * 2 ** attempt)
                continue
            return {"error_kind": e.kind, "error": str(e)[:300], "attempts": attempt + 1}
        except Exception as e:  # a probe bug is recorded, never allowed to crash the batch
            return {"error_kind": "internal", "error": f"{type(e).__name__}: {e}"[:300]}
    return {"error_kind": "internal", "error": "unreachable"}  # pragma: no cover


def capture(registry: Registry, reader: Any, keys: Iterable[str],
            sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    return {"observed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "observations": {k: observe(registry, reader, k, sleep) for k in keys}}


def keys_for(verifiers: List[Dict[str, Any]], registry: Registry) -> List[str]:
    keys: List[str] = []
    for v in verifiers:
        name, params = (v["name"], v.get("params")) if isinstance(v, dict) else (v.name, v.params)
        spec = registry.checks.get(name)
        if spec is None:
            continue
        for k in spec.observes(params or {}) or []:
            if k not in keys:
                keys.append(k)
    return keys
