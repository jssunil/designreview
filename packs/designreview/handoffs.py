"""
Cross-seat hand-offs and the project schedule.

  list_milestones              DesignMilestone rows for the file's project (read)
  find_cross_seat_dependencies open serious items (gate blockers + high/critical
                               feedback) matched against handoffs.toml topics:
                               one proposed hand-off per owner (read-only, pure)
  raise_handoffs               WRITE action. Its preview is the exact
                               AgentEscalation.create call each hand-off would
                               make; in a dry run the engine records that preview
                               (node HANDED_OFF) and files nothing. Filing for real
                               is not enabled in this build.

The topic table is data (handoffs.toml, validated by pydantic): which owner
gets which kind of problem is a team decision, not code.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentkit.config import ConfigError, validation_message
from agentkit.graph import RESOLVED, RunEnv, declined
from agentkit.registry import Registry

HANDOFFS_PATH = Path(__file__).resolve().parent / "handoffs.toml"
CLOSED_FEEDBACK = {"resolved", "closed", "rejected", "wont_fix", "won't_fix", "cancelled", "duplicate"}
SERIOUS = {"critical", "high"}
MILESTONE_KEYS = ("id", "name", "milestone_type", "status", "due_date", "completed_date", "is_critical_path",
                  "gate_result", "depends_on_milestone_id")
FINISHED_MILESTONE = {"completed", "skipped"}
SESSION_PLACEHOLDER = "<AgentSession created when filed>"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HandoffTopic(_Strict):
    name: str
    owner: str
    ask: str
    keywords: List[str] = Field(min_length=1)

    def matches(self, text: str) -> List[str]:
        low = (text or "").lower()
        return [k for k in self.keywords if re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])", low)]


class HandoffTable(_Strict):
    subject_prefix: str = "T21-DR"
    reason_code: str = "needs_another_app"
    sla_minutes: int = 60
    topics: List[HandoffTopic] = Field(min_length=1)


def load_handoff_table(path: Path = HANDOFFS_PATH) -> HandoffTable:
    try:
        return HandoffTable.model_validate(tomllib.loads(Path(path).read_text(encoding="utf-8")))
    except ValidationError as e:
        raise ConfigError(validation_message(e, str(path))) from None


# ---------------------------------------------------------------- pure helpers

def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [r for r in payload["data"] if isinstance(r, dict)]
    return []


def _resolved(results: Dict[str, Any], node: str) -> Optional[Any]:
    r = results.get(node)
    return r.data if r is not None and r.verdict == RESOLVED else None


def open_serious_items(gate_payload: Any, feedback_payload: Any) -> List[Dict[str, Any]]:
    """Release-gate blockers plus open high/critical feedback, one entry per id
    (a blocker is usually also a feedback row; it's kept once, marked blocker)."""
    items: Dict[str, Dict[str, Any]] = {}
    gate = gate_payload.get("result", gate_payload) if isinstance(gate_payload, dict) else {}
    for b in gate.get("blockers") or []:
        if isinstance(b, dict) and b.get("id"):
            items[b["id"]] = {"id": b["id"], "title": b.get("title") or "", "priority": b.get("priority"),
                              "status": b.get("status"), "source": "release_blocker"}
    for r in _rows(feedback_payload):
        if not r.get("id") or r["id"] in items:
            continue
        if str(r.get("status") or "").lower() in CLOSED_FEEDBACK or str(r.get("priority") or "").lower() not in SERIOUS:
            continue
        items[r["id"]] = {"id": r["id"], "number": r.get("number"), "title": r.get("title") or "",
                          "priority": r.get("priority"), "status": r.get("status"), "source": "feedback"}
    return list(items.values())


def propose_handoffs(items: List[Dict[str, Any]], table: HandoffTable) -> List[Dict[str, Any]]:
    by_owner: Dict[str, Dict[str, Any]] = {}
    for item in items:
        for topic in table.topics:
            hits = topic.matches(item["title"])
            if not hits:
                continue
            h = by_owner.setdefault(topic.owner, {"owner": topic.owner, "topics": [], "ask": topic.ask,
                                                   "reason_code": table.reason_code, "cites": []})
            if topic.name not in h["topics"]:
                h["topics"].append(topic.name)
            if item["id"] not in {c["id"] for c in h["cites"]}:
                h["cites"].append({**item, "matched": hits})
    return list(by_owner.values())


def escalation_call(handoff: Dict[str, Any], file: Dict[str, Any], table: HandoffTable) -> Dict[str, Any]:
    """The AgentEscalation.create call a hand-off would make (Part 3 files it)."""
    label = file.get("number") or file.get("id") or "design file"
    cited = "; ".join(f"[{c.get('priority')}] {c.get('title')}" for c in handoff["cites"])
    return {"tool": "AgentEscalation.create", "args": {
        "session_id": SESSION_PLACEHOLDER,
        "reason_code": handoff["reason_code"],
        "subject": f"{table.subject_prefix} {label}: {handoff['owner']} needed",
        "reason": f"{label} ({file.get('name') or ''}) is blocked on items design review cannot close: {cited}. "
                  f"Ask: {handoff['ask']}"}}


def milestone_view(row: Dict[str, Any], today: str) -> Dict[str, Any]:
    m = {k: row.get(k) for k in MILESTONE_KEYS}
    due = (m.get("due_date") or "")[:10]
    m["past_due"] = bool(due) and due < today and str(m.get("status") or "").lower() not in FINISHED_MILESTONE
    return m


# ---------------------------------------------------------------- actions

def register_handoffs(reg: Registry, table: Optional[HandoffTable] = None) -> None:
    table = table or load_handoff_table()
    reg.extras["handoff_table"] = table

    @reg.action("list_milestones", retries=2, description="DesignMilestone rows of the design file's project.")
    def list_milestones(env: RunEnv, args: Dict[str, Any], results) -> Any:
        project_id = args.get("project_id") or (_resolved(results, "design_file") or {}).get("project_id")
        if not project_id:
            return declined("the design file has no project, so there is no schedule to read")
        return env.client.invoke_tool("DesignMilestone.list", {"project_id": project_id, "sort_by": "due_date",
                                                                 "sort_order": "asc", "limit": 100})

    @reg.action("find_cross_seat_dependencies",
                description="Open serious items on the file that another team must act on (handoffs.toml).")
    def find_cross_seat_dependencies(env: RunEnv, args: Dict[str, Any], results) -> Any:
        gate, feedback = _resolved(results, "release_gate"), _resolved(results, "feedback")
        missing = [n for n, v in (("release_gate", gate), ("feedback", feedback)) if v is None]
        items = open_serious_items(gate, feedback)
        return {"items_considered": len(items), "handoffs": propose_handoffs(items, table),
                "sources_unavailable": missing}

    def preview(env: RunEnv, args: Dict[str, Any], results) -> List[Dict[str, Any]]:
        deps = _resolved(results, "cross_seat") or {}
        file = _resolved(results, "design_file") or {}
        return [{"owner": h["owner"], **escalation_call(h, file, table)} for h in deps.get("handoffs") or []]

    @reg.action("raise_handoffs", writes=True, preview=preview,
                description="File one escalation per proposed hand-off (dry run: preview only).")
    def raise_handoffs(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return declined("filing hand-offs on the platform is not enabled in this build")
