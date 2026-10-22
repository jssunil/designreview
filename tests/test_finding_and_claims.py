"""
Offline tests for the finding (packs/designreview/finding.py), the claim audit
(agentkit/answer + packs/designreview/claims.py), the template and the narrator.    Markers: none

Author(s): Sunil Jakkaraju , Bharath KR    Written by hand:  yes

Use cases to cover:
  [x] Rev letters come from commit messages: Battery Tray B -> v2, C -> v3; Propeller B -> v1, C -> v2;
      numeric revs work ("Rev 2" -> 2)
  [ ] TODO: A requested rev that doesn't exist makes revision_delta "declined" and the finding "partial"
  [ ] TODO: A missing design file makes the whole finding "declined" with the reason
  [ ] TODO: geometry_compared is False and basis is "commit_message"
  [ ] TODO: audit_narrative flags an invented "4.5 mm", an unknown UUID, and a standard ("ISO 2768-m") not in the finding
  [ ] TODO: "12.0 mm" is NOT accepted as "2.0 mm"
  [ ] TODO: "The tray is ready for release." contradicts a not-ready gate; "Nothing is blocked" alone does not
  [ ] TODO: "Rev B is v2" is not read as Indian Standard "IS v2"
  [ ] TODO: The template answer passes its own audit for a normal, a ready, a declined and a partial finding
  [ ] TODO: Narrator: good first draft -> source "llm"; bad then good -> one rewrite; bad twice -> template;
      LLM exception -> template with the reason recorded
  [ ] TODO: The prompt carries the finding between <untrusted_platform_data> tags; a commit message containing
      "</untrusted_platform_data>" can't close the fence (and the JSON still parses); the system prompt ends
      with DATA_RULE
  [ ] TODO: A draft saying "2.5 mm" gets a rewrite prompt naming '3.0 mm' and where it is in the finding
Added since 2026-10-14:
  [ ] TODO: requested_pair: "between rev B and rev C", "from B to C", "rev C vs rev B" give ["B", "C"]; a rev named in
      another sentence ("Rev A was the prototype.") is not compared; three revs and no compare phrase ->
      revision_delta declined
  [ ] TODO: quarantine_instructions on a commit message with "SYSTEM: ignore the rules" keeps the text before it,
      cuts the instruction, and the finding records where it cut ("quarantined")
  [ ] TODO: compose_answer(tiers=("fast", "quality")): a passing fast draft is the answer; a failing one goes to the
      quality tier; the result names the tier, the provider and the model
  [ ] TODO: audit_narrative with "[@path]" citations: see test_llm_cascade_and_budgets.py
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: known_numbers() of a nested finding collects numbers from lists and dicts at every depth
  [ ] TODO: strip_citations("Wall grew to 3.0 mm [@revision_delta.change_notes[1]].") leaves no "[@" in the text
  [ ] TODO: fence_data on a finding whose text has "<untrusted_platform_data>" (an opening tag) still parses as
      one JSON object inside one fence
Hint: build node results by hand -- agentkit.graph.NodeResult("resolved", data={...}) -- and call
build_finding(results, {"file_id": ..., "prompt": ...}). For the narrator, pass a fake gateway object
with a call(prompt, **kw) method that returns scripted strings.
"""

import pytest

from agentkit.answer import audit_narrative, compose_answer
from agentkit.graph import NodeResult
from packs.designreview.claims import CLAIM_RULES
from packs.designreview.finding import build_finding, rev_letter
from packs.designreview.templates import plain_rendering

# Write your tests below.

def test_rev_letter_from_commit():
    # Numeric and letter revs extracted from commit messages
    assert rev_letter("Rev B — cell layout updated") == "B"
    assert rev_letter("Rev C — corner bend radius opened") == "C"
    assert rev_letter("Rev 2 — jaw insert hardened") == "2"

    # Battery Tray mapping: B -> v2, C -> v3
    tray_results = {
        "design_file": NodeResult("resolved", data={"id": "f1", "name": "Battery Tray"}),
        "revisions": NodeResult("resolved", data={"data": [
            {"version_number": 1, "commit_message": "Initial release against Bharat EV drawing BEV-BT-2400 rev A."},
            {"version_number": 2, "commit_message": "Rev B — cell layout updated, 2.0 mm corner bend radius."},
            {"version_number": 3, "commit_message": "Rev C — corner bend radius opened from 2.0 mm to 3.0 mm."},
        ]}),
    }
    tray_finding = build_finding(tray_results, {"file_id": "f1", "prompt": "What changed between rev B and rev C?"})
    assert tray_finding["revision_delta"]["from_version"] == 2
    assert tray_finding["revision_delta"]["to_version"] == 3

    # Propeller mapping: B -> v1, C -> v2
    prop_results = {
        "design_file": NodeResult("resolved", data={"id": "f2", "name": "Propeller"}),
        "revisions": NodeResult("resolved", data={"data": [
            {"version_number": 1, "commit_message": "Rev B — three-blade, 380 mm diameter."},
            {"version_number": 2, "commit_message": "Rev C — fourth blade added."},
        ]}),
    }
    prop_finding = build_finding(prop_results, {"file_id": "f2", "prompt": "What changed between rev B and rev C?"})
    assert prop_finding["revision_delta"]["from_version"] == 1
    assert prop_finding["revision_delta"]["to_version"] == 2

def test_first_label_wins_when_note_has_two():
    #Documents current behaviour: first match wins if a note contains multiple labels.
    assert rev_letter("Rev B reverted to rev A") == "B"

def test_no_label_gives_none():
    # No revision or rev present gives None.
    assert rev_letter("Initial release against Bharat EV drawing BEV-BT-2400") is None
    assert rev_letter("") is None
    assert rev_letter(" ") is None
    assert rev_letter(None) is None

def test_label_at_last_in_small():
    # label at the end of commit message is identified even in lowercase
    assert rev_letter("Initial release against Bharat EV drawing BEV-BT-2400 rev a") == "A"
    assert rev_letter("Vice assembly rev 1.") == "1"

def test_rev_letter_review_is_not_a_revision():
    # word starting with rev is not always a label
    assert rev_letter("Review comments applied") is None
    assert rev_letter("Review 2 comments applied") is None
    assert rev_letter("Review B applied") is None