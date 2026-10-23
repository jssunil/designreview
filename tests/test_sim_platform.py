"""
Offline tests for agentkit/sim (fixture capture model, replay, faults) and for
the whole harness run offline against the captured fixture.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] A recorded call replays its result; a recorded error replays its error kind
  [x] A call that was never recorded raises "not in fixture" -- never an empty result
  [x] Replayed results are fresh copies (mutating one doesn't change the next)
  [x] drop_tool removes the tool from the catalogue and refuses it (NOT_IN_SEAT)
  [x] fail_once fails the next call only; fail_always fails every call; faults only fire while armed
  [ ] TODO: edit_text rewrites recorded results (MCP and REST) after apply_edits()
  [x] Bad fault specs ("nope:x", "fail_once:A.get") raise ValueError
  [ ] TODO: fail_times:<tool>:flaky:2 fails the next two calls only; "fail_times:A.get:flaky:x" raises ValueError
  [ ] TODO: inject_text:<tool>:<field>=<text> appends the text to that field BEFORE the agent reads it
  [ ] TODO: truncate:<tool>:2 keeps two rows of "data" and leaves "total" as it was
  [ ] TODO: rename_field:<tool>:<old>=<new> renames the key in every row
  [ ] TODO: an offline run writes sim.json with the fixture and the faults (the injection check reads it)
  [x] Offline batch on the fixtures folder (packs/designreview/fixtures): all 12 tasks grade "pass",
      each replaying its own tenant's fixture (suryodaya.json / keystone.json)
  [ ] TODO: SimSession with a folder raises a clear error for a tenant that has no fixture
  [ ] TODO: `python -m agentkit.sim.capture --tenant keystone` only runs tasks whose tenants include keystone
  [ ] TODO: Under faults, t01 grades: fail_once flaky -> pass; release readiness always failing -> fail;
      rev C note edited during the run -> drift
  [ ] TODO: Two batches started in the same second get different ids (no evidence overwritten)
  [ ] TODO: scripts/ci_fault_matrix.py: all 13 scenarios of packs/designreview/ci_faults.toml grade as expected
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: tools_called() lists the tools in call order, including the ones that failed
  [ ] TODO: SeatFixture.save then load gives the same calls (round-trip in tmp_path)
  [ ] TODO: canonical(args) is the same for {"a": 1, "b": 2} and {"b": 2, "a": 1}
Hint: SimSession(fixture, faults, registry.read_only) gives reader_for / run_agent to pass into
agentkit.harness.batch.run_batch(..., runs_dir=tmp_path, fresh_reader_per_task=True); grade with
agentkit.harness.grade.grade_batch(batch_dir).
"""

from pathlib import Path

import pytest

from agentkit.harness.batch import SimSession, run_batch
from agentkit.harness.grade import grade_batch
from agentkit.harness.tasks import load_tasks
from agentkit.registry import load_pack
from agentkit.sim import SeatFixture, SimPlatform
from agentkit.transport import FLAKY, MISSING, NOT_IN_SEAT, SeatCallFailure

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "packs" / "designreview" / "fixtures"
SURYODAYA = FIXTURES / "suryodaya.json"
TRAY = {"id": "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"}
NO_SUCH_FILE = {"id": "00000000-0000-0000-0000-000000000000"}


def test_recorded_results_and_errors_replay_and_each_result_is_a_fresh_copy():
    sim = SimPlatform(SURYODAYA)
    first = sim.invoke_tool("DesignFile.get", TRAY)
    assert first
    first["mutated"] = True
    assert "mutated" not in sim.invoke_tool("DesignFile.get", TRAY)     # a fresh copy every time
    with pytest.raises(SeatCallFailure) as err:
        sim.invoke_tool("DesignFile.get", NO_SUCH_FILE)                 # a recorded error replays its kind
    assert err.value.kind == MISSING


def test_a_call_that_was_never_recorded_is_loud():
    with pytest.raises(SeatCallFailure, match="not in fixture"):
        SimPlatform(SURYODAYA).invoke_tool("DesignFile.get", {"id": "never-captured"})


def test_drop_tool_and_fail_once_and_faults_only_fire_while_armed():
    sim = SimPlatform(SURYODAYA, ["drop_tool:DesignFeedback.list", "fail_once:DesignFile.get:flaky"])
    assert not sim.offers("DesignFeedback.list")
    with pytest.raises(SeatCallFailure) as err:
        sim.invoke_tool("DesignFeedback.list", {})
    assert err.value.kind == NOT_IN_SEAT

    sim.armed = False
    assert sim.invoke_tool("DesignFile.get", TRAY)           # disarmed: the fault waits
    sim.armed = True
    with pytest.raises(SeatCallFailure) as err:
        sim.invoke_tool("DesignFile.get", TRAY)
    assert err.value.kind == FLAKY
    assert sim.invoke_tool("DesignFile.get", TRAY)           # only the next call failed

    with pytest.raises(ValueError):
        SimPlatform(SURYODAYA, ["nope:x"])
    with pytest.raises(ValueError):
        SimPlatform(SURYODAYA, ["fail_once:A.get"])


def test_offline_batch_all_twelve_tasks_pass_on_their_own_tenant_fixture(tmp_path):
    registry = load_pack("packs.designreview")
    tasks = load_tasks([Path(registry.extras["tasks_dir"])])
    sim = SimSession(FIXTURES, [], registry.read_only)
    batch_dir = run_batch(
        tasks,
        skip_llm=True,
        runs_dir=tmp_path,
        reader_factory=sim.reader_for,
        agent_runner=sim.run_agent,
        fresh_reader_per_task=True,
        batch_id="sim",
    )
    _report, results = grade_batch(batch_dir)          # results: [(manifest entry, score or None)]
    statuses = {entry["run_id"]: (score or {}).get("status") for entry, score in results}
    assert len(tasks) == 12
    assert set(statuses.values()) == {"pass"}, statuses

