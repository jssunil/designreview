"""
Offline tests for the official harness runner with our agent inside: harness/runner.py (--sim / --fault /
--out), harness/agent_tasks.py (one results.json entry per task and per check) and
scripts/check_results_json.py (the Release 8.1 results.json rules).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: README.md ("Evaluated checks"); docs/CHANGES.md, 2026-10-16; the admin's harness reviews (an
exception must be a failed check with its line; only tasks that ran belong in results.json). All of this
exists today.

Use cases to cover:
  Entries (agent_tasks.entries_for, with hand-made graded results -- no batch needed):
  [x] one passing task with 3 checks -> 1 task entry + 3 check entries; ids "task:<id>" and
      "check:<id>:<check>:<name>"
  [x] a check with status "skip" gets no entry (only what ran is reported)
  [x] a check with status "fail", "error" or "drift" -> passed false, and the evidence starts with the status
  [x] a failed task's score is its partial credit (score.json "partial"); its evidence names the failed checks
  [x] a run with no score.json -> a failed task entry that says it was not graded
  [ ] TODO: with little room (room=4): every task entry is kept, then the failed checks, then the passing ones

  Errors (agent_tasks.run_agent_tasks):
  [x] run_batch raising ValueError("x") -> exactly one failed entry "<instance>:agent_tasks", whose evidence
      has "ValueError: x" and "(at <file>:<line>)" -- never a lost result, never a pass
  [ ] TODO: an instance that no task lists -> one failed entry that says so

  The runner offline (python -m harness.runner --instance <i> --sim packs/designreview/fixtures --out <f>):
  [ ] TODO: keystone: exit 0; results.json has the 4 agent tasks and their checks, all passed
  [ ] TODO: with --fault "fail_always:endpoint.designreview.release_readiness:flaky" on suryodaya: exit 1, and the
      failed entries include check:...:release_gate_matches_db:ready with "agent None, platform False"
  [ ] TODO: --sim skips the platform smoke checks and needs no login and no network (AGENTKIT_OFFLINE=1)

  The validator (scripts/check_results_json.py: problems(data, instance, min_agent_tasks, expect)):
  [x] a valid file -> no problems; main([...]) returns 0
  [x] "passed": "true" (a string), score 1.5, score True, a duplicate id, 0 or 201 entries -> one problem each
  [x] a failed entry with empty evidence -> a problem; a passed entry with score 0.5 -> a problem
  [x] --instance suryodaya and an id "keystone:x" -> a problem
  [ ] TODO: --expect some-fail on an all-pass file -> a problem (a planted fault was not reported)

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: _types: {"type": ["string", "null"]} -> {"string", "null"}; {"anyOf": [{"type": "integer"}, {"type":
      "string"}]} -> both; a non-dict -> empty set
  [ ] TODO: a task evidence longer than 300 characters ends with "..." and has no newlines (_short)
  [ ] TODO: problems(): a file with no "summary" and a "tasks" that is not a list -> both reported
Hint: harness/ is a package: `from harness.agent_tasks import entries_for, run_agent_tasks`. Pass
run_agent_tasks a small recorder function(task_id, title, passed, evidence, score=None) that appends to a
list. scripts/ is not a package: load check_results_json.py with importlib.util.spec_from_file_location.
For the runner itself, use subprocess.run([sys.executable, "-m", "harness.runner", ...]) with
AGENTKIT_OFFLINE=1 in the environment and --out in tmp_path.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

import agentkit.harness.batch as batch
from harness.agent_tasks import entries_for, run_agent_tasks

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "packs" / "designreview" / "fixtures"


def load_validator():
    spec = importlib.util.spec_from_file_location("check_results_json", ROOT / "scripts" / "check_results_json.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score(status, checks, partial="1"):
    return {
        "status": status,
        "partial": partial,
        "checks": [{"check": c, "name": n, "status": s, "detail": d} for c, n, s, d in checks],
    }


def test_one_entry_per_task_and_per_check_that_ran(tmp_path):
    results = [
        (
            {"task_id": "t1", "run_id": "r1"},
            score(
                "fail",
                [("a", "x", "pass", "ok"), ("b", "y", "fail", "agent None, platform 1"), ("c", "z", "skip", "n/a")],
                partial="1/2",
            ),
        )
    ]
    rows = entries_for("suryodaya", results, tmp_path, {}, room=200)
    assert [r["id"] for r in rows] == ["task:t1", "check:t1:a:x", "check:t1:b:y"]   # no entry for the skip
    task, failed_check = rows[0], rows[2]
    assert task["passed"] is False and task["score"] == 0.5                        # partial credit
    assert "b.y=fail" in task["evidence"]
    assert failed_check["passed"] is False and failed_check["evidence"].startswith("fail")


def test_a_run_with_no_score_is_a_failed_task_that_says_so(tmp_path):
    rows = entries_for("suryodaya", [({"task_id": "t1", "run_id": "r1"}, None)], tmp_path, {}, room=200)
    assert len(rows) == 1 and rows[0]["passed"] is False and rows[0]["score"] == 0.0
    assert "not graded" in rows[0]["evidence"]


def test_an_exception_in_the_batch_is_one_failed_entry_with_its_line(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("x")

    monkeypatch.setattr(batch, "run_batch", boom)
    rows = []
    run_agent_tasks(
        "suryodaya",
        lambda task_id, title, passed, evidence, score=None: rows.append((task_id, passed, evidence)),
        sim=FIXTURES,
    )
    assert len(rows) == 1
    task_id, passed, evidence = rows[0]
    assert task_id == "agent_tasks" and passed is False
    assert "ValueError: x" in evidence and "(at " in evidence


@pytest.mark.parametrize("bad, message", [
    ({"passed": "true"}, "not a strict boolean"),
    ({"score": 1.5}, "not a number from 0.0 to 1.0"),
    ({"passed": False, "score": 0.0, "evidence": " "}, "failed without evidence"),
    ({"passed": True, "score": 0.5}, "passed but its score is 0.5"),
    ({"id": "keystone:x"}, "does not start with 'suryodaya:'"),
])
def test_validator_reports_each_broken_rule(bad, message):
    entry = {"id": "suryodaya:x", "title": "t", "passed": True, "score": 1.0, "evidence": "e", **bad}
    problems = load_validator().problems({"tasks": [entry], "summary": "s"}, "suryodaya")
    assert any(message in p for p in problems), problems
    good = {"id": "suryodaya:x", "title": "t", "passed": True, "score": 1.0, "evidence": "e"}
    assert load_validator().problems({"tasks": [good], "summary": "s"}, "suryodaya") == []

