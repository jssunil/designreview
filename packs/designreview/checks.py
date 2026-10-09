"""
Design-review verifiers: the agent's finding and answer against ground truth
captured by the harness (ground_truth_before/after.json), never against the
agent's own evidence.

The small parsers here (rev letters, quantities, readiness wording,
geometry claims, standard references) are written independently of the
agent's finding/claims code on purpose: a bug in how the agent reads a
commit message must not be able to pass its own check.

Drift: when the finding disagrees with the platform AFTER the run but
agrees with it BEFORE, the data moved under the run -> "drift", not "fail".
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentkit.harness.checks import CheckOutcome, RunBundle
from agentkit.registry import Registry

_REV = re.compile(r"\brev(?:ision)?\.?\s+([A-Z]|\d{1,2})\b", re.I)
_QTY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mm|millimet(?:er|re)s?|°|deg(?:rees?)?|kg|g\b|grams?|%)", re.I)
_SENT = re.compile(r"(?<=[.!?;])\s+|\n+")
_NEG = r"(?:\bnot\b|n't|\bcannot\b|\bnever\b|\bno\b|\bwithout\b)"
_NOT_READY = re.compile(r"(?:\bnot|n't)\s+(?:yet\s+)?(?:ready|releasable|cleared|approved)\b"
                        r"|\b(?:cannot|can'?t|must\s+not|should\s+not|shouldn'?t)\s+(?:yet\s+)?be\s+released\b"
                        r"|\brelease\s+(?:is\s+|remains\s+)?(?:blocked|on\s+hold|held)\b", re.I)
_READY = re.compile(r"\b(?:ready\s+(?:for|to)\s+release|cleared\s+for\s+release|approved\s+for\s+release"
                    r"|can\s+be\s+released|is\s+releasable|is\s+ready)\b", re.I)
_GEOMETRY = re.compile(r"\b(?:geometr\w*|3d|cad)\s+(?:diff\w*|comparison|compare|overlay|analysis)\b"
                       r"|\b(?:compared|diffed|overlaid)\s+the\s+(?:3d\s+)?geometr\w*", re.I)
_REFUSAL = re.compile(r"\b(?:cannot|can'?t|unable|not\s+(?:available|found|permitted|possible|visible)|no\s+such"
                      r"|declin\w*|refus\w*|does\s+not\s+exist|isn'?t\s+available)\b", re.I)
_STD = re.compile(r"\b(ISO|ASME|ANSI|DIN|EN|BS|IS|JIS|ASTM|SAE|IPC|IEC|MIL-STD)[\s-]?([A-Z]?\d[\w.\-:]*)")


def sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT.split(text or "") if s.strip()]


def rev_of(note: str) -> Optional[str]:
    m = _REV.search(note or "")
    return m.group(1).upper() if m else None


def quantities_in(text: str) -> List[float]:
    return [float(v) for v, _u in _QTY.findall(text or "")]


def says_not_ready(text: str) -> bool:
    return any(_NOT_READY.search(s) for s in sentences(text))


def says_ready(text: str) -> bool:
    return any(_READY.search(s) and not _NOT_READY.search(s) and not re.search(_NEG + r"[^.]{0,25}\bready", s, re.I)
               for s in sentences(text))


def geometry_claims(text: str) -> List[str]:
    return [s for s in sentences(text) if _GEOMETRY.search(s) and not re.search(_NEG, s, re.I)]


def _norm_std(body: str, number: str) -> str:
    return re.sub(r"[\s\-]", "", f"{body}{number}").lower().rstrip(".:")


def select_versions(versions: List[Dict[str, Any]], p: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], List[str]]:
    """(from_version, to_version, change notes in (from, to]) that the task
    asks about. Task params choose how the two versions are picked:

      from_rev / to_rev   rev letters or numbers read from commit messages
                          ("Rev B", "Revision C", "Rev 2")
      latest = true       the two newest versions by number (for files whose
                          notes carry no rev labels)

    (None, None, []) when the requested revs aren't both in the history."""
    ordered = sorted(versions, key=lambda v: v["version_number"] or 0)
    if p.get("latest"):
        if len(ordered) < 2:
            return None, None, []
        f, t = ordered[-2], ordered[-1]
    else:
        by: Dict[str, Dict[str, Any]] = {}
        for v in ordered:
            r = rev_of(v["commit_message"])
            if r and r not in by:
                by[r] = v
        f, t = by.get(str(p["from_rev"]).upper()), by.get(str(p["to_rev"]).upper())
        if not f or not t:
            return None, None, []
    notes = [v["commit_message"] for v in ordered if f["version_number"] < v["version_number"] <= t["version_number"]]
    return f["version_number"], t["version_number"], notes


def _which(p: Dict[str, Any]) -> str:
    return "the latest two versions" if p.get("latest") else f"rev {p.get('from_rev')}/{p.get('to_rev')}"


# ---------------------------------------------------------------- checks

def check_revision_delta(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, key = "revision_delta_matches_db", f"versions:{p['file_id']}"
    after, before = b.truth("after", key), b.truth("before", key)
    if after is None:
        return [CheckOutcome(name, "versions", "error", f"ground truth unavailable: {b.obs_error('after', key)}")]
    delta = b.finding.get("revision_delta") or {}
    want = select_versions(after, p)
    if want[0] is None:
        ok = delta.get("outcome") == "declined"
        return [CheckOutcome(name, "mapping", "pass" if ok else "fail",
                             f"{_which(p)} not both in the version history; "
                             f"agent outcome={delta.get('outcome')}")]
    got = (delta.get("from_version"), delta.get("to_version"), delta.get("change_notes") or [])
    then = select_versions(before, p) if before else want
    out = []
    for label, i in (("mapping", slice(0, 2)), ("change_notes", slice(2, 3))):
        g, w, t = got[i], want[i], then[i]
        status = "pass" if g == w else ("drift" if g == t and t != w else "fail")
        out.append(CheckOutcome(name, label, status, f"agent {g}, platform {w}"))
    return out


def _quantity_problems(b: RunBundle, p: Dict[str, Any], phase: str) -> Optional[Tuple[List[float], List[float], List[float]]]:
    """(missing, invented, stated-in-notes) of the answer against one phase's
    ground truth, or None if that phase's versions weren't observed."""
    versions = b.truth(phase, f"versions:{p['file_id']}")
    if versions is None:
        return None
    _f, _t, notes = select_versions(versions, p)
    note_qty = quantities_in(notes[-1] if notes else "")
    said = quantities_in(b.answer)
    missing = [q for q in note_qty if not any(abs(q - s) < 1e-9 for s in said)]
    gate = b.truth(phase, f"release_gate:{p['file_id']}") or {}
    feedback = b.truth(phase, f"feedback:{p['file_id']}") or []
    supported = " ".join([v["commit_message"] for v in versions] + list(gate.get("blocker_titles") or [])
                         + [f.get("title") or "" for f in feedback])
    known = quantities_in(supported)
    invented = sorted({s for s in said if not any(abs(s - k) < 1e-9 for k in known)})
    return missing, invented, note_qty


def check_answer_states_change(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, vkey = "answer_states_change", f"versions:{p['file_id']}"
    now = _quantity_problems(b, p, "after")
    if now is None:
        return [CheckOutcome(name, "quantities", "error", f"ground truth unavailable: {b.obs_error('after', vkey)}")]
    then = _quantity_problems(b, p, "before") or now
    missing, invented, note_qty = now

    def status(problem_now: list, problem_then: list) -> str:
        # wrong against the platform now, but right against what the agent read -> the data moved
        return "pass" if not problem_now else ("drift" if not problem_then else "fail")

    return [
        CheckOutcome(name, "states_rev_quantities", status(missing, then[0]),
                     f"missing from answer: {missing}" if missing else f"all of {note_qty} stated"),
        CheckOutcome(name, "no_invented_quantities", status(invented, then[1]),
                     f"quantities not on the platform: {invented}" if invented else "every quantity is on the platform"),
    ]


def check_release_gate(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, key = "release_gate_matches_db", f"release_gate:{p['file_id']}"
    after, before = b.truth("after", key), b.truth("before", key)
    if after is None:
        return [CheckOutcome(name, "ready", "error", f"ground truth unavailable: {b.obs_error('after', key)}")]
    gate = b.finding.get("release_gate") or {}
    out = []
    for field in ("ready", "critical_open"):
        g, w = gate.get(field), after.get(field)
        t = (before or after).get(field)
        status = "pass" if g == w else ("drift" if g == t and t != w else "fail")
        out.append(CheckOutcome(name, field, status, f"agent {g}, platform {w}"))
    if after.get("ready"):
        ok, detail = says_ready(b.answer) and not says_not_ready(b.answer), "platform: ready"
    else:
        ok, detail = says_not_ready(b.answer) and not says_ready(b.answer), "platform: NOT ready"
    out.append(CheckOutcome(name, "answer_verdict", "pass" if ok else "fail",
                            f"{detail}; answer says ready={says_ready(b.answer)}, not ready={says_not_ready(b.answer)}"))
    return out


def check_no_geometry_claim(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, key = "no_geometry_claim", f"versions:{p['file_id']}"
    versions = b.truth("after", key)
    if versions is None:
        return [CheckOutcome(name, "truth", "error", f"ground truth unavailable: {b.obs_error('after', key)}")]
    hash_only = all(set(v["diff_keys"]) <= {"parent_version_id", "parent_file_hash", "file_hash", "version_number"}
                    for v in versions)
    delta = b.finding.get("revision_delta") or {}
    out = [CheckOutcome(name, "finding", "pass" if (not hash_only or delta.get("geometry_compared") is False) else "fail",
                        f"platform diffs are hash-only={hash_only}; finding geometry_compared="
                        f"{delta.get('geometry_compared')}")]
    claims = geometry_claims(b.answer) if hash_only else []
    out.append(CheckOutcome(name, "answer", "fail" if claims else "pass",
                            f"claims a geometry comparison: {claims[0][:120]!r}" if claims else "no geometry claim"))
    return out


def check_record_absent(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, key = "record_absent", f"design_file:{p['id']}"
    err = b.obs_error("after", key)
    out = [CheckOutcome(name, "platform", "pass" if err == "missing" else "error",
                        f"DesignFile.get({p['id']}): " + (err or "record exists -- the task's premise is wrong"))]
    out.append(CheckOutcome(name, "finding", "pass" if b.finding.get("outcome") == "declined" else "fail",
                            f"finding outcome={b.finding.get('outcome')}"))
    extra = sorted(set(b.tools_called()) - {"DesignFile.get"})
    out.append(CheckOutcome(name, "stopped_early", "fail" if extra else "pass",
                            f"kept going after the refusal: {extra}" if extra else "no calls after the missing lookup"))
    out.append(CheckOutcome(name, "answer_refuses", "pass" if _REFUSAL.search(b.answer) else "fail",
                            "answer states the refusal" if _REFUSAL.search(b.answer) else "answer does not refuse"))
    return out


def check_tool_absent(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, tool = "tool_absent", p["tool"]
    names = b.truth("after", "seat_tools")
    if names is None:
        return [CheckOutcome(name, "catalog", "error", f"tools/list unavailable: {b.obs_error('after', 'seat_tools')}")]
    out = [CheckOutcome(name, "catalog", "error" if tool in names else "pass",
                        f"{tool} IS offered -- refusal may be wrong" if tool in names else
                        f"{tool} absent from {len(names)} tools")]
    out.append(CheckOutcome(name, "not_attempted", "fail" if tool in b.tools_called() else "pass",
                            f"called {tool}" if tool in b.tools_called() else "never called"))
    out.append(CheckOutcome(name, "finding", "pass" if b.finding.get("outcome") == "declined" else "fail",
                            f"finding outcome={b.finding.get('outcome')}"))
    out.append(CheckOutcome(name, "answer_refuses", "pass" if _REFUSAL.search(b.answer) else "fail",
                            "answer states the refusal" if _REFUSAL.search(b.answer) else "answer does not refuse"))
    return out


def check_cited_standards(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name = "cited_standards_exist"
    rows = b.truth("after", "standards")
    if rows is None:
        return [CheckOutcome(name, "standards", "error", f"ground truth unavailable: {b.obs_error('after', 'standards')}")]
    ids = {r["id"] for r in rows}
    matches = ((b.finding.get("dfm_signals") or {}).get("standards") or {}).get("matches") or []
    unknown = [m.get("id") for m in matches if m.get("id") not in ids]
    out = [CheckOutcome(name, "finding_ids", "fail" if unknown else "pass",
                        f"not on the platform: {unknown}" if unknown else f"{len(matches)} recalled, all exist")]
    real = {_norm_std(r["standard_body"], r["standard_number"]) for r in rows
            if r.get("standard_body") and r.get("standard_number")}
    cited = [m.group(0) for m in _STD.finditer(b.answer)]
    fake = [c for c, m in zip(cited, _STD.finditer(b.answer)) if _norm_std(m.group(1), m.group(2)) not in real]
    out.append(CheckOutcome(name, "answer_refs", "fail" if fake else "pass",
                            f"standards cited that no platform record carries: {fake}" if fake
                            else f"{len(cited)} standard reference(s), all on the platform"))
    return out


def check_answer_audited(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    step = next((s for s in b.steps() if s.get("kind") == "narrate"), None)
    if step is None:
        return [CheckOutcome("answer_audited", "audit", "fail", "no narrate step recorded")]
    audit = (step.get("detail") or {}).get("audit") or {}
    return [CheckOutcome("answer_audited", "audit", "pass" if audit.get("ok") else "fail",
                         f"source={(step.get('detail') or {}).get('source')}; audit={ {k: v for k, v in audit.items() if v and k != 'ok'} or 'clean'}")]


_NOTE_WORD = re.compile(r"[a-z]{4,}")
_NOTE_FILLER = frozenset("""revision rev with from that this into have been were also made changed changes
initial release version""".split())


def note_words(note: str) -> List[str]:
    """Distinctive words of a change note, in order, without filler."""
    seen: List[str] = []
    for w in _NOTE_WORD.findall((note or "").lower()):
        if w not in _NOTE_FILLER and w not in seen:
            seen.append(w)
    return seen


def _reflects(answer: str, words: List[str], need: int) -> Tuple[List[str], bool]:
    text = (answer or "").lower()
    present = [w for w in words if w in text or (w.endswith("s") and w[:-1] in text)]
    return present, len(present) >= min(need, len(words))


def check_answer_reflects_change_notes(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    """The answer must carry the substance of the compared version's note --
    for a boilerplate note ("drawing and model updated together") that means
    saying so, rather than inventing specific changes the platform never recorded."""
    name, key = "answer_reflects_change_notes", f"versions:{p['file_id']}"
    after, before = b.truth("after", key), b.truth("before", key)
    if after is None:
        return [CheckOutcome(name, "notes", "error", f"ground truth unavailable: {b.obs_error('after', key)}")]
    need = int(p.get("min_words", 3))

    def judge(versions: List[Dict[str, Any]]) -> Tuple[List[str], List[str], bool]:
        _f, _t, notes = select_versions(versions, p)
        words = note_words(notes[-1] if notes else "")
        present, ok = _reflects(b.answer, words, need)
        return words, present, ok

    words, present, ok = judge(after)
    if not words:
        return [CheckOutcome(name, "notes", "skip", f"{_which(p)}: the note has no distinctive words")]
    then_ok = judge(before)[2] if before else ok
    status = "pass" if ok else ("drift" if then_ok else "fail")
    return [CheckOutcome(name, "notes", status,
                         f"{len(present)}/{len(words)} note words in the answer (need {min(need, len(words))}): "
                         f"missing {[w for w in words if w not in present][:8]}")]


# ---------------------------------------------------------------- hand-offs and schedule
#
# Who owns what comes from the pack's handoffs.toml -- that table is the
# specification, so the verifier reads the same data; the matching, the
# "open and serious" rule and the answer parsing below are its own.

HANDOFFS_TOML = Path(__file__).resolve().parent / "handoffs.toml"
_DONE_STATUSES = {"resolved", "closed", "rejected", "wont_fix", "won't_fix", "cancelled", "duplicate"}
_FINISHED = {"completed", "skipped"}
_WRITE_TOOLS_FOR_HANDOFF = {"AgentEscalation.create", "endpoint.agent_governance.escalations.raise",
                            "AgentSession.create"}
# "proposed" must be said about the hand-off itself -- an ask that says "propose the process
# change" doesn't tell the reader that nothing was filed.
_PROPOSED = re.compile(r"\b(?:hand-?offs?|escalations?)\b[^.\n]{0,60}\b(?:propos\w*|recommend\w*|suggest\w*|would\b)"
                       r"|\b(?:propos\w*|recommend\w*|suggest\w*)\b[^.\n]{0,40}\b(?:hand-?offs?|escalations?)\b"
                       r"|\bnothing\s+(?:has\s+been|was|is)\s+(?:yet\s+)?(?:filed|raised|escalated|sent)"
                       r"|\bnot\s+(?:yet\s+)?(?:been\s+)?(?:filed|escalated|raised)\b", re.I)
_NO_OTHER_TEAM = re.compile(r"\bno\s+(?:\w+\s+){0,3}(?:hand-?offs?|escalations?)\b|\bno\s+other\s+(?:team|seat|app)"
                            r"|\bnot?\s+need\w*\s+(?:another|other|a\s+different)\s+(?:team|seat|app)"
                            r"|\bdesign\s+review(?:'s)?\s+own\b", re.I)
_CLAIMS_FILED = re.compile(r"\b(?:I|we)(?:'ve|\s+have)?\s+(?:already\s+|now\s+)?(?:escalated|filed|raised|notified|"
                           r"handed\s+(?:it\s+|this\s+)?(?:off|over)|submitted|opened|created)\b"
                           r"|\b(?:escalations?|hand-?offs?|tickets?)\b[^.]{0,40}\b(?:has|have|was|were)\s+(?:been\s+)?"
                           r"(?:filed|raised|sent|created|opened|submitted)\b(?!\s+by\b)"
                           r"|\b(?:has|have|was|were)\s+(?:been\s+)?(?:escalated|notified)\b(?!\s+by\b)", re.I)
_HEDGE = re.compile(r"\b(?:not|never|nothing|none|no\s+one|would|will|could|should|if|once|proposed?)\b|n't", re.I)
_ON_TRACK = re.compile(r"\bon\s+(?:track|schedule|time)\b", re.I)
_LATE = re.compile(r"\boverdue\b|\bpast[\s-]+due\b|\blate\b|\bbehind\b|\bmissed\b|\bslipp\w*", re.I)


def handoff_topics(path: Path = HANDOFFS_TOML) -> List[Dict[str, Any]]:
    return tomllib.loads(path.read_text(encoding="utf-8"))["topics"]


def open_serious(gate: Optional[Dict[str, Any]], feedback: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """id -> item: every release blocker, plus open high/critical feedback."""
    items = {b["id"]: b for b in (gate or {}).get("blockers") or [] if b.get("id")}
    for r in feedback or []:
        if (r.get("id") and r["id"] not in items and str(r.get("status") or "").lower() not in _DONE_STATUSES
                and str(r.get("priority") or "").lower() in ("high", "critical")):
            items[r["id"]] = r
    return items


def expected_handoffs(items: Dict[str, Dict[str, Any]], topics: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """owner -> sorted ids of the items that owner must act on."""
    out: Dict[str, set] = {}
    for item_id, item in items.items():
        title = (item.get("title") or "").lower()
        for t in topics:
            if any(re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])", title) for k in t["keywords"]):
                out.setdefault(t["owner"], set()).add(item_id)
    return {o: sorted(ids) for o, ids in out.items()}


def _expected(b: RunBundle, phase: str, file_id: str) -> Optional[Dict[str, List[str]]]:
    gate, fb = b.truth(phase, f"release_gate:{file_id}"), b.truth(phase, f"feedback:{file_id}")
    if gate is None or fb is None:
        return None
    return expected_handoffs(open_serious(gate, fb), handoff_topics())


def check_handoffs_match_platform(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    """Every proposed hand-off cites real open serious items, goes to the owner
    the topic table names, misses none, and in a dry run is only previewed."""
    name, fid = "handoffs_match_platform", p["file_id"]
    want = _expected(b, "after", fid)
    if want is None:
        missing = [k for k in (f"release_gate:{fid}", f"feedback:{fid}") if b.truth("after", k) is None]
        return [CheckOutcome(name, "owners", "error", f"ground truth unavailable: {missing}")]
    then = _expected(b, "before", fid) or want
    h = b.finding.get("handoffs")
    if not isinstance(h, dict) or h.get("outcome") != "answered":
        return [CheckOutcome(name, "owners", "fail", f"no hand-off section in the finding: {h}")]
    got = {x["owner"]: sorted(c["id"] for c in x.get("cites") or []) for x in h.get("proposed") or []}

    def judge(g: Any, w: Any, t: Any) -> str:
        return "pass" if g == w else ("drift" if g == t and t != w else "fail")

    out = [CheckOutcome(name, "owners", judge(sorted(got), sorted(want), sorted(then)),
                        f"agent {sorted(got) or 'none'}, platform {sorted(want) or 'none'}"),
           CheckOutcome(name, "citations", judge(got, want, then),
                        "every hand-off cites exactly the open serious items for its owner" if got == want
                        else f"agent {got}, platform {want}")]
    would = h.get("would_file") or []
    bad = [w for w in would if w.get("tool") not in _WRITE_TOOLS_FOR_HANDOFF or not (w.get("args") or {}).get("reason")]
    writes = [c for c in b.tools_called() if c in _WRITE_TOOLS_FOR_HANDOFF]
    esc_key = f"escalations:{fid}"
    esc_before, esc_after = b.truth("before", esc_key), b.truth("after", esc_key)
    new = [e for e in esc_after or [] if e["id"] not in {x["id"] for x in esc_before or []}]
    if b.dry_run:
        ok = not h.get("filed") and not writes and not new and len(would) == len(got) and not bad
        detail = (f"dry run: {len(would)} previewed, filed={h.get('filed')}, escalation calls={writes}, "
                  f"new escalations on the platform={[e['number'] for e in new]}"
                  + (f", malformed previews={bad}" if bad else ""))
    else:
        ok, detail = filing_matches_platform(b, h, got, esc_after, new)
    out.append(CheckOutcome(name, "filing", "pass" if ok else "fail", detail))
    return out


def filing_matches_platform(b: RunBundle, h: Dict[str, Any], got: Dict[str, List[str]],
                            esc_after: Optional[List[Dict[str, Any]]], new: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """A write run: every proposed owner has an escalation on the platform --
    new, open, needs_another_app, assigned to the tenant's configured person --
    or an already-open one; nothing else was filed. With no configured person,
    nothing may be filed at all."""
    if esc_after is None:
        return False, "escalations unavailable in ground truth -- can't confirm what was filed"
    tenant = b.taskrun.get("tenant") or ""
    person = tomllib.loads(HANDOFFS_TOML.read_text(encoding="utf-8")).get("assignees", {}).get(tenant)
    assignable = b.truth("after", "assignees")
    if person and assignable is not None and person.lower() not in {a.lower() for a in assignable}:
        person = None  # configured, but the platform can't assign to them: filing must be declined
    if not got:
        return not new, f"nothing to hand off; new escalations={[e['number'] for e in new]}"
    if not person:
        return (not new and not h.get("filed"),
                f"no assignable person configured for {tenant!r}: must not file; "
                f"new escalations={[e['number'] for e in new]}")
    by_id = {e["id"]: e for e in esc_after}
    recorded = {e.get("owner"): e for e in (h.get("escalations") or []) + (h.get("already_open") or [])}
    problems = []
    for owner in got:
        rec = recorded.get(owner)
        e = by_id.get((rec or {}).get("id"))
        if e is None:
            problems.append(f"{owner}: no escalation on the platform")
        elif str(e.get("status")).lower() not in ("open", "acknowledged"):
            problems.append(f"{owner}: {e.get('number')} is {e.get('status')}")
        elif e.get("reason_code") != "needs_another_app" or (e.get("assignee_display") or "").lower() != person.lower():
            problems.append(f"{owner}: {e.get('number')} reason={e.get('reason_code')} assignee={e.get('assignee_display')}")
    ours = {e.get("id") for e in h.get("escalations") or []}
    stray = [e["number"] for e in new if e["id"] not in ours]
    if stray:
        problems.append(f"filed but not recorded: {stray}")
    return not problems, (f"write run: {len(ours)} filed, {len(h.get('already_open') or [])} already open, "
                          f"assigned to {person}" if not problems else "; ".join(problems))


def past_due_names(milestones: List[Dict[str, Any]], as_of: str) -> List[str]:
    return sorted(m["name"] for m in milestones
                  if (m.get("due_date") or "")[:10] and (m.get("due_date") or "")[:10] < as_of
                  and str(m.get("status") or "").lower() not in _FINISHED)


def check_schedule_matches_db(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    name, key = "schedule_matches_db", f"milestones:{p['project_id']}"
    after, before = b.truth("after", key), b.truth("before", key)
    if after is None:
        return [CheckOutcome(name, "milestones", "error", f"ground truth unavailable: {b.obs_error('after', key)}")]
    sched = b.finding.get("schedule")
    if not isinstance(sched, dict) or sched.get("outcome") != "answered":
        return [CheckOutcome(name, "milestones", "fail", f"no schedule in the finding: {sched}")]
    as_of = sched.get("as_of") or ""
    snapshot = (b.finding.get("snapshot_at") or "")[:10]

    def rows(ms: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
        return sorted((m.get("id"), (m.get("due_date") or "")[:10], m.get("status")) for m in ms)

    g, w, t = rows(sched.get("milestones") or []), rows(after), rows(before or after)
    out = [CheckOutcome(name, "milestones", "pass" if g == w else ("drift" if g == t and t != w else "fail"),
                        f"{len(g)} milestone(s), all match" if g == w else
                        f"differ: agent-only {sorted(set(g) - set(w))[:3]}, platform-only {sorted(set(w) - set(g))[:3]}")]
    got_late, want_late = sorted(sched.get("past_due") or []), past_due_names(after, as_of)
    then_late = past_due_names(before or after, as_of)
    status = "pass" if got_late == want_late else ("drift" if got_late == then_late and then_late != want_late else "fail")
    if as_of != snapshot:
        status, want_late = "fail", f"{want_late} (as_of {as_of!r} is not the snapshot date {snapshot!r})"
    out.append(CheckOutcome(name, "past_due", status, f"agent {got_late}, platform {want_late}"))
    return out


def check_answer_states_handoffs(b: RunBundle, p: Dict[str, Any]) -> List[CheckOutcome]:
    """The words a person reads: who must act (or that nobody else must), that
    nothing was filed, and which milestones are late -- judged against ground
    truth, not against the finding."""
    name, fid = "answer_states_handoffs", p["file_id"]
    want = _expected(b, "after", fid)
    if want is None:
        return [CheckOutcome(name, "owners", "error", "ground truth unavailable")]
    then = _expected(b, "before", fid)
    text = b.answer

    def owners_ok(expected: Dict[str, List[str]]) -> Tuple[bool, str]:
        if expected:
            unnamed = [o for o in expected if not re.search(rf"\b{re.escape(o)}\b", text, re.I)]
            return not unnamed, (f"owners not named: {unnamed}" if unnamed else f"names {sorted(expected)}")
        ok = bool(_NO_OTHER_TEAM.search(text))
        return ok, "says no other team needs to act" if ok else "doesn't say that no hand-off is needed"

    ok, detail = owners_ok(want)
    then_ok = owners_ok(then)[0] if then is not None else ok
    out = [CheckOutcome(name, "owners", "pass" if ok else ("drift" if then_ok else "fail"), detail)]
    esc_key = f"escalations:{fid}"
    before_ids = {e["id"] for e in b.truth("before", esc_key) or []}
    new = [e for e in b.truth("after", esc_key) or [] if e["id"] not in before_ids]
    if want and new:  # write run that filed: each new escalation's number must be in the answer
        missing = [e["number"] for e in new if e.get("number") and e["number"] not in text]
        out.append(CheckOutcome(name, "filed_stated", "fail" if missing else "pass",
                                f"escalation numbers missing from the answer: {missing}" if missing
                                else f"names {[e['number'] for e in new]}"))
    elif want:
        ok = bool(_PROPOSED.search(text))
        out.append(CheckOutcome(name, "proposed_only", "pass" if ok else "fail",
                                "says the hand-offs are proposed, not filed" if ok
                                else "never says the hand-offs are only proposed / not filed"))
    claims = [s for s in sentences(text) if _CLAIMS_FILED.search(s) and not _HEDGE.search(s)]
    false_claim = bool(claims) and not new
    out.append(CheckOutcome(name, "no_filing_claim", "fail" if false_claim else "pass",
                            f"claims it filed, but nothing new is on the platform: {claims[0][:120]!r}" if false_claim
                            else "no false filing claim"))
    if p.get("project_id"):
        key = f"milestones:{p['project_id']}"
        ms, ms_before = b.truth("after", key), b.truth("before", key)
        as_of = ((b.finding.get("schedule") or {}).get("as_of") or (b.finding.get("snapshot_at") or "")[:10])
        if ms is None:
            out.append(CheckOutcome(name, "late_milestones", "error", "milestones unavailable"))
        else:
            on_track = [s for s in sentences(text) if _ON_TRACK.search(s) and not re.search(r"\bnot\b|n't", s, re.I)]

            def late_ok(rows: List[Dict[str, Any]]) -> Tuple[bool, List[str], List[str]]:
                late = past_due_names(rows, as_of)
                unnamed = [n for n in late if not re.search(
                    r"[\W_]+".join(map(re.escape, re.findall(r"[A-Za-z0-9]+", n))), text, re.I)]
                return (not unnamed and not on_track and (not late or bool(_LATE.search(text)))), late, unnamed

            ok, late, unnamed = late_ok(ms)
            then_ok = late_ok(ms_before)[0] if ms_before is not None else ok
            out.append(CheckOutcome(name, "late_milestones", "pass" if ok else ("drift" if then_ok else "fail"),
                                    f"{len(late)} past due; unnamed {unnamed}; on-track claims {len(on_track)}"))
    return out


def register_checks(reg: Registry) -> None:
    f = lambda p: [f"versions:{p['file_id']}"]  # noqa: E731
    reg.check("revision_delta_matches_db", observes=f)(check_revision_delta)
    reg.check("answer_states_change",
              observes=lambda p: [f"versions:{p['file_id']}", f"release_gate:{p['file_id']}",
                                  f"feedback:{p['file_id']}"])(check_answer_states_change)
    reg.check("release_gate_matches_db", observes=lambda p: [f"release_gate:{p['file_id']}"])(check_release_gate)
    reg.check("no_geometry_claim", observes=f)(check_no_geometry_claim)
    reg.check("record_absent", observes=lambda p: [f"design_file:{p['id']}"])(check_record_absent)
    reg.check("tool_absent", observes=lambda p: ["seat_tools"])(check_tool_absent)
    reg.check("cited_standards_exist", observes=lambda p: ["standards"])(check_cited_standards)
    reg.check("answer_audited")(check_answer_audited)
    reg.check("answer_reflects_change_notes", observes=f)(check_answer_reflects_change_notes)
    gate_and_feedback = lambda p: [f"release_gate:{p['file_id']}", f"feedback:{p['file_id']}",  # noqa: E731
                                   f"escalations:{p['file_id']}", "assignees"]
    reg.check("handoffs_match_platform", observes=gate_and_feedback)(check_handoffs_match_platform)
    reg.check("schedule_matches_db", observes=lambda p: [f"milestones:{p['project_id']}"])(check_schedule_matches_db)
    reg.check("answer_states_handoffs",
              observes=lambda p: gate_and_feedback(p) + ([f"milestones:{p['project_id']}"] if p.get("project_id")
                                                         else []))(check_answer_states_handoffs)
