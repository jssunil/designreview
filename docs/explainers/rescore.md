# `rescore.py` — free re-scoring of saved runs

*Reading order: **10th, last**. Depends on everything before it (`as_client.py`, `core/eval_framework.py`, `core/harness.py`, `capstone_evals.py`). This is the shortest file in the project and the one that only makes sense once you understand why `capstone_agent.py` saves a `TaskRun` to disk *before* anyone scores it.*

## 10,000-ft view

A CLI script: load every saved `TaskRun` from `proofs/runs/`, re-run the axis functions from `capstone_evals.py` against each one, write the results to `proofs/results.json`. No agent is re-run, no LLM synthesis call is repeated — the only new work is re-fetching ground truth from the live platform (cheap and fast) and, optionally, one LLM-judge call per run if `--with-judge` is passed.

## Why it's written this way (intent & trade-offs)

- **This file exists to make a scoring bug free to fix.** The provenance note names the exact motivating incident from `S18Code`: a scorer bug shipped wrong there and could only be corrected by burning more (paid) compute re-running the agent, because the original design didn't separate "the agent's run" from "how that run gets graded." This project's `TaskRun.save()`/`.load()` round-trip (see `core/harness.py`) is what makes `rescore.py` possible at all — without it, fixing the `axis_matches_real_release_readiness` substring bug found during this session would have required re-running `capstone_agent.py` (and paying for another Gemini call) just to verify the fix. Instead, it was verified with `python rescore.py` against runs already on disk.
- **The LLM-judge axis is excluded by default (`--with-judge` opt-in).** This is the one axis that *isn't* free to re-run — it costs a real LLM call and can (in principle) return a different verdict on a re-judge of the exact same text, since LLM judging isn't perfectly deterministic even at `temperature=0.0`. Excluding it by default keeps the common case (`python rescore.py`) genuinely free and deterministic; `--with-judge` is there for when you specifically want to also re-check that one axis.
- **`AXES` is imported directly from `capstone_evals.py`, not redefined here.** This is what guarantees a rescored run is graded by the *exact same* logic as a freshly-scored one — there's no separate "rescoring axis set" that could drift out of sync with the live scoring path. The trade-off: `rescore.py` has zero domain knowledge of its own; every axis-related decision (including which one is the LLM-calling one to exclude, hardcoded by name as `"judged_reasoning_quality"`) has to match `capstone_evals.py` exactly, by string, with no compile-time check that the name is still correct.
- **Still requires a live client and live network access, just not a live agent run.** `main()` unconditionally logs in and initializes MCP — "rescore" means "skip the agent and the paid LLM synthesis call," not "skip the network entirely." This is a deliberate, correctly-scoped claim: the ground-truth axes' whole point is re-fetching *real* platform state, which by definition requires talking to the platform, even during a "free" rescore.
- **Results are always overwritten in full** (`RESULTS_PATH.write_text(...)`, not appended) — every `rescore.py` invocation re-scores *every* saved run under `proofs/runs/`, not just new ones since the last invocation. Simple and correct, but means `results.json` doesn't accumulate a history of scoring attempts over time — only the most recent rescoring pass is ever visible.

## What it does (walkthrough)

1. **Argument parsing**: `--tenant` (default `suryodaya`), `--with-judge` (a flag, default off).
2. **Client setup**: constructs and logs in an `AgentSwitchClient`, runs the MCP handshake — the one client shared across every rescored run's axis evaluation.
3. **Axis selection**: copies `capstone_evals.AXES`, removes `"judged_reasoning_quality"` unless `--with-judge` was passed.
4. **Discovery**: globs every `*.json` file under `proofs/runs/`, sorted (so output order is stable/reproducible across invocations, not filesystem-order-dependent). Prints a clear message and exits early if none exist.
5. **Scoring loop**: for each saved file, `TaskRun.load(path)` → `evaluate_run(run, axes, ground_truth_context=client, run_path=path)` → append to `results`, print a one-line summary immediately (so a long rescoring pass shows progress incrementally rather than going silent until the very end).
6. **Write**: dumps the full `results` list as pretty JSON to `proofs/results.json`, prints a final count.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `PROOFS_DIR` | `Path` (module constant) | `proofs/runs/` — must match `capstone_agent.py::PROOFS_DIR` exactly; there is no shared constant between the two files, just two independently-written identical `Path` expressions (a small duplication risk — if one file's `proofs/runs` path ever changed, the other wouldn't automatically follow). |
| `RESULTS_PATH` | `Path` (module constant) | `proofs/results.json` — the one output file, always fully overwritten. |
| `args.tenant` / `args.with_judge` | `str` / `bool` | Parsed CLI flags. |
| `axes` (local var in `main`) | `Dict[str, AxisFn]` | A *copy* of `capstone_evals.AXES` (`dict(AXES)`) — copied specifically so `.pop(...)` doesn't mutate the shared module-level `AXES` dict that `capstone_evals.py` itself (and any other importer) also holds a reference to. |

## Function/method reference

| Function | Inputs → Output | Notes |
|---|---|---|
| `main()` | none → `None` (side effects: prints, writes `results.json`) | The only function in the file; everything is sequential, no helper functions extracted — appropriate for a script this short and linear. |

## How to test it

The pure logic here (axis-set selection, sorting, result-list building) is thin enough to test with fakes; the "actually talks to the live platform" parts need the same fixture/mocking approach as `as_client.py` and `capstone_evals.py`.

```python
def test_with_judge_flag_keeps_judge_axis(monkeypatch):
    # simulate argv, patch AgentSwitchClient to a fake, assert
    # "judged_reasoning_quality" is present in the axes dict passed to evaluate_run
    ...

def test_without_judge_flag_excludes_judge_axis_by_default():
    from capstone_evals import AXES
    axes = dict(AXES)
    axes.pop("judged_reasoning_quality", None)
    assert "judged_reasoning_quality" not in axes
    assert set(axes.keys()) == set(AXES.keys()) - {"judged_reasoning_quality"}

def test_no_saved_runs_exits_cleanly(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("rescore.PROOFS_DIR", tmp_path)  # empty directory
    # main() should print "No saved runs found..." and return without writing results.json

def test_results_are_sorted_by_filename(tmp_path):
    # write two TaskRun files with filenames that sort differently from
    # creation order, confirm scoring processes them in sorted() order
    ...

def test_results_json_is_fully_overwritten_not_appended(tmp_path):
    # run main() twice with a different set of saved runs each time,
    # assert the second results.json only contains the second run's entries
    ...
```

## Tests that should be added for stability

In priority order:

1. **`test_without_judge_flag_excludes_judge_axis_by_default`** — this is cheap to write right now (no mocking needed at all) and directly protects the "rescoring is free by default" guarantee this whole file exists for.
2. **A test that `dict(AXES)` is truly a copy, not a shallow-reference gotcha** — `.pop()` on a shallow copy of a dict is safe (it doesn't mutate the original dict's *keys*, only the copy's), but this is exactly the kind of assumption worth pinning down explicitly given how much this file's correctness depends on *not* mutating the shared `capstone_evals.AXES`.
3. **A test for the "no saved runs" early-exit path** — currently the only thing tested manually; deserves an automated test asserting it prints the expected message and does *not* attempt to write `results.json` (as opposed to writing an empty list, which would be a different, worse behavior — silently producing a `results.json` that looks like "zero runs scored zero" rather than "no runs existed to score").
4. **A `--tenant` argument test**: confirm passing `--tenant keystone` actually constructs an `AgentSwitchClient("keystone")`, not just that argparse parses the flag — this is the kind of wiring bug that argparse's own tests won't catch.
5. **A regression test tying `rescore.py`'s `PROOFS_DIR` to `capstone_agent.py`'s `PROOFS_DIR`** — assert they resolve to the same `Path`, so a future edit to one file's constant that isn't mirrored in the other fails a test immediately instead of silently causing `rescore.py` to find zero runs after `capstone_agent.py` starts saving them somewhere else. (The cleaner long-term fix is extracting `PROOFS_DIR` into one shared location both files import — worth doing alongside this test.)
6. **An end-to-end test using `tmp_path` and a fully faked `AgentSwitchClient`**: save two real-shaped `TaskRun` fixtures, run `main()` against them, and assert `results.json`'s contents match calling `evaluate_run` directly on each — this is the one test that would catch a regression in the file's actual wiring (as opposed to testing each piece in isolation).
