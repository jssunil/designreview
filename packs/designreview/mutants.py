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

    @reg.mutant("change_notes_ignored", target="answer_reflects_change_notes")
    def change_notes_ignored(b: RunBundle) -> Optional[RunBundle]:
        from packs.designreview.checks import note_words

        notes = (b.finding.get("revision_delta") or {}).get("change_notes") or []
        words = note_words(notes[-1] if notes else "")
        if not words:
            return None
        text = b.answer
        for w in words:  # a generic answer that never says what the note says
            text = re.sub(rf"(?i){re.escape(w[:-1] if w.endswith('s') else w)}\w*", "[...]", text)
        return _answer(b, text)

    @reg.mutant("audit_failed", target="answer_audited")
    def audit_failed(b: RunBundle) -> Optional[RunBundle]:
        step = next((s for s in b.steps() if s.get("kind") == "narrate"), None)
        if step is None:
            return None
        step.setdefault("detail", {})["audit"] = {"ok": False, "missing": ["the release verdict"]}
        return b

    # ---- hand-offs and schedule

    def _handoffs(b: RunBundle) -> Optional[dict]:
        h = b.finding.get("handoffs")
        return h if isinstance(h, dict) and h.get("outcome") == "answered" else None

    @reg.mutant("handoff_invented", target="handoffs_match_platform")
    def handoff_invented(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None:
            return None
        fake = {"id": "00000000-0000-0000-0000-00000000f00d", "title": "Coating adhesion failure", "priority": "high"}
        h.setdefault("proposed", []).append({"owner": "paint shop", "cites": [fake], "ask": "fix it"})
        h.setdefault("would_file", []).append({"owner": "paint shop", "tool": "AgentEscalation.create",
                                               "args": {"reason": "Coating adhesion failure"}})
        return b

    @reg.mutant("handoff_dropped", target="handoffs_match_platform")
    def handoff_dropped(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or not h.get("proposed"):
            return None
        h["proposed"].pop()
        h["would_file"] = (h.get("would_file") or [])[:-1]
        return b

    @reg.mutant("handoff_cites_closed_item", target="handoffs_match_platform")
    def handoff_cites_closed_item(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or not h.get("proposed"):
            return None
        h["proposed"][0]["cites"].append({"id": "11111111-2222-3333-4444-555555555555",
                                          "title": "Weld spatter on the old bracket", "priority": "low"})
        return b

    @reg.mutant("handoff_marked_filed", target="handoffs_match_platform")
    def handoff_marked_filed(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or not h.get("proposed") or not b.dry_run:
            return None
        h["filed"] = True
        return b

    @reg.mutant("filing_claimed_in_answer", target="answer_states_handoffs")
    def filing_claimed_in_answer(b: RunBundle) -> Optional[RunBundle]:
        if _handoffs(b) is None or not b.dry_run:
            return None
        return _answer(b, b.answer + "\nI have escalated this to the owning team.")

    @reg.mutant("handoff_owner_unnamed", target="answer_states_handoffs")
    def handoff_owner_unnamed(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or not h.get("proposed"):
            return None
        owner = h["proposed"][0]["owner"]
        return _answer(b, re.sub(rf"(?i)\b{re.escape(owner)}\b", "another team", b.answer))

    @reg.mutant("no_handoff_unstated", target="answer_states_handoffs")
    def no_handoff_unstated(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or h.get("proposed"):
            return None
        text = "\n".join(line for line in b.answer.splitlines()
                         if not re.search(r"(?i)no other team|no\s+(?:\w+\s+){0,3}hand-?off|design review'?s? own|"
                                          r"need\w*\s+(?:another|other)", line))
        return _answer(b, text)

    @reg.mutant("escalation_not_on_platform", target="handoffs_match_platform")
    def escalation_not_on_platform(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or b.dry_run or not h.get("escalations"):
            return None  # write runs that filed only
        h["escalations"][0]["id"] = "00000000-0000-0000-0000-0000000e5c00"
        return b

    @reg.mutant("filed_number_unstated", target="answer_states_handoffs")
    def filed_number_unstated(b: RunBundle) -> Optional[RunBundle]:
        h = _handoffs(b)
        if h is None or b.dry_run or not h.get("escalations"):
            return None
        text = b.answer
        for e in h["escalations"]:
            text = text.replace(e.get("number") or "\x00", "an escalation")
        return _answer(b, text)

    @reg.mutant("late_milestone_hidden", target="schedule_matches_db")
    def late_milestone_hidden(b: RunBundle) -> Optional[RunBundle]:
        s = b.finding.get("schedule") or {}
        if not s.get("past_due"):
            return None
        s["past_due"] = s["past_due"][1:]
        return b

    @reg.mutant("milestone_date_shifted", target="schedule_matches_db")
    def milestone_date_shifted(b: RunBundle) -> Optional[RunBundle]:
        ms = (b.finding.get("schedule") or {}).get("milestones") or []
        if not ms:
            return None
        ms[0]["due_date"] = "2099-01-01"
        return b

    @reg.mutant("on_track_claimed", target="answer_states_handoffs")
    def on_track_claimed(b: RunBundle) -> Optional[RunBundle]:
        if not (b.finding.get("schedule") or {}).get("past_due"):
            return None
        return _answer(b, b.answer + "\nThe project is on track for its release.")
