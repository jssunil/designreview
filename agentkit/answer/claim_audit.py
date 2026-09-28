"""
audit_narrative(): does an answer say only what the finding says, and enough of it?

Deterministic, no LLM. Four kinds of problem:

- unsupported_ids         a record UUID in the text that appears nowhere in the finding
- unsupported_quantities  a number-with-unit (per the pack's pattern) whose value matches
                          no number anywhere in the finding (numbers inside finding
                          strings count -- e.g. "2.0 mm" inside a commit message)
- contradictions          pack-defined: the text asserts the opposite of the finding
                          (e.g. "ready for release" when the gate says not ready)
- missing                 pack-defined facts the answer must carry but left out

The pack supplies a ClaimRules (what a quantity looks like, which facts are
required, which contradictions to look for); this module holds no domain
vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Set

UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# Every number found in the finding (including inside strings) widens what the
# text may cite, so a simple, permissive pattern is the safe choice here.
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
TOLERANCE = 1e-6


@dataclass
class RequiredFact:
    label: str  # human wording used in the correction note
    patterns: List[str]  # regexes; the fact is present if ANY matches (case-insensitive)

    def present_in(self, text: str) -> bool:
        return any(re.search(p, text, re.I) for p in self.patterns)


@dataclass
class ClaimRules:
    quantity_re: Optional[Pattern[str]] = None  # group 1 = numeric value
    required: Callable[[Dict[str, Any]], List[RequiredFact]] = lambda finding: []
    contradictions: Callable[[str, Dict[str, Any]], List[str]] = lambda text, finding: []
    ignore_ids: Set[str] = field(default_factory=set)


def known_numbers(obj: Any, out: Optional[Set[float]] = None) -> Set[float]:
    out = set() if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in NUMBER_RE.findall(obj):
            try:
                out.add(float(m))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            known_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            known_numbers(v, out)
    return out


def _flat(obj: Any) -> str:
    return repr(obj).lower()


def audit_narrative(text: str, finding: Dict[str, Any], rules: ClaimRules) -> Dict[str, Any]:
    text = text or ""
    flat = _flat(finding)
    numbers = known_numbers(finding)

    unsupported_ids = sorted({u for u in UUID_RE.findall(text)
                              if u.lower() not in flat and u.lower() not in rules.ignore_ids})
    unsupported_quantities: List[str] = []
    if rules.quantity_re is not None:
        for m in rules.quantity_re.finditer(text):
            try:
                value = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if not any(abs(value - k) <= TOLERANCE for k in numbers):
                unsupported_quantities.append(m.group(0).strip())
    contradictions = list(rules.contradictions(text, finding))
    missing = [f.label for f in rules.required(finding) if not f.present_in(text)]

    ok = not (unsupported_ids or unsupported_quantities or contradictions or missing)
    return {"ok": ok, "unsupported_ids": unsupported_ids,
            "unsupported_quantities": sorted(set(unsupported_quantities)),
            "contradictions": contradictions, "missing": missing}


def correction_note(report: Dict[str, Any]) -> str:
    """The message sent back to the LLM for its one rewrite."""
    lines = ["Your answer does not match the FINDING. Rewrite the whole answer from scratch, "
             "using only facts in the FINDING, and do not mention that it was corrected."]
    if report["unsupported_ids"]:
        lines.append("- These record ids are not in the FINDING; remove them: " + ", ".join(report["unsupported_ids"]))
    if report["unsupported_quantities"]:
        lines.append("- These quantities are not in the FINDING; remove them or use the FINDING's exact values: "
                     + ", ".join(report["unsupported_quantities"]))
    for c in report["contradictions"]:
        lines.append(f"- Contradicts the FINDING: {c}")
    if report["missing"]:
        lines.append("- You must also state: " + "; ".join(report["missing"]) + ".")
    return "\n".join(lines)
