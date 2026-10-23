"""
Offline tests for golden values: exact answers a person checked once in the seed data, written as [[golden]]
blocks in the task files and checked on every run (agentkit/harness/goldens.py, the built-in check "golden",
the mutant "golden_broken", scripts/make_goldens.py).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/explainers/agentkit_harness.md ("Golden values"); docs/CHANGES.md, 2026-10-16. The feature
exists today.

Use cases to cover:
  The format (GoldenSpec):
  [x] a block with no comparison, or with two (equals and length), is rejected
  [ ] TODO: equals = false and length = 0 count as given (not as "missing")
  [x] a path that does not start with finding / route / tools / answer is rejected
  [x] answer_contains with a path is rejected; field with equals is rejected
  [x] a note that still starts with "CHECK:" is rejected (an unchecked golden cannot be committed)
  [ ] TODO: TaskDef(...).to_dict() keeps only the fields that were written, and TaskDef(**d) loads it back

  Paths (resolve):
  [x] "finding.revision_delta.versions[2].rev" and "...versions[rev=C].version_number" find the same item
  [x] an index past the end, an unknown key and a selector that matches nothing give "missing"

  Comparisons (compare):
  [x] equals: 3 == 3 holds; false does not equal 0
  [x] values with field: [{"text": "2.0 mm"}, {"text": "3.0 mm"}] holds for ["3.0 mm", "2.0 mm"]; an extra item
      fails and the message names it
  [ ] TODO: only: ["DesignFile.get", "DesignFile.get"] holds for only = ["DesignFile.get"]; another tool fails
  [ ] TODO: answer_contains is case-insensitive; a missing text fails and is named
  [ ] TODO: each failure message has the expected and the observed value

  The check and the mutant:
  [x] a task without goldens gives one "skip"; a task with two goldens gives two outcomes
  [ ] TODO: two goldens on the same path give two different outcome names
  [x] the mutant "golden_broken" makes a passing golden fail, for a finding path, a route path, "tools" and
      answer_contains; it gives None for a task with no goldens

  The helper:
  [ ] TODO: scripts/make_goldens.py prints blocks that tomllib can parse; each has note = "CHECK: ..."

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: assign() on a missing path returns False and changes nothing; on "[rev=C]" it replaces that item only
  [ ] TODO: resolve() on a path with text left over after the last part ("finding.x!") gives "missing"
  [ ] TODO: contains on a string value: "3.0 mm" in "2.0 mm -> 3.0 mm" holds; on a number value it fails with a message
Hint: build a RunBundle(run_dir=Path("."), task={"golden": [...]}, taskrun={...}, graph=None, journal=[...],
before={}, after={}) and call agentkit.harness.checks.check_golden(bundle, {}). The mutant is
next(m for m in agentkit.harness.mutants.BUILTIN_MUTANTS if m.name == "golden_broken"); call m.apply(bundle).
"""

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentkit.harness.checks import RunBundle, check_golden
from agentkit.harness.goldens import GoldenSpec, compare, resolve, view
from agentkit.harness.mutants import BUILTIN_MUTANTS
from agentkit.harness.tasks import TaskDef

# Write your tests below.

VERSIONS = [{"version_number": 1, "rev": "A"}, {"version_number": 2, "rev": "B"}, {"version_number": 3, "rev": "C"}]
DATA = {"finding": {"revision_delta": {"versions": VERSIONS}}, "route": {}, "tools": [], "answer": ""}


def bundle(goldens, finding=None, answer="") -> RunBundle:
    return RunBundle(run_dir=Path("."), task={"golden": goldens}, graph=None, journal=[],
                     taskrun={"finding": finding or {}, "claimed_answer": answer}, before={}, after={})


@pytest.mark.parametrize("fields", [
    {"path": "finding.x"},                                   # no comparison
    {"path": "finding.x", "equals": 1, "length": 1},         # two comparisons
    {"path": "graph.x", "equals": 1},                        # root not finding/route/tools/answer
    {"path": "finding.x", "answer_contains": ["a"]},         # answer_contains takes no path
    {"path": "finding.x", "equals": 1, "note": "CHECK: confirm on the platform"},  # never checked
])
def test_bad_golden_blocks_are_rejected(fields):
    with pytest.raises(ValidationError):
        GoldenSpec(**fields)


def test_index_and_selector_find_the_same_item_and_bad_paths_are_missing():
    by_index = resolve(DATA, "finding.revision_delta.versions[2].version_number")
    by_rev = resolve(DATA, "finding.revision_delta.versions[rev=C].version_number")
    assert by_index == by_rev == 3
    for bad in ("finding.revision_delta.versions[9].rev", "finding.nope", "finding.revision_delta.versions[rev=Z]"):
        assert compare(GoldenSpec(path=bad, equals=1), DATA) == f"no value at {bad}"


def test_compare_equals_is_strict_and_values_names_the_extra_item():
    assert compare(GoldenSpec(path="finding.revision_delta.versions[2].version_number", equals=3), DATA) is None
    falsy = {"finding": {"ready": False}}
    problem = compare(GoldenSpec(path="finding.ready", equals=0), falsy)
    assert problem and "expected 0" in problem and "observed false" in problem
    q = {"finding": {"q": [{"text": "2.0 mm"}, {"text": "3.0 mm"}]}}
    assert compare(GoldenSpec(path="finding.q", values=["3.0 mm", "2.0 mm"], field="text"), q) is None
    q["finding"]["q"].append({"text": "9.9 mm"})
    problem = compare(GoldenSpec(path="finding.q", values=["3.0 mm", "2.0 mm"], field="text"), q)
    assert "9.9 mm" in problem


def test_check_skips_without_goldens_and_the_mutant_breaks_a_passing_golden():
    assert [o.status for o in check_golden(bundle([]), {})] == ["skip"]

    golden = {"path": "finding.release_gate.ready", "equals": False}
    good = bundle([golden], finding={"release_gate": {"ready": False}})
    assert [o.status for o in check_golden(good, {})] == ["pass"]

    mutant = next(m for m in BUILTIN_MUTANTS if m.name == "golden_broken")
    broken = mutant.apply(copy.deepcopy(good))
    assert [o.status for o in check_golden(broken, {})] == ["fail"]
    assert mutant.apply(bundle([])) is None
