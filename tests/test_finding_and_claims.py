"""
Offline tests for the finding (packs/designreview/finding.py), the claim audit
(agentkit/answer + packs/designreview/claims.py), the template and the narrator.    Markers: none

Author(s): Sunil Jakkaraju , Bharath KR    Written by hand:  yes

Use cases to cover:
  [ ] Rev letters come from commit messages: Battery Tray B -> v2, C -> v3; Propeller B -> v1, C -> v2;
      numeric revs work ("Rev 2" -> 2)
  [ ] A requested rev that doesn't exist makes revision_delta "declined" and the finding "partial"
  [ ] A missing design file makes the whole finding "declined" with the reason
  [ ] geometry_compared is False and basis is "commit_message"
  [ ] audit_narrative flags an invented "4.5 mm", an unknown UUID, and a standard ("ISO 2768-m") not in the finding
  [ ] "12.0 mm" is NOT accepted as "2.0 mm"
  [ ] "The tray is ready for release." contradicts a not-ready gate; "Nothing is blocked" alone does not
  [ ] "Rev B is v2" is not read as Indian Standard "IS v2"
  [ ] The template answer passes its own audit for a normal, a ready, a declined and a partial finding
  [ ] Narrator: good first draft -> source "llm"; bad then good -> one rewrite; bad twice -> template;
      LLM exception -> template with the reason recorded
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