"""
Ground-truth probes for design review. Each reads the platform with the
HARNESS's own login (GroundTruthReader: `rest` = REST, `mcp` = a separate
MCP session), never the agent's client, and returns only the fields the
checks need.

  versions:<file_id>      REST  DesignVersion rows (number, commit message, diff keys)
  design_file:<file_id>   REST  DesignFile (404 -> recorded as error_kind "missing")
  feedback:<file_id>      REST  DesignFeedback titles/priority/status for a file
  release_gate:<file_id>  MCP   release readiness (a POST-only computed endpoint; REST can't GET it)
  milestones:<project_id> REST  DesignMilestone rows of a project (name, due date, status)
  escalations:<file_id>   MCP   our hand-off escalations for that file (subject prefix), any status
  assignees               MCP   who escalations can be assigned to (display names)
  seat_tools              MCP   this seat's tools/list names
  standards               REST  DesignStandard ids, names, body/number
"""

from __future__ import annotations

from typing import Any, Dict, List

from agentkit.registry import Registry


def _versions(reader: Any, file_id: str) -> List[Dict[str, Any]]:
    rows = reader.rest.list_all("DesignVersion", file_id=file_id)
    return sorted(({"id": r.get("id"), "version_number": r.get("version_number"),
                    "commit_message": r.get("commit_message") or "",
                    "diff_keys": sorted((r.get("diff_from_parent_json") or {}).keys()),
                    "updated_at": r.get("updated_at")}
                   for r in rows if r.get("file_id") in (None, file_id)),
                  key=lambda r: r["version_number"] or 0)


def _design_file(reader: Any, file_id: str) -> Dict[str, Any]:
    r = reader.rest.get("DesignFile", file_id)
    return {k: r.get(k) for k in ("id", "number", "name", "status", "current_version", "updated_at")}


def _release_gate(reader: Any, file_id: str) -> Dict[str, Any]:
    payload = reader.mcp.invoke_tool("endpoint.designreview.release_readiness", {"file_id": file_id})
    g = payload.get("result", payload) if isinstance(payload, dict) else {}
    return {"ready": bool(g.get("ready")), "version_number": g.get("version_number"),
            "critical_open": g.get("critical_open"), "reason_codes": list(g.get("reason_codes") or []),
            "blocker_titles": [b.get("title") for b in g.get("blockers") or [] if isinstance(b, dict)],
            "blockers": [{k: b.get(k) for k in ("id", "title", "priority", "status")}
                         for b in g.get("blockers") or [] if isinstance(b, dict)]}


def _feedback(reader: Any, file_id: str) -> List[Dict[str, Any]]:
    return [{k: r.get(k) for k in ("id", "number", "title", "priority", "status")}
            for r in reader.rest.list_all("DesignFeedback", file_id=file_id) if r.get("file_id") in (None, file_id)]


def _milestones(reader: Any, project_id: str) -> List[Dict[str, Any]]:
    return sorted(({k: r.get(k) for k in ("id", "name", "milestone_type", "status", "due_date", "completed_date",
                                          "is_critical_path", "updated_at")}
                   for r in reader.rest.list_all("DesignMilestone", project_id=project_id)
                   if r.get("project_id") in (None, project_id)),
                  key=lambda r: (r["due_date"] or "", r["name"] or ""))


def _escalations(reader: Any, file_id: str) -> List[Dict[str, Any]]:
    """Our hand-off escalations for one file (subject "<prefix> <file number>: ..."),
    any status -- before/after show exactly what a run filed."""
    from packs.designreview.handoffs import load_handoff_table

    number = reader.rest.get("DesignFile", file_id).get("number") or file_id
    prefix = f"{load_handoff_table().subject_prefix} {number}: "
    rows = reader.mcp.fetch_every_page("AgentEscalation.list", {})
    return sorted(({k: r.get(k) for k in ("id", "number", "subject", "status", "reason_code", "assignee_display",
                                          "assignee_party_id", "session_id", "sla_minutes")}
                   for r in rows if str(r.get("subject") or "").startswith(prefix)),
                  key=lambda r: (r["subject"] or "", r["number"] or ""))


def _assignees(reader: Any, _arg: str) -> List[str]:
    """Who escalations can be assigned to on this tenant (display names)."""
    payload = reader.mcp.invoke_tool("endpoint.agent_governance.escalations.assignees", {})
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    return sorted((o.get("label") or o.get("display") or "") for o in result.get("options") or [])


def _seat_tools(reader: Any, _arg: str) -> List[str]:
    return sorted(reader.mcp.seat_catalog(refresh=True))


def _standards(reader: Any, _arg: str) -> List[Dict[str, Any]]:
    return [{k: r.get(k) for k in ("id", "name", "standard_body", "standard_number")}
            for r in reader.rest.list_all("DesignStandard")]


def register_probes(reg: Registry) -> None:
    reg.probes.update({"versions": _versions, "design_file": _design_file, "release_gate": _release_gate,
                       "feedback": _feedback, "milestones": _milestones, "escalations": _escalations, "assignees": _assignees,
                       "seat_tools": _seat_tools, "standards": _standards})
