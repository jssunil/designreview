"""
Offline tests for the design-review verifiers in packs/designreview/checks.py.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Goal: prove the verifiers REJECT wrong answers, not just accept right ones.
Use cases to cover:
  [ ] revision_delta_matches_db: rev B->v2, rev C->v3 passes; mapping by position (v1->v2) fails
  [ ] revision_delta_matches_db: versions that changed during the run give "drift", not "fail"
  [ ] answer_states_change: 2.0 / 3.0 / 4.2 mm all stated passes; "12.0 mm to 13.0 mm" fails
  [ ] answer_states_change: an invented quantity (not in any commit, blocker or feedback) fails
  [ ] release_gate_matches_db: "not ready for release" is read as NOT ready (negation handled)
  [ ] release_gate_matches_db: "Nothing is blocked. It is ready for release." is read as READY
  [ ] no_geometry_claim: "the geometric diff confirms ..." fails; "no geometry comparison was run" passes
  [ ] record_absent / tool_absent: a refusal passes; carrying on after the refusal fails
  [ ] Ground truth that couldn't be read gives "error" -- never "pass"
Hint: build a RunBundle by hand (agentkit.harness.checks.RunBundle) with `after` / `before` as
{"observations": {"versions:<file_id>": {"value": [...]}}} -- no network, no files needed.
"""

import pytest

from agentkit.harness.checks import RunBundle
from packs.designreview import checks

# Write your tests below.
