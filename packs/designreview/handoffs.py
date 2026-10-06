"""
Cross-seat hand-offs and the project schedule.

  list_milestones              DesignMilestone rows for the file's project (read)
  find_cross_seat_dependencies open serious items (gate blockers + high/critical
                               feedback) matched against handoffs.toml topics:
                               one proposed hand-off per owner (read-only, pure)
  raise_handoffs               WRITE action, one escalation per hand-off:
                                 AgentSession.create (titled with the subject)
                                 -> endpoint.agent_governance.escalations.raise
                                    to the tenant's configured assignee
                               Dry run: the engine records the preview (the exact
                               calls) and files nothing (node HANDED_OFF).
                               Write run (--write + task allow_writes): files them;
                               declines when the tenant has no assignable person;
                               skips a hand-off whose escalation is already open.
  cleanup (extras)             withdraws our escalations and closes their sessions;
                               the batch runs it after the "after" ground truth.

The topic table and the assignee per tenant are data (handoffs.toml,
validated by pydantic), and the assignee is looked up by name on every run.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentkit.config import ConfigError, validation_message
from agentkit.graph import FAILED, RESOLVED, NodeResult, RunEnv, declined
from agentkit.record import load_record
from agentkit.registry import Registry
from agentkit.transport import SeatCallFailure

HANDOFFS_PATH = Path(__file__).resolve().parent / "handoffs.toml"
CLOSED_FEEDBACK = {"resolved", "closed", "rejected", "wont_fix", "won't_fix", "cancelled", "duplicate"}
SERIOUS = {"critical", "high"}
MILESTONE_KEYS = ("id", "name", "milestone_type", "status", "due_date", "completed_date", "is_critical_path",
                  "gate_result", "depends_on_milestone_id")
FINISHED_MILESTONE = {"completed", "skipped"}
OPEN_ESCALATION = {"open", "acknowledged"}
SESSION_PLACEHOLDER = "<AgentSession created when filed>"

SESSION_CREATE = "AgentSession.create"
SESSION_CLOSE = "AgentSession.close.active.closed"
RAISE = "endpoint.agent_governance.escalations.raise"
UPDATE = "endpoint.agent_governance.escalations.update"
ASSIGNEES = "endpoint.agent_governance.escalations.assignees"
# What a write run of this pack may call (checked from the journal by tool_calls_policy).
WRITE_TOOLS = (SESSION_CREATE, RAISE, SESSION_CLOSE)
# Reads that don't end in .list/.get.
READ_ONLY_ENDPOINTS = (ASSIGNEES, "endpoint.agent_governance.escalations")


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
    # tenant -> the person escalations go to, by display name (resolved to a party id per run)
    assignees: Dict[str, str] = Field(default_factory=dict)
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


def _result(payload: Any) -> Dict[str, Any]:
    """Computed endpoints answer {"status": ..., "result": {...}}."""
    if isinstance(payload, dict):
        inner = payload.get("result", payload)
        return inner if isinstance(inner, dict) else {}
    return {}


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


def subject_for(file: Dict[str, Any], owner: str, table: HandoffTable) -> str:
    return f"{table.subject_prefix} {file.get('number') or file.get('id') or 'design file'}: {owner} needed"


def escalation_call(handoff: Dict[str, Any], file: Dict[str, Any], table: HandoffTable,
                    assignee: Optional[str] = None) -> Dict[str, Any]:
    """The calls one hand-off makes when filed: a session titled with the
    subject (the escalation takes its subject from the session), then raise."""
    label = file.get("number") or file.get("id") or "design file"
    cited = "; ".join(f"[{c.get('priority')}] {c.get('title')}" for c in handoff["cites"])
    subject = subject_for(file, handoff["owner"], table)
    return {"subject": subject,
            "session": {"tool": SESSION_CREATE, "args": {"title": subject, "channel": "api"}},
            "tool": RAISE, "args": {
                "session_id": SESSION_PLACEHOLDER,
                "assignee_party_id": f"<{assignee}, resolved when filed>" if assignee
                else "<no assignee configured for this tenant: would not be filed>",
                "reason_code": handoff["reason_code"],
                "sla_minutes": table.sla_minutes,
                "reason": f"{label} ({file.get('name') or ''}) is blocked on items design review cannot close: "
                          f"{cited}. Ask: {handoff['ask']}"}}


def milestone_view(row: Dict[str, Any], today: str) -> Dict[str, Any]:
    m = {k: row.get(k) for k in MILESTONE_KEYS}
    due = (m.get("due_date") or "")[:10]
    m["past_due"] = bool(due) and due < today and str(m.get("status") or "").lower() not in FINISHED_MILESTONE
    return m


# ---------------------------------------------------------------- platform helpers (write mode, clean-up)

def find_assignee(client: Any, name: str) -> Optional[Dict[str, Any]]:
    options = _result(client.invoke_tool(ASSIGNEES, {"q": name})).get("options") or []
    want = name.strip().lower()
    return next((o for o in options if (o.get("label") or o.get("display") or "").strip().lower() == want), None)


def open_escalations_with_subject(client: Any, subject: str) -> List[Dict[str, Any]]:
    rows = _rows(client.invoke_tool("AgentEscalation.list", {"subject": subject, "limit": 50}))
    return [r for r in rows if r.get("subject") == subject and str(r.get("status") or "").lower() in OPEN_ESCALATION]


def _close_session(client: Any, session_id: Optional[str]) -> Optional[str]:
    if not session_id:
        return None
    try:
        client.invoke_tool(SESSION_CLOSE, {"id": session_id})
        return None
    except SeatCallFailure as e:
        return f"{e.kind}: {e}"[:200]


def file_handoffs(client: Any, previews: List[Dict[str, Any]], assignee_name: Optional[str], tenant: str,
                  table: HandoffTable) -> Any:
    base = {"would_file": previews, "filed": False, "assignee": assignee_name}
    if not assignee_name:
        return declined(f"no escalation assignee is configured for tenant {tenant!r}; nothing filed",
                        data=base)
    person = find_assignee(client, assignee_name)
    if person is None:
        return declined(f"{assignee_name!r} is not an assignable person on {tenant!r} "
                        f"(escalations.assignees); nothing filed", data=base)
    filed: List[Dict[str, Any]] = []
    already: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for p in previews:
        subject = p["subject"]
        existing = open_escalations_with_subject(client, subject)
        if existing:  # never file the same hand-off twice
            e = existing[0]
            already.append({"owner": p["owner"], "id": e.get("id"), "number": e.get("number"), "subject": subject,
                            "status": e.get("status"), "assignee": e.get("assignee_display")})
            continue
        session_id = None
        try:
            session = client.invoke_tool(SESSION_CREATE, p["session"]["args"])
            session_id = session.get("id") if isinstance(session, dict) else None
            if not session_id:
                raise RuntimeError(f"AgentSession.create returned no id: {str(session)[:120]}")
            res = _result(client.invoke_tool(RAISE, {**p["args"], "session_id": session_id,
                                                     "assignee_party_id": person["id"]}))
        except (SeatCallFailure, RuntimeError) as e:
            errors.append({"owner": p["owner"], "subject": subject, "error": str(e)[:200],
                           "session_closed": _close_session(client, session_id) is None if session_id else None})
            continue
        esc = res.get("escalation") if isinstance(res.get("escalation"), dict) else {}
        if not res.get("ok") or not esc.get("id"):
            errors.append({"owner": p["owner"], "subject": subject, "refused": res.get("reason_code") or "no escalation",
                           "session_closed": _close_session(client, session_id) is None})
            continue
        filed.append({"owner": p["owner"], "id": esc["id"], "number": esc.get("number"), "subject": subject,
                      "session_id": session_id, "status": esc.get("status"),
                      "assignee": esc.get("assignee_display") or person.get("label"), "due_at": esc.get("due_at")})
    data = {**base, "filed": bool(filed or already) and not errors, "escalations": filed, "already_open": already,
            "errors": errors, "assignee_party_id": person["id"]}
    if errors:
        return NodeResult(FAILED, data=data, reason=f"{len(errors)} of {len(previews)} hand-off(s) not filed")
    return data


def cleanup_escalations(reader: Any, *, table: HandoffTable, run_dir: Optional[Path] = None, tenant: str = "",
                        dry_run: bool = False) -> Dict[str, Any]:
    """Withdraw our open escalations and close their sessions. With run_dir:
    only what that run filed; without: every open escalation whose subject
    carries our prefix on this tenant."""
    client = reader.mcp
    if run_dir is not None:
        finding = (load_record(Path(run_dir) / "taskrun.json") or {}).get("finding") or {}
        targets = [{"id": e.get("id"), "session_id": e.get("session_id"), "subject": e.get("subject")}
                   for e in (finding.get("handoffs") or {}).get("escalations") or [] if e.get("id")]
    else:
        rows = client.fetch_every_page("AgentEscalation.list", {}) if hasattr(client, "fetch_every_page") else \
            _rows(client.invoke_tool("AgentEscalation.list", {"limit": 1000}))
        targets = [{"id": r.get("id"), "session_id": r.get("session_id"), "subject": r.get("subject")}
                   for r in rows if str(r.get("subject") or "").startswith(table.subject_prefix + " ")
                   and str(r.get("status") or "").lower() in OPEN_ESCALATION]
    report: Dict[str, Any] = {"tenant": tenant, "dry_run": dry_run, "withdrawn": [], "skipped": [], "errors": []}
    for t in targets:
        try:
            current = client.invoke_tool("AgentEscalation.get", {"id": t["id"]})
            status = str((current or {}).get("status") or "").lower()
            if status not in OPEN_ESCALATION:
                report["skipped"].append({**t, "status": status})
                continue
            if dry_run:
                report["withdrawn"].append({**t, "status": status, "would_withdraw": True})
                continue
            res = _result(client.invoke_tool(UPDATE, {"escalation_id": t["id"], "action": "withdraw",
                                                      "expect_status": status, "outcome": "withdrawn",
                                                      "note": "Team 21 harness clean-up: test hand-off withdrawn "
                                                              "after grading."}))
            if res.get("ok") is False:
                report["errors"].append({**t, "refused": res.get("reason_code")})
                continue
            report["withdrawn"].append({**t, "was": status, "session_close_error": _close_session(client, t["session_id"])})
        except SeatCallFailure as e:
            report["errors"].append({**t, "error": f"{e.kind}: {e}"[:200]})
    return report


# ---------------------------------------------------------------- actions

def register_handoffs(reg: Registry, table: Optional[HandoffTable] = None) -> None:
    table = table or load_handoff_table()
    reg.extras["handoff_table"] = table
    reg.extras["write_tools"] = WRITE_TOOLS
    reg.extras["cleanup"] = lambda reader, run_dir=None, tenant="", dry_run=False: cleanup_escalations(
        reader, table=table, run_dir=run_dir, tenant=tenant, dry_run=dry_run)
    reg.declare_read_only(READ_ONLY_ENDPOINTS)

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
        assignee = table.assignees.get(env.tenant)
        return [{"owner": h["owner"], **escalation_call(h, file, table, assignee)} for h in deps.get("handoffs") or []]

    @reg.action("raise_handoffs", writes=True, preview=preview,
                description="File one escalation per proposed hand-off (dry run: preview only).")
    def raise_handoffs(env: RunEnv, args: Dict[str, Any], results) -> Any:
        return file_handoffs(env.client, preview(env, args, results), table.assignees.get(env.tenant), env.tenant,
                             table)
