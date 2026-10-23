"""
Offline tests for the verifier self-test (agentkit/harness/mutants.py +
packs/designreview/mutants.py): the harness must catch a misbehaving agent.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] On an offline batch of passing runs, calibrate() catches every mutant: report["ok"] is True,
      no "missed", no "unexercised_checks"
  [x] Each built-in mutant makes its target check fail: crashed_run, sneaky_write, no_answer, duplicate_read,
      injection_echoed, stray_state_change (write runs only) and golden_broken (tasks with goldens only)
  [ ] TODO: A deliberately weakened check (always "pass") shows up as MISSED and as unexercised
  [ ] TODO: Runs that did not pass are skipped, not used for calibration
  [ ] TODO: Calibration never changes any file in the run folders
Write at least one mutant-style test of your own for each verifier you care about most, e.g.:
  [ ] TODO: take a passing t01 run, change the finding's to_version to 2 -> revision_delta_matches_db fails
  [ ] TODO: append "The part is ready for release." to the answer -> release_gate_matches_db fails
Pack mutants added since 2026-10-14 (packs/designreview/mutants.py, 30 in all):
  [ ] TODO: refused_without_lookup: a refusal with no DesignFile.get in the journal -> lookup_before_refusal fails
  [ ] TODO: route_misread: a routed run whose taskrun.route names another file -> routed_as fails
End-to-end calibration (agentkit/harness/replay.py, since 2026-10-13):
  [ ] TODO: calibrate_replays(batch_dir, "packs/designreview/fixtures") is ok: every control replay still passes and
      every replay fault fails its target check
  [ ] TODO: replaying t01 with the gate_reports_ready fault: the new run's finding says ready, release_gate_matches_db
      fails, and failed_checklists is gone (its rule saw no blockers)
  [ ] TODO: the original run folder is byte-for-byte unchanged after a replay
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: every mutant name (built-in and pack) is unique, and each names a target check that the registry has
  [ ] TODO: calibrate() run twice on the same batch gives the same report (deterministic)
  [ ] TODO: golden_broken on a passing t01 run: the golden outcome fails; the other built-in checks keep their status
      (a pack verifier that reads the same finding field, e.g. revision_delta_matches_db, may fail too)
Hint: build the offline batch as in test_sim_platform.py, then calibrate([batch_dir]).
"""

import json
from pathlib import Path

import pytest

from agentkit.harness.batch import SimSession, run_batch
from agentkit.harness.mutants import calibrate
from agentkit.harness.replay import calibrate_replays, replay_run
from agentkit.harness.tasks import load_task
from agentkit.llm.gateway import LLMGateway
from agentkit.registry import load_pack

FIXTURES = Path("packs/designreview/fixtures")
T01 = Path("packs/designreview/tasks/t01_battery_tray_rev_diff.toml")

# Write your tests below.
'''[ ] On an offline batch of passing runs, calibrate() catches every mutant: report["ok"] is True,
      no "missed", no "unexercised_checks" 
'''
def _t01_batch(tmp_path, *, skip_llm: bool) -> Path:
    """A batch of t01 runs; the platform is always replayed from the captured fixtures.
    skip_llm=True: rule planner + template answer. skip_llm=False: real LLM planner + LLM answer."""
    t01_task = load_task(T01)
    registry = load_pack(t01_task.pack)
    sim = SimSession(FIXTURES, faults=[], read_only=registry.read_only)
    return run_batch([t01_task], skip_llm=skip_llm, runs_dir=tmp_path, reader_factory=sim.reader_for,
                     agent_runner=sim.run_agent, fresh_reader_per_task=True)


def _assert_calibration_ok(report):
    assert not report["skipped_runs_not_passing"], report  # every run passed, so all were used
    assert sum(r["applied"] for r in report["mutants"].values()) > 0, report  # not vacuously ok
    assert report["missed"] == []
    assert report["unexercised_checks"] == []
    assert report["ok"] is True


def test_calibrates_all_builtins(tmp_path):
    # Offline: no live platform, no LLM.
    # calibrate() mutates every passing run and expects each mutant's target check to fail.
    _assert_calibration_ok(calibrate([_t01_batch(tmp_path, skip_llm=True)]))


def _keyed_llm_available() -> bool:
    """A provider with a real API key. Keyless local rows (lmstudio, ollama) always count as
    "available" to the gateway, so available_providers() alone is never empty."""
    gw = LLMGateway()
    return any(k for p in gw.available_providers() for k in gw._keys(p))


def _answer_sources(batch_dir: Path) -> dict:
    runs_root = batch_dir.parent.parent  # run_batch: <runs_dir>/batches/<id>, runs in <runs_dir>/<run_id>
    manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
    return {e["run_id"]: json.loads((runs_root / e["run_id"] / "taskrun.json").read_text(encoding="utf-8"))
            .get("answer_source") for e in manifest["runs"]}


@pytest.mark.agent
@pytest.mark.gateway_live
@pytest.mark.skipif(not _keyed_llm_available(), reason="no LLM provider key in env/.env")
def test_calibrates_all_builtins_llm(tmp_path):
    # Same as above, but the planner and the answer come from a real LLM (costs calls, not deterministic).
    batch_dir = _t01_batch(tmp_path, skip_llm=False)
    # A failed LLM call falls back to the template answer and the run still passes: that is not an LLM test.
    sources = _answer_sources(batch_dir)
    assert sources and all(s == "llm" for s in sources.values()), f"answer not from the LLM: {sources}"
    _assert_calibration_ok(calibrate([batch_dir]))


    
