"""
Offline tests for agentkit/sim (fixture capture model, replay, faults) and for
the whole harness run offline against the captured fixture.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] A recorded call replays its result; a recorded error replays its error kind
  [ ] A call that was never recorded raises "not in fixture" -- never an empty result
  [ ] Replayed results are fresh copies (mutating one doesn't change the next)
  [ ] drop_tool removes the tool from the catalogue and refuses it (NOT_IN_SEAT)
  [ ] fail_once fails the next call only; fail_always fails every call; faults only fire while armed
  [ ] edit_text rewrites recorded results (MCP and REST) after apply_edits()
  [ ] Bad fault specs ("nope:x", "fail_once:A.get") raise ValueError
  [ ] Offline batch on the fixtures folder (packs/designreview/fixtures): all 9 tasks grade "pass",
      each replaying its own tenant's fixture (suryodaya.json / keystone.json)
  [ ] SimSession with a folder raises a clear error for a tenant that has no fixture
  [ ] `python -m agentkit.sim.capture --tenant keystone` only runs tasks whose tenants include keystone
  [ ] Under faults, t01 grades: fail_once flaky -> pass; release readiness always failing -> fail;
      rev C note edited during the run -> drift
  [ ] Two batches started in the same second get different ids (no evidence overwritten)
Hint: SimSession(fixture, faults, registry.read_only) gives reader_for / run_agent to pass into
agentkit.harness.batch.run_batch(..., runs_dir=tmp_path, fresh_reader_per_task=True); grade with
agentkit.harness.grade.grade_batch(batch_dir).
"""

from pathlib import Path

import pytest

from agentkit.harness.batch import SimSession, run_batch
from agentkit.harness.grade import grade_batch
from agentkit.sim import SeatFixture, SimPlatform

# Write your tests below.
