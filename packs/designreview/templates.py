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

    delta = finding.get("revision_delta") or {}
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

    for d in finding.get("declines") or []:
        out.append(f"- Not available to this seat: {d.get('node')} -- {d.get('reason')}.")
    return "\n".join(out)
