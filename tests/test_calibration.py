"""
Offline tests for the verifier self-test (agentkit/harness/mutants.py +
packs/designreview/mutants.py): the harness must catch a misbehaving agent.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] On an offline batch of passing runs, calibrate() catches every mutant: report["ok"] is True,
      no "missed", no "unexercised_checks"
  [ ] Each built-in mutant (crashed_run, sneaky_write, no_answer) makes its target check fail
  [ ] A deliberately weakened check (always "pass") shows up as MISSED and as unexercised
  [ ] Runs that did not pass are skipped, not used for calibration
  [ ] Calibration never changes any file in the run folders
Write at least one mutant-style test of your own for each verifier you care about most, e.g.:
  [ ] take a passing t01 run, change the finding's to_version to 2 -> revision_delta_matches_db fails
  [ ] append "The part is ready for release." to the answer -> release_gate_matches_db fails
Hint: build the offline batch as in test_sim_platform.py, then calibrate([batch_dir]).
"""

import pytest

from agentkit.harness.mutants import calibrate

# Write your tests below.
