"""
Offline tests for how the harness *reports* results: reliability over repeats, error bars,
trivial-agent baselines, partial credit, failure modes and tool-call budgets
(agentkit/harness/grade.py, agentkit/harness/checks.py, packs/designreview/checks.py).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/RESEARCH_GAP_ANALYSIS.md, gaps G2, G3, G9, G10, G11 (and §4 for the math).
Items marked (not built yet) test a feature that does not exist today: write the test, and mark it
@pytest.mark.xfail(strict=True, reason="G#") (or skip) until the feature lands.

Use cases to cover:
  Works today:
  [x] score_batch: 3 runs of one task, 2 pass -> pass_all is False and the run ids that failed are listed
  [x] score_batch: a task in manifest["expected"] with no runs counts as missing and does not pass
  [x] A hedged answer ("The part is ready for release. It is not ready.") against a NOT-ready gate:
      release_gate_matches_db must fail -- an answer that lists both verdicts can't pass (G2)

  pass@k and pass^k (G3, built 2026-10-14: grade.pass_at_k / pass_hat_k / frac, score_batch rows):
  [x] n=3 runs, c=2 pass: pass@1 = 2/3, pass@2 = 1, pass@3 = 1
  [x] same task: pass^1 = 2/3, pass^2 = 1/3, pass^3 = 0
  [x] values are exact fractions written as strings ("1/3"), so batch_score.json stays byte-stable
      and grade --check still says "identical"
  [x] k > n is rejected (or not reported), never a division by zero

  Error bars (G3, built 2026-10-14: grade.wilson_interval / clustered_se, batch_score.json "total"):
  [ ] TODO: Wilson 95% interval for 9 of 9 tasks passing is about (0.70, 1.00)
  [ ] TODO: Wilson 95% interval for 0 of 20 attacked runs succeeding has an upper bound of about 0.16
  [ ] TODO: Clustered standard error (cluster = task) is >= the plain standard error when repeats agree

  Trivial-agent baselines (G2):
  [ ] TODO: (works today -- keep it so) a "null agent" bundle (no tool calls, finding declined, answer
      "I can't answer that") fails every one of the twelve tasks
  [ ] TODO: (works today) a hand-off list naming every possible owner fails handoffs_match_platform (exact set)
  [ ] TODO: lookup_before_refusal (added 2026-10-14): a journal whose DesignFile.get for the id ended
      error_kind "missing" -> pass; an empty journal -> fail; a flaky try then a "missing" retry -> pass

  Partial credit (G9, built 2026-10-14: grade.partial_score, score.json "partial"; report only):
  [ ] TODO: 3 of 4 applicable checks pass -> S_partial = 0.5 * 3/4 + 0.5 * 0 = 0.375
  [ ] TODO: all checks pass -> S_partial = 1.0;  "skip" checks are not in the denominator

  Failure modes (G10, built 2026-10-14: registry.FAILURE_MODES, checks.failure_mode_of):
  [ ] TODO: every built-in and pack check has a failure mode from FAILURE_MODES
  [ ] TODO: Registry().check("x", failure_mode="bad_luck") raises ValueError
  [ ] TODO: a failed check shows up in score.json "fail_modes" under its mode; report.md counts failed checks by mode

  Tool-call budget (G11, built 2026-10-14: checks.check_tool_call_budget, mutant duplicate_read):
  [ ] TODO: a journal with two identical successful reads (same tool, same args) -> tool_call_budget fails
  [ ] TODO: the same read again AFTER a write -> passes (the state may have changed)
  [ ] TODO: a failed read retried with the same args -> passes
  [ ] TODO: in a replay (replay.json has replay_start_seq) only calls from that seq on are counted

  Cost against accuracy (G12, built 2026-10-15: agentkit/harness/pareto.py, docs/COST_ACCURACY.md):
  [ ] TODO: load_arm on a hand-made batch: runs, passed, cost, cost per pass, escalations (runs with calls on more
      than one model) and answer sources come out as built
  [ ] TODO: a run with no score.json counts as not passed
  [ ] TODO: frontier(): an arm with a higher pass rate and a lower cost beats another; two equal arms are both on it
  [ ] TODO: a template-only arm (manifest skip_llm = true) is labelled "template only (no LLM)" and costs 0

More use cases (added 2026-10-16, to write by hand):
  [x] pass_at_k(3, 2, 4) and pass_hat_k(3, 2, 0) raise ValueError (k must be 1..n)
  [ ] TODO: wilson_interval(0, 0) is (0.0, 1.0): no runs means no knowledge, not an error
  [ ] TODO: partial_score(0, 0, full=True) == 1 and partial_score(0, 0, full=False) == 0 (no applicable checks)
Hint: build a manifest by hand: {"batch_id": "b", "repeat": 3, "expected": {"t": 3}, "runs": [...]},
and results as [(entry, {"status": "pass"}), ...] -- then call score_batch(manifest, results).
For check tests, build a RunBundle by hand (see test_verifiers.py) and call the check function directly.
"""

from fractions import Fraction
import json
from pathlib import Path
import pytest

from agentkit.harness.checks import BUILTIN_CHECKS, CheckOutcome, RunBundle, check_run, check_tool_call_budget
from agentkit.harness.grade import (clustered_se, frac, overall_status, partial_score, pass_at_k, pass_hat_k,
                                    score_batch, wilson_interval)
from agentkit.registry import FAILURE_MODES, Registry
from packs.designreview.checks import check_lookup_before_refusal, check_release_gate

# Write your tests below.


def test_score_batch_three_runs_two_pass():
    """Use case: score_batch: 3 runs of one task, 2 pass -> pass_all is False and the run ids that failed are listed."""
    manifest = {
        "batch_id": "b",
        "repeat": 3,
        "expected": {"t": 3},
        "runs": [],
    }
    results = [
        ({"task_id": "t", "run_id": "r1"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r2"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r3"}, {"status": "fail"}),
    ]

    out = score_batch(manifest, results)
    row = out["tasks"]["t"]

    assert row["runs"] == 3
    assert row["passed"] == 2
    assert row["pass_all"] is False
    assert row["failed_runs"] == ["r3"]


def test_score_batch_expected_task_with_no_runs_counts_as_missing():
    """Use case: score_batch: a task in manifest["expected"] with no runs counts as missing and does not pass."""
    manifest = {
        "batch_id": "b",
        "repeat": 3,
        "expected": {"t": 3, "ghost": 2},
        "runs": [],
    }
    results = [
        ({"task_id": "t", "run_id": "r1"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r2"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r3"}, {"status": "pass"}),
    ]

    out = score_batch(manifest, results)

    # ghost has no runs, so missing == 2 and pass_all is False
    assert out["tasks"]["ghost"]["runs"] == 0
    assert out["tasks"]["ghost"]["missing"] == 2
    assert out["tasks"]["ghost"]["pass_all"] is False

    # Contrast: task t had all 3 runs pass, so pass_all is True
    assert out["tasks"]["t"]["runs"] == 3
    assert out["tasks"]["t"]["missing"] == 0
    assert out["tasks"]["t"]["pass_all"] is True

    # Total roll-up: 1 out of 2 tasks passed all runs
    assert out["total"]["tasks"] == 2
    assert out["total"]["tasks_passing_all"] == 1


def test_hedged_answer_fails_release_gate_matches_db():
    """Use case: A hedged answer ("The part is ready for release. It is not ready.") against a NOT-ready gate:
    release_gate_matches_db must fail -- an answer that lists both verdicts can't pass (G2)."""
    bundle = RunBundle(
        run_dir=Path("."),
        task={},
        graph=None,
        journal=[],
        taskrun={
            "finding": {"release_gate": {"ready": False, "critical_open": 1}},
            "claimed_answer": "The part is ready for release. It is not ready.",
        },
        before={"observations": {}},
        after={"observations": {"release_gate:f1": {"value": {"ready": False, "critical_open": 1}}}},
    )

    outcomes = check_release_gate(bundle, {"file_id": "f1"})
    statuses = {o.name: o.status for o in outcomes}

    # Finding matches the platform, so ready and critical_open pass
    assert statuses["ready"] == "pass"
    assert statuses["critical_open"] == "pass"
    # But the hedged answer triggers both says_ready and says_not_ready, failing the verdict check
    assert statuses["answer_verdict"] == "fail"

    # Contrast: clear answer stating only that it is not ready passes
    clean_bundle = RunBundle(
        run_dir=Path("."),
        task={},
        graph=None,
        journal=[],
        taskrun={
            "finding": {"release_gate": {"ready": False, "critical_open": 1}},
            "claimed_answer": "It is not ready.",
        },
        before={"observations": {}},
        after={"observations": {"release_gate:f1": {"value": {"ready": False, "critical_open": 1}}}},
    )
    clean_statuses = {o.name: o.status for o in check_release_gate(clean_bundle, {"file_id": "f1"})}
    assert clean_statuses["answer_verdict"] == "pass"


def test_pass_at_k():
    """Use case: n=3 runs, c=2 pass: pass@1 = 2/3, pass@2 = 1, pass@3 = 1."""
    assert pass_at_k(3, 2, 1) == Fraction(2, 3)
    assert pass_at_k(3, 2, 2) == 1
    assert pass_at_k(3, 2, 3) == 1


def test_pass_hat_k():
    """Use case: same task: pass^1 = 2/3, pass^2 = 1/3, pass^3 = 0."""
    assert pass_hat_k(3, 2, 1) == Fraction(2, 3)
    assert pass_hat_k(3, 2, 2) == Fraction(1, 3)
    assert pass_hat_k(3, 2, 3) == 0


def test_exact_fractions_as_strings_byte_stable():
    """Use case: values are exact fractions written as strings ("1/3"), so batch_score.json stays byte-stable
    and grade --check still says "identical"."""
    # Direct
    assert frac(Fraction(1, 3)) == "1/3"
    assert frac(Fraction(1)) == "1"
    assert frac(Fraction(0)) == "0"

    # Through score_batch
    manifest = {
        "batch_id": "b",
        "repeat": 3,
        "expected": {"t": 3},
        "runs": [],
    }
    results = [
        ({"task_id": "t", "run_id": "r1"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r2"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r3"}, {"status": "fail"}),
    ]
    out1 = score_batch(manifest, results)
    row = out1["tasks"]["t"]
    assert row["pass_at_k"] == {"1": "2/3", "2": "1", "3": "1"}
    assert row["pass_hat_k"] == {"1": "2/3", "2": "1/3", "3": "0"}
    for v in row["pass_at_k"].values():
        assert isinstance(v, str)
    for v in row["pass_hat_k"].values():
        assert isinstance(v, str)

    # Byte-stable
    out2 = score_batch(manifest, results)
    assert json.dumps(out1, sort_keys=True) == json.dumps(out2, sort_keys=True)


def test_k_out_of_range_rejected_and_not_reported():
    """Use case: k > n is rejected (or not reported), never a division by zero."""
    with pytest.raises(ValueError, match="k must be in"):
        pass_at_k(3, 2, 4)
    with pytest.raises(ValueError, match="k must be in"):
        pass_hat_k(3, 2, 4)
    with pytest.raises(ValueError, match="k must be in"):
        pass_hat_k(3, 2, 0)

    # Not reported in score_batch
    manifest = {
        "batch_id": "b",
        "repeat": 3,
        "expected": {"t": 3},
        "runs": [],
    }
    results = [
        ({"task_id": "t", "run_id": "r1"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r2"}, {"status": "pass"}),
        ({"task_id": "t", "run_id": "r3"}, {"status": "fail"}),
    ]
    out = score_batch(manifest, results)
    row = out["tasks"]["t"]
    assert set(row["pass_at_k"].keys()) == {"1", "2", "3"}
    assert "4" not in row["pass_at_k"]

