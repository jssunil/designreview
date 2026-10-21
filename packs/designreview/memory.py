"""
What the design-review agent keeps after an audited answer, and how it compares (agentkit.memory).

Kept, by design file: the file's current version, the release gate verdict and the version it was
evaluated on, the open blockers (id and title, as cleaned by the finding builder) and the time of
the snapshot. The comparison names what changed since that review: the version, the gate verdict,
the blockers that are new and the blockers that cleared.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from agentkit.memory import MemorySpec

KIND = "design_review"


def facts(finding: Dict[str, Any]) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    if finding.get("outcome") != "answered":
        return None
    file = finding.get("file") or {}
    gate = finding.get("release_gate") or {}
    if not file.get("id") or gate.get("outcome") != "answered":
        return None
    return file["id"], KIND, {
        "file_number": file.get("number"),
        "current_version": file.get("current_version"),
        "gate_ready": bool(gate.get("ready")),
        "gate_version": gate.get("version_number"),
        "blockers": [{"id": b.get("id"), "title": b.get("title")} for b in gate.get("blockers") or []],
        "snapshot_at": finding.get("snapshot_at"),
    }


def compare(previous: Dict[str, Any], now: Dict[str, Any]) -> Dict[str, Any]:
    then = previous.get("facts") or {}
    before = {b["id"]: b for b in then.get("blockers") or [] if b.get("id")}
    after = {b["id"]: b for b in now.get("blockers") or [] if b.get("id")}
    new = [after[i] for i in after if i not in before]
    cleared = [before[i] for i in before if i not in after]
    return {
        "outcome": "answered",
        "run_id": previous.get("run_id"),
        "reviewed_at": then.get("snapshot_at") or previous.get("ts"),
        "version_then": then.get("current_version"),
        "version_now": now.get("current_version"),
        "gate_passed_then": then.get("gate_ready"),
        "gate_passed_now": now.get("gate_ready"),
        "new_blockers": new,
        "cleared_blockers": cleared,
        "changed": bool(new or cleared or then.get("current_version") != now.get("current_version")
                        or then.get("gate_ready") != now.get("gate_ready")),
    }


MEMORY = MemorySpec(facts=facts, compare=compare)
