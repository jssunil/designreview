"""
plain_rendering(): the deterministic answer -- every sentence comes straight
from the finding. Used when the LLM is unavailable or its wording fails the
claim audit, and always kept in the run record for comparison. It is
written to satisfy the pack's own required facts (a test enforces that).
"""

from __future__ import annotations

from typing import Any, Dict, List


def _q(v: Dict[str, Any]) -> str:
    return f"v{v.get('version_number')}" + (f" (rev {v['rev']})" if v.get("rev") else "")


def plain_rendering(finding: Dict[str, Any]) -> str:
    if finding.get("outcome") == "declined":
        return finding.get("refusal") or "Cannot answer this request: the design file could not be read."

    out: List[str] = []
    f = finding.get("file") or {}
    out.append(f"Design file {f.get('number') or f.get('id')} -- {f.get('name')} (status {f.get('status')}, "
               f"current version {f.get('current_version')}).")

    delta = finding.get("revision_delta")
    if delta is not None:
        _what_changed(delta, out)
    _release(finding, out)
    if finding.get("schedule") is not None:
        _schedule(finding["schedule"], out)
    if finding.get("handoffs") is not None:
        _handoffs(finding["handoffs"], out)
    for d in finding.get("declines") or []:
        out.append(f"- Not available to this seat: {d.get('node')} -- {d.get('reason')}.")
    return "\n".join(out)


def _schedule(sched: Dict[str, Any], out: List[str]) -> None:
    out.append("")
    out.append("Schedule")
    if sched.get("outcome") != "answered":
        out.append(f"- Project milestones unavailable: {sched.get('reason') or sched.get('outcome')}.")
        return
    if not sched.get("milestones"):
        out.append("- The project has no milestones.")
        return
    late = [m for m in sched["milestones"] if m.get("past_due")]
    if late:
        out.append(f"- {len(late)} milestone(s) are past due as of {sched.get('as_of')}:")
        for m in late:
            out.append(f"  - {m.get('name')} -- due {m.get('due_date')}, still {m.get('status')}"
                       + (" (critical path)" if m.get("is_critical_path") in (1, True) else ""))
    else:
        out.append(f"- No milestone is past due as of {sched.get('as_of')}.")
    if sched.get("next_open"):
        nxt = next(m for m in sched["milestones"] if m.get("name") == sched["next_open"])
        out.append(f"- Next open milestone: {nxt.get('name')} -- due {nxt.get('due_date')} ({nxt.get('status')}).")


def _handoffs(h: Dict[str, Any], out: List[str]) -> None:
    out.append("")
    out.append("Who needs to act")
    if h.get("outcome") != "answered":
        out.append(f"- Could not check for cross-team dependencies: {h.get('reason') or h.get('outcome')}.")
        return
    proposed = h.get("proposed") or []
    if not proposed:
        out.append("- No other team needs to act: none of the open serious items is for manufacturing, quality "
                   "or purchasing -- they are design review's own work.")
    for p in proposed:
        items = "; ".join(f"[{c.get('priority')}] {c.get('title')}" for c in p.get("cites") or [])
        out.append(f"- {p['owner']}: {items}. Ask: {p.get('ask')}")
    for e in h.get("escalations") or []:
        out.append(f"- Filed {e.get('number')} for {e.get('owner')}, assigned to {e.get('assignee')}"
                   + (f", due {e.get('due_at')}" if e.get("due_at") else "") + ".")
    for e in h.get("already_open") or []:
        out.append(f"- Already open: {e.get('number')} for {e.get('owner')} ({e.get('status')}, assigned to "
                   f"{e.get('assignee')}), not filed again.")
    for e in h.get("filing_errors") or []:
        out.append(f"- Not filed for {e.get('owner')}: {e.get('refused') or e.get('error')}.")
    if proposed and not h.get("filed") and not h.get("escalations"):
        why = (h.get("filing") or {}).get("reason") if h.get("filing", {}).get("verdict") not in (None, "handed_off") \
            else "dry run"
        out.append(f"- These hand-offs are proposed only; nothing has been filed on the platform ({why}).")
    for src in h.get("sources_unavailable") or []:
        out.append(f"- Not checked for hand-offs ({src} unavailable).")


def _what_changed(delta: Dict[str, Any], out: List[str]) -> None:
    out.append("")
    out.append("What changed")
    if delta.get("outcome") == "answered":
        versions = {v["version_number"]: v for v in delta.get("versions") or []}
        req = delta.get("requested")
        frm, to = versions.get(delta.get("from_version")), versions.get(delta.get("to_version"))
        if req and frm and to:
            out.append(f"- Rev {req['from']} is {_q(frm)} and rev {req['to']} is {_q(to)}.")
        for v in delta.get("versions") or []:
            if frm and to and frm["version_number"] < v["version_number"] <= to["version_number"]:
                out.append(f"- {_q(v)} commit message: \"{v['commit_message']}\"")
            elif not (frm and to) and to and v["version_number"] == to["version_number"]:
                out.append(f"- Latest {_q(v)} commit message: \"{v['commit_message']}\"")
        out.append("- Basis: these changes come from the version commit messages; no geometry comparison was run "
                   "(this build records only file hashes between versions).")
    else:
        out.append(f"- Could not compare revisions: {delta.get('reason') or delta.get('outcome')}.")


def _release(finding: Dict[str, Any], out: List[str]) -> None:
    gate = finding.get("release_gate") or {}
    out.append("")
    out.append("Manufacturability and release")
    if gate.get("outcome") == "answered":
        verdict = "ready for release" if gate.get("ready") else "NOT ready for release"
        out.append(f"- The release gate says the design is {verdict} (evaluated on v{gate.get('version_number')}).")
        for reason in gate.get("reasons") or []:
            out.append(f"  - {reason}")
        if (gate.get("critical_open") or 0) > 0:
            out.append(f"- {gate['critical_open']} critical finding(s) remain open:")
        for b in gate.get("blockers") or []:
            out.append(f"  - [{b.get('priority')}, {b.get('status')}] {b.get('title')}")
    else:
        out.append(f"- Release readiness unavailable: {gate.get('reason') or gate.get('outcome')}.")

    dfm = finding.get("dfm_signals") or {}
    checklists = dfm.get("failed_checklists") or []
    if checklists:
        out.append(f"- {len(checklists)} failed DFM checklist result(s): "
                   + ", ".join(str(c.get("category") or c.get("id")) for c in checklists) + ".")
    elif dfm.get("failed_checklists_note"):
        out.append(f"- Failed DFM checklists: {dfm['failed_checklists_note']}.")
    fb = dfm.get("feedback") or {}
    serious = fb.get("open_high_or_critical") or []
    if serious:
        out.append(f"- {len(serious)} open high/critical feedback item(s) on this file (of {fb.get('total')} total).")

    std = dfm.get("standards") or {}
    if std.get("matches"):
        out.append("- Company design standards related to this change (keyword match, not a compliance check):")
        for m in std["matches"]:
            ref = " ".join(x for x in (m.get("standard_body"), m.get("standard_number")) if x)
            out.append(f"  - {m.get('name')} [{m.get('category')}]" + (f" ({ref})" if ref else "")
                       + ("" if m.get("is_active") in (1, True, None) else " -- inactive"))
