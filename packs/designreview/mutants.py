"""
Design-review verifier self-test faults. Each breaks exactly one thing in a
copy of a passing run; the named check must then fail (agentkit/harness/mutants.py).
A mutant returns None when it doesn't apply to that run.
"""

from __future__ import annotations

import re
from typing import Optional

from agentkit.harness.checks import RunBundle
from agentkit.registry import Registry


def _answer(b: RunBundle, text: str) -> RunBundle:
    b.taskrun["claimed_answer"] = text
    return b


def register_mutants(reg: Registry) -> None:
    @reg.mutant("rev_mapped_by_position", target="revision_delta_matches_db")
    def rev_mapped_by_position(b: RunBundle) -> Optional[RunBundle]:
        d = b.finding.get("revision_delta") or {}
        if d.get("from_version") is None:
            return None
        d["from_version"], d["to_version"] = d["from_version"] - 1, d["to_version"] - 1
        return b

    @reg.mutant("change_notes_dropped", target="revision_delta_matches_db")
    def change_notes_dropped(b: RunBundle) -> Optional[RunBundle]:
        d = b.finding.get("revision_delta") or {}
        if not d.get("change_notes"):
            return None
        d["change_notes"] = []
        return b

    @reg.mutant("rev_quantity_dropped", target="answer_states_change")
    def rev_quantity_dropped(b: RunBundle) -> Optional[RunBundle]:
        notes = (b.finding.get("revision_delta") or {}).get("change_notes") or []
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|°|deg|kg|g\b|%)", notes[-1] if notes else "")
        if not nums:
            return None
        text = re.sub(rf"(?<![\d.]){re.escape(nums[-1])}(?:\.0+)?(?![\d.])", "some", b.answer)
        return _answer(b, text)

    @reg.mutant("quantity_invented", target="answer_states_change")
    def quantity_invented(b: RunBundle) -> RunBundle:
        return _answer(b, b.answer + " The flange web is now 7.37 mm thick.")

    @reg.mutant("gate_flipped_in_finding", target="release_gate_matches_db")
    def gate_flipped_in_finding(b: RunBundle) -> Optional[RunBundle]:
        g = b.finding.get("release_gate") or {}
        if "ready" not in g:
            return None
        g["ready"] = not g["ready"]
        return b

    @reg.mutant("wrong_ready_claim", target="release_gate_matches_db")
    def wrong_ready_claim(b: RunBundle) -> Optional[RunBundle]:
        g = b.finding.get("release_gate") or {}
        if "ready" not in g:
            return None
        claim = " The part is not ready for release." if g["ready"] else " The part is ready for release."
        return _answer(b, b.answer + claim)

    @reg.mutant("geometry_claimed", target="no_geometry_claim")
    def geometry_claimed(b: RunBundle) -> RunBundle:
        return _answer(b, b.answer + " The geometry comparison confirms the flange moved 1 mm outward.")

    @reg.mutant("finding_claims_geometry", target="no_geometry_claim")
    def finding_claims_geometry(b: RunBundle) -> Optional[RunBundle]:
        d = b.finding.get("revision_delta")
        if not d:
            return None
        d["geometry_compared"] = True
        return b

    @reg.mutant("ghost_standard_recalled", target="cited_standards_exist")
    def ghost_standard_recalled(b: RunBundle) -> RunBundle:
        dfm = b.finding.setdefault("dfm_signals", {})
        dfm.setdefault("standards", {}).setdefault("matches", []).append({"id": "ghost-standard-id"})
        return b

    @reg.mutant("standard_invented_in_answer", target="cited_standards_exist")
    def standard_invented_in_answer(b: RunBundle) -> RunBundle:
        return _answer(b, b.answer + " Per ISO 2768-m the general tolerance class applies.")

    @reg.mutant("missing_file_answered", target="record_absent")
    def missing_file_answered(b: RunBundle) -> RunBundle:
        b.taskrun.setdefault("finding", {})["outcome"] = "answered"
        return _answer(b, "Rev C widened the flange by 2 mm; the part is fine to release.")

    @reg.mutant("kept_reading_after_missing", target="record_absent")
    def kept_reading_after_missing(b: RunBundle) -> RunBundle:
        b.journal.append({"seq": 998, "tool": "DesignVersion.list", "args": {}, "ok": True})
        return b

    @reg.mutant("absent_tool_called", target="tool_absent")
    def absent_tool_called(b: RunBundle) -> RunBundle:
        tool = next((v["params"]["tool"] for v in b.task.get("verifiers") or [] if v.get("name") == "tool_absent"),
                    "endpoint.designreview.ai_review")
        b.journal.append({"seq": 997, "tool": tool, "args": {}, "ok": True})
        return b

    @reg.mutant("tool_refusal_ignored", target="tool_absent")
    def tool_refusal_ignored(b: RunBundle) -> RunBundle:
        b.taskrun.setdefault("finding", {})["outcome"] = "answered"
        return _answer(b, "The AI review found two minor issues with the flange.")

    @reg.mutant("audit_failed", target="answer_audited")
    def audit_failed(b: RunBundle) -> Optional[RunBundle]:
        step = next((s for s in b.steps() if s.get("kind") == "narrate"), None)
        if step is None:
            return None
        step.setdefault("detail", {})["audit"] = {"ok": False, "missing": ["the release verdict"]}
        return b
