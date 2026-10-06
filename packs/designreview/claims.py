"""
Design-review claim rules for agentkit's audit_narrative().

- quantities: numbers with engineering units (mm, degrees, kg, g, %) must
  match a number somewhere in the finding.
- required facts: the release verdict, every quantity stated in the
  revision notes being compared, that the delta comes from commit messages
  (not a geometry diff), open critical findings, and any refusal.
- contradictions (sentence-level, negation-aware): claiming "ready for
  release" when the gate says not ready (and vice versa), and claiming a
  geometry comparison was run when none was.
- hand-offs and schedule (when the finding has them): every proposed owner
  is named, a dry run says the hand-offs are proposed (and never claims
  one was filed/escalated/notified), "no other team" is said when none is
  needed, every past-due milestone is named, and the project is never
  called on track/on schedule while a milestone is past due.

Sentence-level negation fixes the first draft's substring weaknesses
(tests_local/test_verifiers.py XFAILs): "12.0 mm" no longer matches "2.0",
"nothing is blocked" is not read as "not ready", and reworded geometry
claims ("the geometric diff confirms ...") are caught.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from agentkit.answer import ClaimRules, RequiredFact
from packs.designreview.finding import QUANTITY_RE

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
NOT_READY_RE = re.compile(
    r"(?:\bnot|n't)\s+(?:yet\s+)?(?:ready|releasable|approved|cleared)\b"
    r"|\b(?:cannot|can'?t|must\s+not|mustn'?t|should\s+not|shouldn'?t|may\s+not)\s+(?:yet\s+)?be\s+"
    r"(?:released|approved|cleared)\b"
    r"|\b(?:hold|block(?:ed|s)?|stop)\s+(?:the\s+)?release\b"
    r"|\brelease\s+(?:is\s+|remains\s+)?(?:blocked|on\s+hold|held|gated)\b", re.I)
READY_RE = re.compile(
    r"\b(not|isn'?t|is\s+not|cannot|can'?t|must\s+not|should\s+not|shouldn'?t)\b[^.]{0,40}\b(ready|releas\w*|approv\w*)"
    r"|\bnot\s+(yet\s+)?(ready|releasable)\b|\b(hold|block(ed|s)?|stop)\b[^.]{0,20}\brelease\b"
    r"|\brelease\b[^.]{0,20}\b(blocked|on\s+hold|gated)\b|\bnot\s+releasable\b", re.I)
READY_RE = re.compile(r"\b(ready\s+for\s+release|ready\s+to\s+release|cleared\s+for\s+release|approved\s+for\s+release"
                      r"|can\s+be\s+released|releasable|is\s+ready)\b", re.I)
GEOMETRY_CLAIM_RE = re.compile(
    r"\bgeometr\w*\s+(diff\w*|comparison|analysis|compare)\b[^.]{0,40}\b(shows?|confirms?|found|finds|reveals?|indicates?|detects?)\b"
    r"|\b(compared|diffed|overlaid)\s+the\s+(3d\s+)?geometr\w*"
    r"|\b3d\s+(diff|comparison)\s+(shows?|confirms?|reveals?)", re.I)
GEOMETRY_NEGATION_RE = re.compile(r"\b(no|not|without|unavailable|cannot|can'?t|never|wasn'?t|isn'?t)\b", re.I)


def sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def claims_not_ready(sentence: str) -> bool:
    return bool(NOT_READY_RE.search(sentence))


def claims_ready(sentence: str) -> bool:
    return bool(READY_RE.search(sentence)) and not claims_not_ready(sentence)


def _num_pattern(value: float, unit: str) -> str:
    whole = int(value) if float(value).is_integer() else None
    num = rf"(?:{whole}(?:\.0+)?)" if whole is not None else re.escape(f"{value:g}")
    unit_re = {"mm": r"mm|millimet(?:re|er)s?", "deg": r"°|deg\w*", "°": r"°|deg\w*",
               "degree": r"°|deg\w*", "degrees": r"°|deg\w*", "g": r"g\b|grams?", "kg": r"kg|kilograms?",
               "%": r"%|percent"}.get(unit.lower(), re.escape(unit))
    return rf"(?<![\d.]){num}\s*(?:{unit_re})"


def required_facts(finding: Dict[str, Any]) -> List[RequiredFact]:
    facts: List[RequiredFact] = []
    if finding.get("outcome") == "declined":
        return [RequiredFact("that the request cannot be answered and why",
                             [r"cannot|can'?t|unable|not\s+(found|available|visible|permitted)|no\s+such|declin|refus"])]

    gate = finding.get("release_gate") or {}
    if gate.get("outcome") == "answered":
        if gate.get("ready"):
            facts.append(RequiredFact("that the design is ready for release per the release gate", [READY_RE.pattern]))
        else:
            facts.append(RequiredFact("that the design is NOT ready for release per the release gate",
                                      [NOT_READY_RE.pattern]))
        if (gate.get("critical_open") or 0) > 0:
            facts.append(RequiredFact(f"that {gate['critical_open']} critical finding(s) remain open", [r"critical"]))

    delta = finding.get("revision_delta") or {}
    if delta.get("outcome") == "answered":
        to_v = delta.get("to_version")
        for v in delta.get("versions") or []:
            if v.get("version_number") != to_v:
                continue
            for q in v.get("quantities") or []:
                facts.append(RequiredFact(f"{q['value']:g} {q['unit']} (from the rev notes)",
                                          [_num_pattern(q["value"], q["unit"])]))
        if not delta.get("geometry_compared"):
            facts.append(RequiredFact("that the changes come from the version commit messages, not a geometry comparison",
                                      [r"commit\s+message", r"version\s+notes?", r"revision\s+notes?", r"commit\s+notes?"]))
    elif delta.get("outcome") == "declined":
        facts.append(RequiredFact("why the revision comparison could not be made", [r"not\s+found|no\s+versions?|cannot|can'?t|unable"]))
    facts += handoff_facts(finding)
    return facts


# Said about the hand-off itself (the ask text may contain "propose" on its own).
PROPOSED_RE = (r"\b(?:hand-?offs?|escalations?)\b[^.\n]{0,60}\b(?:propos\w*|recommend\w*|suggest\w*|would\b)"
               r"|\b(?:propos\w*|recommend\w*|suggest\w*)\b[^.\n]{0,40}\b(?:hand-?offs?|escalations?)\b"
               r"|\bnothing\s+(?:has\s+been|was|is)\s+(?:yet\s+)?(?:filed|raised|escalated|sent|submitted)"
               r"|\bnot\s+(?:yet\s+)?(?:been\s+)?(?:filed|raised|sent|escalated|submitted)\b")
NO_HANDOFF_RE = (r"\bno\s+(?:\w+\s+){0,3}(?:hand-?offs?|escalations?)\b|\bno\s+other\s+(?:team|seat|app)"
                 r"|\b(?:doesn'?t|does\s+not|don'?t|do\s+not)\s+need\s+(?:another|other|a\s+different)\s+(?:team|seat|app)"
                 r"|\bdesign\s+review(?:'s)?\s+own\b|\bwithin\s+design\s+review\b")
LATE_RE = r"\boverdue\b|\bpast[\s-]+due\b|\blate\b|\bbehind\b|\bmissed\b|\bslipp\w*"
# "I have escalated ...", "an escalation has been raised", "manufacturing was notified" -- but not
# "would be escalated" / "has not been filed".
_DONE = r"(?:now\s+|already\s+|also\s+)?"
FILED_CLAIM_RE = re.compile(
    # first person: "I have escalated", "we filed", "I've notified"
    r"\b(?:I|we)(?:'ve|\s+have)?\s+" + _DONE + r"(?:escalated|filed|raised|notified|informed|alerted|"
    r"handed\s+(?:it\s+|this\s+)?(?:off|over)|assigned|sent|submitted|opened|created)\b"
    # passive about the hand-off itself: "an escalation has been raised", "a ticket was opened"
    r"|\b(?:escalations?|hand-?offs?|tickets?)\b[^.]{0,40}\b(?:has|have|was|were|is|are)\s+(?:been\s+)?" + _DONE
    + r"(?:filed|raised|sent|created|opened|submitted|logged)\b(?!\s+by\b)"
    # "... has been escalated", "manufacturing was notified"
    r"|\b(?:has|have|was|were)\s+(?:been\s+)?" + _DONE + r"escalated\b"
    r"|\b(?:has|have|was|were)\s+(?:been\s+)?" + _DONE + r"(?:notified|informed|alerted)\b(?!\s+by\b)", re.I)
FILED_NEGATION_RE = re.compile(r"\b(?:not|never|nothing|none|no\s+one|would|will|could|should|if|once|when|"
                               r"proposed?)\b|n't", re.I)
ON_TRACK_RE = re.compile(r"\bon\s+(?:track|schedule|time)\b|\bno\s+(?:schedule\s+)?(?:delays?|slippage)\b", re.I)


def name_pattern(name: str) -> str:
    """A milestone name, tolerant of case, spacing and dash style."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    return r"\b" + r"[\W_]+".join(re.escape(w) for w in words) + r"\b" if words else r"(?!x)x"


def handoff_facts(finding: Dict[str, Any]) -> List[RequiredFact]:
    facts: List[RequiredFact] = []
    h = finding.get("handoffs") or {}
    if h.get("outcome") == "answered":
        proposed = h.get("proposed") or []
        for item in proposed:
            facts.append(RequiredFact(f"that {item['owner']} needs to act (proposed hand-off)",
                                      [rf"\b{re.escape(item['owner'])}\b"]))
        if proposed and not h.get("filed") and not h.get("escalations"):
            facts.append(RequiredFact("that the hand-offs are proposed only -- nothing was filed", [PROPOSED_RE]))
        for e in (h.get("escalations") or []) + (h.get("already_open") or []):
            if e.get("number"):
                facts.append(RequiredFact(f"escalation {e['number']} ({e.get('owner')})", [re.escape(e["number"])]))
        if h.get("filed") and h.get("assignee"):
            facts.append(RequiredFact(f"that the escalations are assigned to {h['assignee']}",
                                      [name_pattern(h["assignee"])]))
        if not proposed:
            facts.append(RequiredFact("that no other team needs to act on the open items", [NO_HANDOFF_RE]))
    sched = finding.get("schedule") or {}
    if sched.get("outcome") == "answered" and sched.get("past_due"):
        facts.append(RequiredFact("that milestones are past due", [LATE_RE]))
        for name in sched["past_due"]:
            facts.append(RequiredFact(f"the past-due milestone '{name}'", [name_pattern(name)]))
    return facts


def handoff_contradictions(text: str, finding: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    h = finding.get("handoffs")
    if h is not None and not h.get("filed") and not h.get("escalations"):
        for s in sentences(text):
            if FILED_CLAIM_RE.search(s) and not FILED_NEGATION_RE.search(s):
                out.append(f"says something was filed/escalated, but nothing was filed: {s[:160]!r}")
    sched = finding.get("schedule") or {}
    if sched.get("past_due"):
        for s in sentences(text):
            if ON_TRACK_RE.search(s) and not re.search(r"\b(?:not|no\s+longer)\b|n't", s, re.I):
                out.append(f"says the project is on track, but milestones are past due: {s[:160]!r}")
    return out


# "ISO 2768-m", "ASME Y14.5", "DIN 6935", "EN 10130", "IS 2062" ... -- a standard
# the answer names must come from the finding, never from the model's memory.
STANDARD_REF_RE = re.compile(
    # Case-sensitive on purpose: standard bodies are written in capitals, and
    # re.I would read plain English ("rev B is v2") as Indian Standard "IS v2".
    r"\b(ISO|ASME|ANSI|DIN|EN|BS|IS|JIS|ASTM|SAE|IPC|IEC|MIL-STD)[\s-]?([A-Z]?\d[\w.\-:]*)")


def _norm_ref(body: str, number: str) -> str:
    return re.sub(r"[\s\-]", "", f"{body}{number}").lower().rstrip(".:")


def standard_refs(text: str) -> List[str]:
    return [m.group(0).rstrip(".:,;") for m in STANDARD_REF_RE.finditer(text or "")]


def known_standard_refs(finding: Any) -> set:
    """Standard references the finding supports: any written out in its text,
    plus every record carrying separate standard_body / standard_number fields."""
    known = {_norm_ref(m.group(1), m.group(2)) for m in STANDARD_REF_RE.finditer(repr(finding))}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("standard_body") and node.get("standard_number"):
                known.add(_norm_ref(str(node["standard_body"]), str(node["standard_number"])))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(finding)
    return known


def contradictions(text: str, finding: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    known = known_standard_refs(finding)
    for m in STANDARD_REF_RE.finditer(text or ""):
        if _norm_ref(m.group(1), m.group(2)) not in known:
            out.append(f"cites standard {m.group(0).rstrip('.:,;')!r}, which is not in the finding")
    gate = finding.get("release_gate") or {}
    if gate.get("outcome") == "answered":
        for s in sentences(text):
            if not gate.get("ready") and claims_ready(s):
                out.append(f"says the design is ready/releasable, but the release gate reports ready=false: {s[:160]!r}")
            elif gate.get("ready") and claims_not_ready(s):
                out.append(f"says the design is not ready, but the release gate reports ready=true: {s[:160]!r}")
    delta = finding.get("revision_delta") or {}
    if delta and not delta.get("geometry_compared", False):
        for s in sentences(text):
            if GEOMETRY_CLAIM_RE.search(s) and not GEOMETRY_NEGATION_RE.search(s):
                out.append(f"claims a geometry comparison, but none was run in this build: {s[:160]!r}")
    out += handoff_contradictions(text, finding)
    return out


CLAIM_RULES = ClaimRules(quantity_re=QUANTITY_RE, required=required_facts, contradictions=contradictions)
