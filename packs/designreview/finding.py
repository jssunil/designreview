"""
build_finding(): the gradable answer, computed in code from node results.

The LLM never decides a fact. Everything a verifier grades lives here:

  outcome          answered | partial | declined
  file             id / number / name / status / current_version
  revision_delta   which version is rev X / rev Y (from commit messages),
                   each version's commit note + the quantities it states,
                   basis = "commit_message", geometry_compared = False
  release_gate     ready, reason codes/reasons, critical_open, blockers
  dfm_signals      failed checklist results + open high/critical feedback +
                   DesignStandard records recalled for the change (keyword match)
  declines         every node the platform refused, with its reason

Why commit messages: in this build diff_from_parent_json only carries file
hashes and no CAD kernel exists (PLAN.md §8), so the revision delta can
only come from the engineers' own version notes -- and the finding says so.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional

from agentkit.graph import DECLINED, FAILED, RESOLVED, NodeResult

FINDING_SCHEMA = "designreview-finding-v1"
REV_RE = re.compile(r"\brev(?:ision)?\.?\s+([A-Z]|\d{1,2})\b", re.I)  # "rev B", "Rev 2"
QUANTITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|°|deg|degrees?|kg|g|%)(?![A-Za-z])", re.I)
CLOSED_FEEDBACK = {"resolved", "closed", "rejected", "wont_fix", "won't_fix", "cancelled", "duplicate"}
SERIOUS = {"critical", "high"}
FILE_KEYS = ("id", "number", "name", "status", "current_version", "released_version_id", "has_brep",
             "conversion_status")


def rev_letter(commit_message: str) -> Optional[str]:
    m = REV_RE.search(commit_message or "")
    return m.group(1).upper() if m else None


def requested_revs(prompt: str) -> List[str]:
    """Rev letters named in the question, in order, de-duplicated."""
    seen: List[str] = []
    for letter in REV_RE.findall(prompt or ""):
        if letter.upper() not in seen:
            seen.append(letter.upper())
    return seen


def quantities(text: str) -> List[Dict[str, Any]]:
    return [{"value": float(v), "unit": u.lower(), "text": f"{v} {u}"} for v, u in QUANTITY_RE.findall(text or "")]


def _data(results: Dict[str, NodeResult], node: str) -> Optional[Any]:
    r = results.get(node)
    return r.data if r is not None and r.verdict == RESOLVED else None


def _section_state(results: Dict[str, NodeResult], node: str) -> Dict[str, Any]:
    r = results.get(node)
    if r is None:
        return {"outcome": "not_run"}
    if r.verdict == RESOLVED:
        return {"outcome": "answered"}
    out = {"outcome": "declined" if r.verdict == DECLINED else ("error" if r.verdict == FAILED else r.verdict),
           "reason": r.reason}
    if r.error_kind:
        out["error_kind"] = r.error_kind
    return out


def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [r for r in payload["data"] if isinstance(r, dict)]
    return []


def _revision_delta(results: Dict[str, NodeResult], prompt: str) -> Dict[str, Any]:
    section = _section_state(results, "revisions")
    if section["outcome"] != "answered":
        return section
    versions = []
    for row in sorted(_rows(_data(results, "revisions")), key=lambda r: r.get("version_number") or 0):
        note = row.get("commit_message") or ""
        versions.append({"version_number": row.get("version_number"), "id": row.get("id"),
                         "rev": rev_letter(note), "commit_message": note, "quantities": quantities(note)})
    section.update(basis="commit_message", geometry_compared=False,
                   basis_note="diff_from_parent_json only records file hashes in this build; no geometry "
                              "comparison was run. Changes come from the version commit messages.",
                   versions=versions)
    if not versions:
        section.update(outcome="declined", reason="this file has no versions")
        return section

    wanted = requested_revs(prompt)
    if len(wanted) >= 2:
        by_rev = {v["rev"]: v for v in versions if v["rev"]}
        missing = [r for r in wanted[:2] if r not in by_rev]
        section["requested"] = {"from": wanted[0], "to": wanted[1]}
        if missing:
            section.update(outcome="declined",
                           reason=f"rev {', '.join(missing)} not found in this file's version history "
                                  f"(revs present: {', '.join(sorted(by_rev)) or 'none'})")
            return section
        frm, to = by_rev[wanted[0]], by_rev[wanted[1]]
        section.update(from_version=frm["version_number"], to_version=to["version_number"],
                       change_notes=[v["commit_message"] for v in versions
                                     if frm["version_number"] < v["version_number"] <= to["version_number"]])
    else:
        latest = versions[-1]
        section.update(to_version=latest["version_number"],
                       from_version=versions[-2]["version_number"] if len(versions) > 1 else None,
                       change_notes=[latest["commit_message"]])
    return section


def _release_gate(results: Dict[str, NodeResult]) -> Dict[str, Any]:
    section = _section_state(results, "release_gate")
    if section["outcome"] != "answered":
        return section
    payload = _data(results, "release_gate") or {}
    gate = payload.get("result", payload) if isinstance(payload, dict) else {}
    section.update(
        ready=bool(gate.get("ready")), version_number=gate.get("version_number"),
        reason_codes=list(gate.get("reason_codes") or []), reasons=list(gate.get("reasons") or []),
        critical_open=gate.get("critical_open"),
        blockers=[{k: b.get(k) for k in ("id", "title", "priority", "status")}
                  for b in gate.get("blockers") or [] if isinstance(b, dict)],
        blockers_truncated=bool(gate.get("blockers_truncated")))
    return section


def _dfm_signals(results: Dict[str, NodeResult]) -> Dict[str, Any]:
    feedback = _section_state(results, "feedback")
    section: Dict[str, Any] = {"outcome": feedback["outcome"]}
    if feedback["outcome"] == "answered":
        payload = _data(results, "feedback")
        rows = _rows(payload)
        open_serious = [{k: r.get(k) for k in ("id", "number", "title", "priority", "status", "ai_generated")}
                        for r in rows if str(r.get("status") or "").lower() not in CLOSED_FEEDBACK
                        and str(r.get("priority") or "").lower() in SERIOUS]
        section["feedback"] = {"total": payload.get("total", len(rows)) if isinstance(payload, dict) else len(rows),
                               "open_high_or_critical": open_serious}
    else:
        section["feedback"] = feedback
    checklists = _section_state(results, "failed_checklists")
    if checklists["outcome"] == "answered":
        section["failed_checklists"] = [
            {k: r.get(k) for k in ("id", "number", "category", "overall_result", "checklist_id")}
            for r in _rows(_data(results, "failed_checklists"))]
    elif checklists["outcome"] == "not_run":
        section["failed_checklists"] = []
        section["failed_checklists_note"] = "not read: the release gate reported no blockers"
    else:
        section["failed_checklists"] = []
        section["failed_checklists_note"] = f"not available: {checklists.get('reason')}"
    standards = _section_state(results, "dfm_standards")
    if standards["outcome"] == "answered":
        data = _data(results, "dfm_standards") or {}
        section["standards"] = {
            "query": data.get("query"), "indexed_records": data.get("indexed_records"),
            "matches": [{k: m.get(k) for k in ("id", "name", "category", "standard_body", "standard_number",
                                                "is_active", "score", "matched_terms")}
                        for m in data.get("matches") or []],
            "note": "matched by keyword against DesignStandard names/descriptions; relevance, not compliance"}
    elif standards["outcome"] != "not_run":
        section["standards"] = standards
    return section


def build_finding(results: Dict[str, NodeResult], params: Dict[str, Any], *, run_id: str = "",
                  dry_run: bool = True) -> Dict[str, Any]:
    prompt = params.get("prompt", "")
    declines = [{"node": node, "reason": r.reason, "error_kind": r.error_kind}
                for node, r in results.items() if r.verdict == DECLINED]
    finding: Dict[str, Any] = {
        "schema": FINDING_SCHEMA, "run_id": run_id, "dry_run": dry_run,
        "snapshot_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "question": prompt, "file_id": params.get("file_id"), "declines": declines,
    }
    file_state = _section_state(results, "design_file")
    if file_state["outcome"] != "answered":
        # The first refusal explains it (e.g. a needed tool is not in this seat);
        # otherwise the design file itself could not be read.
        why = next((d["reason"] for d in declines if d["node"] != "design_file"), None)
        finding.update(outcome="declined", file=None,
                       refusal=f"Cannot answer: {why}" if why else
                       f"Cannot answer: design file {params.get('file_id')} -- {file_state.get('reason')}")
        return finding

    raw = _data(results, "design_file") or {}
    finding["file"] = {k: raw.get(k) for k in FILE_KEYS}
    finding["revision_delta"] = _revision_delta(results, prompt)
    finding["release_gate"] = _release_gate(results)
    finding["dfm_signals"] = _dfm_signals(results)
    sections = [finding["revision_delta"], finding["release_gate"], finding["dfm_signals"]]
    finding["outcome"] = "answered" if all(s["outcome"] == "answered" for s in sections) else "partial"
    return finding
