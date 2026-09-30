"""
Offline tests for the finding (packs/designreview/finding.py), the claim audit
(agentkit/answer + packs/designreview/claims.py), the template and the narrator.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

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
from packs.designreview.finding import build_finding
from packs.designreview.templates import plain_rendering

# Write your tests below.

"""  [ ] Rev letters come from commit messages: Battery Tray B -> v2, C -> v3; Propeller B -> v1, C -> v2;
      numeric revs work ("Rev 2" -> 2)"""

def test_rev_letter_from_commit():
  commit_message="Battery Tray B -> v2"
  assert rev_from_message(commit_message) == "2"
  commit_message="Propeller B -> v1"
  assert rev_from_message(commit_message) == "1"
  commit_message="Rev 2"
  
