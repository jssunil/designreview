"""
Offline tests for the run record and the harness grader (raw run first, grade later).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] TaskRun.open_in(run_dir) writes taskrun.json with ended="running" BEFORE any work
  [x] A saved run loads back with the same task_id, prompt, steps, finding and claimed_answer
  [x] A run that crashed after open_in() still has a record, and grades as "fail" (run_completed)
  [x] add_step records kind / target / ok / detail (and status / error_kind) in order
  [ ] TODO: tool_calls_policy fails a dry run whose tool_journal.jsonl shows a write tool
  [ ] TODO: tool_calls_policy fails a write run that called a tool outside RunBundle.write_tools, passes one inside it
  [ ] TODO: run_task(..., write=True) stays a dry run unless the task sets allow_writes = true
  [ ] TODO: batch --write refuses --sim; --keep-writes needs --write
  [ ] TODO: Grading the same saved run folder twice gives identical score.json (deterministic)
  [x] overall_status: any fail/error -> fail; only drift -> drift; else pass
  [x] agentkit.clock.now_utc() returns AGENTKIT_FROZEN_NOW when set; a value with no timezone raises ValueError
  [x] frozen_now(iso) pins the clock inside the block and restores it afterwards
  [ ] TODO: run_batch(..., batch_id="ci") refuses a second batch with the same id; "../x" is rejected
  [ ] TODO: run_batch(..., repeat=2) gives run ids ending -r1 / -r2 and manifest expected_runs == tasks x tenants x 2
  [ ] TODO: score_batch(): a task with one failed (or missing) repeat has pass_all False
  [ ] TODO: report.md carries "**P of T tasks pass on every run.**"
  [ ] TODO: rescore_check(batch_dir) is (True, []) right after grading, and reports a hand-edited score.json
  [ ] TODO: run_task creates an empty tool_journal.jsonl even when the run makes no tool call
Since 2026-10-14 to 2026-10-16:
  [ ] TODO: specs_for(task) lists the built-in checks run_completed, tool_calls_policy, answer_present,
      tool_call_budget, injection_resisted, unexpected_state_change and golden before the task's verifiers
  [ ] TODO: a routed task (plan = "route") saves taskrun.route = {plan, params, why}; a resume or a replay of that run
      uses the saved decision and does not ask the router again
  [ ] TODO: TaskDef with [[golden]] blocks: task.to_dict() keeps only the golden fields that were written, and
      TaskDef(**task.to_dict()) loads back (see test_goldens.py for the golden check itself)
  [ ] TODO: the narrate step in taskrun.json has the provider and the model that wrote the answer, and each attempt's
  [ ] TODO: report.md's Performance table has a "Models (provider:model)" column read from llm_ledger.json
  [ ] TODO: python -m agentkit.harness.pareto on two hand-made batches: the cheaper arm with the same pass rate is on
      the frontier and the other is not (agentkit/harness/pareto.py)
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: grade_run(run_dir, write=False) returns the score but writes no score.json
  [ ] TODO: parse_instant("2026-10-16T10:00:00+05:30") gives 04:30 UTC; "yesterday" raises ValueError
  [ ] TODO: frozen_now nested twice: the inner value holds inside, the outer value comes back after it
Hint: use pytest's tmp_path as the run folder; write taskrun.json / tool_journal.jsonl /
ground_truth_after.json yourself, then call agentkit.harness.grade.grade_run(tmp_path).
For batch-level cases, run the offline batch: SimSession("packs/designreview/fixtures", [], reg.read_only)
-> run_batch(tasks, runs_dir=tmp_path, reader_factory=sim.reader_for, agent_runner=sim.run_agent,
fresh_reader_per_task=True, skip_llm=True, batch_id=..., repeat=...) -> grade_batch(batch_dir).
"""

import datetime as dt
import json

import pytest

from agentkit.clock import FROZEN_ENV, frozen_now, now_utc, parse_instant
from agentkit.harness.checks import CheckOutcome
from agentkit.harness.grade import grade_run, overall_status
from core.harness import Step, TaskRun


def test_open_in_saves_running_first_and_the_record_loads_back(tmp_path):
    run = TaskRun("t01", "What changed between rev B and rev C?")
    run.open_in(tmp_path)
    saved = json.loads((tmp_path / "taskrun.json").read_text(encoding="utf-8"))
    assert saved["ended"] == "running"            # on disk before any work

    run.add_step("tool_call", "DesignFile.get", True, {"id": "f1"})
    run.add_step("answer", "template", False, "no data", error_kind="missing")
    run.finding = {"outcome": "answered"}
    run.claimed_answer = "Rev C widened the radius."
    run.save_in(tmp_path)

    back = TaskRun.load(tmp_path)
    assert (back.task_id, back.prompt, back.finding, back.claimed_answer) == (
        "t01", run.prompt, {"outcome": "answered"}, "Rev C widened the radius."
    )
    assert [(s.kind, s.target, s.ok) for s in back.steps] == [
        ("tool_call", "DesignFile.get", True),
        ("answer", "template", False),
    ]
    assert back.steps[1].error_kind == "missing"


def test_a_run_that_crashed_after_open_in_grades_fail(tmp_path):
    TaskRun("t01", "q").open_in(tmp_path)          # then the process "dies": finish() is never called
    (tmp_path / "tool_journal.jsonl").write_text("", encoding="utf-8")
    score = grade_run(tmp_path, write=False)
    assert score["status"] == "fail"
    completed = [c for c in score["checks"] if c["check"] == "run_completed"]
    assert completed and completed[0]["status"] == "fail"


@pytest.mark.parametrize("statuses, expected", [
    (["pass", "drift", "fail"], "fail"),
    (["pass", "error"], "fail"),
    (["pass", "drift", "skip"], "drift"),
    (["pass", "skip"], "pass"),
])
def test_overall_status(statuses, expected):
    assert overall_status([CheckOutcome("c", f"n{i}", s, "") for i, s in enumerate(statuses)]) == expected


def test_clock_env_value_frozen_block_and_timezone_rule(monkeypatch):
    monkeypatch.setenv(FROZEN_ENV, "2026-10-16T10:00:00+05:30")
    assert now_utc() == dt.datetime(2026, 10, 16, 4, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setenv(FROZEN_ENV, "2026-10-16T10:00:00")      # no timezone
    with pytest.raises(ValueError):
        now_utc()

    monkeypatch.delenv(FROZEN_ENV)
    with frozen_now("2020-01-01T00:00:00+00:00"):
        assert now_utc().year == 2020
    assert now_utc().year >= 2026                              # restored after the block


# --- Bharath: behaviours not covered above ---

def test_grade_run_writes_score_json(tmp_path):
    # Create a taskrun.json and tool_journal.jsonl
    (tmp_path / "taskrun.json").write_text("{}")
    (tmp_path / "tool_journal.jsonl").write_text("")

    # if write=True, score.json should be created
    grade_run(tmp_path, write=True)

    # verify score.json
    assert (tmp_path / "score.json").exists()


def test_grade_run_dont_write_score_json(tmp_path):
    # Create a taskrun.json and tool_journal.jsonl
    (tmp_path / "taskrun.json").write_text("{}")
    (tmp_path / "tool_journal.jsonl").write_text("")

    # if write=False, score.json should not be created
    grade_run(tmp_path, write=False)

    # verify if its created or not.
    assert not (tmp_path / "score.json").exists()


def test_env_variable_wins(monkeypatch):
    # the environment variable wins over frozen_now.
    monkeypatch.setenv("AGENTKIT_FROZEN_NOW", "2026-10-16T10:00:00+05:30")
    assert now_utc() == dt.datetime(2026, 10, 16, 4, 30, tzinfo=dt.timezone.utc)
    with frozen_now("2026-10-16T12:00:00+05:30"):
        assert now_utc() == dt.datetime(2026, 10, 16, 4, 30, tzinfo=dt.timezone.utc)


def test_frozen_now_nested_restores_outer(monkeypatch):
    monkeypatch.delenv("AGENTKIT_FROZEN_NOW", raising=False)
    with frozen_now("2026-10-16T12:00:00+05:30"):
        assert now_utc() == dt.datetime(2026, 10, 16, 6, 30, tzinfo=dt.timezone.utc)
        with frozen_now("2026-10-16T14:00:00+05:30"):
            assert now_utc() == dt.datetime(2026, 10, 16, 8, 30, tzinfo=dt.timezone.utc)
        # After the inner frozen_now block, the outer frozen_now should be restored
        assert now_utc() == dt.datetime(2026, 10, 16, 6, 30, tzinfo=dt.timezone.utc)

def test_parse_instant_without_timezone_raises():
    with pytest.raises(ValueError, match="timezone"):
        parse_instant("2026-10-16T10:00:00")  # no timezone

def test_parse_instant_converts_to_utc():
    # should parse with UTC offset correctly
    instant = parse_instant("2026-10-16T10:00:00+05:30")
    assert instant.utcoffset() == dt.timedelta(0)
    assert instant == dt.datetime(2026, 10, 16, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))

def test_parse_instant_not_datetime_string():
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_instant("not-a-datetime")