# `core/eval_framework.py` — the ground-truth axis engine

*Reading order: **7th**. Depends on `core/harness.py` (5th) for the `TaskRun` type it scores. Read `core/judge.py` (6th) first since one of `capstone_evals.py`'s axes uses it. This file itself is domain-agnostic — the actual axis *functions* live in `capstone_evals.py`, which is next in the reading order.*

## 10,000-ft view

One function, `evaluate_run(run, axes, ...)`, and one result dataclass, `EvaluationResult`. This file doesn't know anything about design review, bend radii, or AgentSwitch — it just runs a dict of named boolean-returning functions (`axes`) against a `TaskRun` and a piece of context (`ground_truth_context`), tallies pass/fail, and produces a score. The actual *meaning* of "ground truth" — what to re-fetch, what to compare — is entirely the caller's business (`capstone_evals.py`).

## Why it's written this way (intent & trade-offs)

- **Axes are plain functions in a `Dict[str, AxisFn]`, not a class hierarchy.** No `Axis` base class to subclass, no registration decorator — just `{"name": callable}`. The trade-off: there's no enforced metadata per axis (no description, no severity, no "is this axis required vs. advisory" flag) — everything about an axis's meaning lives in its docstring and its name, which is fine for five axes and would get unwieldy for fifty.
- **Every axis receives `ground_truth_context`, not just the `TaskRun`.** This is the file's central design decision, and it's what makes ground-truth checking possible at all: an axis isn't limited to inspecting what the agent *claimed* — it's handed a live client (or, in `rescore.py`'s case, whatever `evaluate_run` is called with) it can use to independently ask "what does the platform actually say right now." Without this parameter, every axis would be structurally forced into "grep the agent's own prose," which is exactly the anti-pattern this file exists to avoid (see the module docstring's explicit callout of the first draft's `"bend radius" in analysis.lower()` approach).
- **An axis that raises is scored `False`, not propagated as an error that aborts the whole evaluation.** The `try/except Exception` inside `evaluate_run`'s loop is deliberate: if one axis's ground-truth fetch fails (e.g. a network blip re-fetching `DesignVersion.list`), that shouldn't hide the *other* four axes' results behind an unrelated stack trace. The trade-off: a genuinely broken axis (a bug in the axis function itself, not a transient network issue) looks identical to a legitimately-failed axis in the output — `axes_scores["some_axis"] = False` either way. There's no separate "errored" state distinct from "failed," so debugging *why* an axis is `False` currently requires reading logs or re-running with a debugger, not just reading `EvaluationResult`.
- **`passing_threshold` defaults to `0.80`, applied to a simple `passed_count / len(axes)` ratio — every axis weighted equally.** There's no way, today, to mark one axis as more important than another (e.g. "the governance/release-readiness axis failing should fail the whole run regardless of the other four" — which is arguably the *most* important axis for this specific domain, since a false "ready for release" is the highest-stakes failure mode). This is an accepted simplification, not a considered design stance — worth revisiting if a future axis set has genuinely unequal stakes.
- **`feedback_notes` lists the actual failed axis names** (fixed during this session — the original version just said "All rubric axes passed."/"Failed one or more rubric axes." regardless of which ones, which is a real bug: it's possible to score `0.8` (4/5 axes passing) and still see the misleading blanket message). The current version's `f"Failed axes: {', '.join(failed_axes)}."` is a direct, minimal fix — it doesn't yet explain *why* each axis failed, just which ones did.

## What it does (walkthrough)

1. **`AxisFn`** — a type alias, `Callable[[TaskRun, Any], bool]`, documented rather than enforced (Python doesn't check this at runtime — an axis function with the wrong signature would only fail when actually called).
2. **`EvaluationResult`** — the output shape (see Types below).
3. **`evaluate_run(run, axes, ground_truth_context, passing_threshold, run_path)`**:
   - Loops over every `(name, fn)` pair in `axes`, calls `fn(run, ground_truth_context)`, coerces to `bool`, catches any exception into `False`.
   - Computes `score = passed_count / len(axes)` (rounded to 2 decimals; `0.0` if `axes` is empty rather than a `ZeroDivisionError`).
   - Computes `passed = score >= passing_threshold`.
   - Builds the failed-axes list and the `feedback_notes` string from it.
   - Returns an `EvaluationResult`, threading through `run.task_id`/`run.prompt`/`run.seconds` from the scored `TaskRun` and the optional `run_path` the caller supplies (for traceability back to the exact file on disk that was scored).

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `AxisFn` | `Callable[[TaskRun, Any], bool]` (type alias) | Documents the expected shape of every axis function; see `capstone_evals.py::AXES` for the real dict of these. |
| `EvaluationResult.task_id` / `.prompt` | `str` | Copied straight from the scored `TaskRun`, so an `EvaluationResult` is self-describing without needing the original run alongside it. |
| `EvaluationResult.passed` | `bool` | `score >= passing_threshold`. |
| `EvaluationResult.score` | `float` | `passed_count / len(axes)`, rounded to 2 decimals. |
| `EvaluationResult.axes` | `Dict[str, bool]` | Per-axis pass/fail — the most useful field for debugging *which* thing failed. |
| `EvaluationResult.latency_sec` | `float` | Copied from `run.seconds` — the agent's wall-clock time, not the scoring pass's time (scoring itself, especially the ground-truth re-fetch axes, also takes real time, which is *not* captured anywhere in this result). |
| `EvaluationResult.feedback_notes` | `str` | Human-readable one-liner; see the trade-off note above about its limited detail. |
| `EvaluationResult.run_path` | `Optional[str]` | The path to the raw `TaskRun` JSON on disk, if the caller provided one — lets a report say "here's exactly which run this score came from." |

## Function/method reference

| Function | Inputs → Output | Notes |
|---|---|---|
| `evaluate_run(run, axes, ground_truth_context=None, passing_threshold=0.80, run_path=None)` | → `EvaluationResult` | The only function in the file. Deterministic *given* deterministic axis functions and a stable `ground_truth_context` — but most of `capstone_evals.py`'s axes hit a live, mutable platform, so two calls to `evaluate_run` on the *same* saved `run` can legitimately produce different scores if the platform's state changed in between (e.g. someone else fixes the missing drain hole and the release-readiness axis flips). This is a feature, not a bug, for this project's actual use case — but worth knowing when debugging "why did rescoring give a different answer than the original run." |

## How to test it

Fully unit-testable with fake axis functions and a fake `TaskRun` — no network, no LLM, no real ground truth needed to test *this* file's logic (the real ground-truth fetching is `capstone_evals.py`'s concern, tested separately).

```python
def always_pass(run, ctx): return True
def always_fail(run, ctx): return False
def always_raises(run, ctx): raise RuntimeError("boom")

def test_all_pass_scores_one():
    run = TaskRun(task_id="t", prompt="p")
    result = evaluate_run(run, {"a": always_pass, "b": always_pass})
    assert result.score == 1.0
    assert result.passed is True
    assert result.feedback_notes == "All rubric axes passed."

def test_mixed_scores_and_names_failed_axes():
    run = TaskRun(task_id="t", prompt="p")
    result = evaluate_run(run, {"a": always_pass, "b": always_fail}, passing_threshold=0.80)
    assert result.score == 0.5
    assert result.passed is False
    assert "b" in result.feedback_notes

def test_raising_axis_counts_as_failed_not_aborted():
    run = TaskRun(task_id="t", prompt="p")
    result = evaluate_run(run, {"a": always_pass, "broken": always_raises})
    assert result.axes["broken"] is False
    assert result.axes["a"] is True  # the other axis's result survives

def test_empty_axes_dict_scores_zero_not_crash():
    run = TaskRun(task_id="t", prompt="p")
    result = evaluate_run(run, {})
    assert result.score == 0.0

def test_ground_truth_context_is_passed_through():
    seen = {}
    def spy_axis(run, ctx):
        seen["ctx"] = ctx
        return True
    run = TaskRun(task_id="t", prompt="p")
    evaluate_run(run, {"a": spy_axis}, ground_truth_context="the-context-object")
    assert seen["ctx"] == "the-context-object"

def test_passing_threshold_boundary_is_inclusive():
    run = TaskRun(task_id="t", prompt="p")
    result = evaluate_run(run, {"a": always_pass, "b": always_pass, "c": always_pass, "d": always_pass, "e": always_fail}, passing_threshold=0.80)
    assert result.score == 0.8
    assert result.passed is True  # >= , not >
```

## Tests that should be added for stability

In priority order:

1. **All six tests sketched above** — this file has the cleanest, cheapest-to-achieve full test coverage of any file in the project (everything is fakeable in one line), and currently has none.
2. **A test that pins down the `>=` (not `>`) threshold semantics explicitly** — the boundary test above is the kind of thing that silently breaks if someone "cleans up" the comparison operator during a refactor without realizing `0.80` is meant to be inclusive.
3. **A regression test for the `feedback_notes` bug fixed this session** — a test asserting that a `0.8`-scoring, non-all-passing run's `feedback_notes` names the actual failed axis rather than claiming "All rubric axes passed," so this specific bug class can't silently come back.
4. **A test that distinguishes "axis failed" from "axis errored"** would require a design change (e.g. `EvaluationResult` gaining an `axis_errors: Dict[str, str]` field alongside `axes`) — worth doing given how often a ground-truth axis's failure mode in practice (a network error mid-fetch) is indistinguishable today from a genuine scoring failure. Write the test *first* (asserting the new field exists and is populated correctly) to drive the implementation.
5. **A test for weighted/critical axes**, once that feature exists — today there's nothing to test since every axis is equal-weighted, but if `matches_real_release_readiness`-style "this one axis failing should fail the whole run" logic is ever added (see the trade-off note above), it needs a test proving a single critical-axis failure overrides an otherwise-passing score.
