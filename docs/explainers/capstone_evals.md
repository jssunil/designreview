# `capstone_evals.py` — the ground-truth benchmark suite

*Reading order: **9th**. Depends on `capstone_agent.py` (8th) for the agent it evaluates, and `core/eval_framework.py`/`core/judge.py` (6th/7th) for the scoring machinery. This is the file that actually defines what "correct" means for this project's one graded task.*

## 10,000-ft view

Five axis functions plus a small `Team21Evaluator` wrapper. Each axis takes a finished `TaskRun` and re-fetches something real from the live platform (commit messages, release-readiness) to check the agent's claim against — this is the concrete, domain-specific realization of `core/eval_framework.py`'s generic "axes get ground-truth context" contract.

## Why it's written this way (intent & trade-offs)

- **Every ground-truth helper re-fetches live, on every axis call, with no caching between axes.** `axis_cites_real_bend_radius_change` and `axis_cites_real_blank_growth` both independently call `_fetch_commit_messages` — meaning a single `evaluate_run` call makes the same `DesignVersion.list` MCP request twice. This is a deliberate simplicity-over-efficiency trade-off: each axis is self-contained and doesn't need to know about any other axis's state, at the cost of redundant network calls. For five axes on one file, this is inexpensive; it would need restructuring (e.g. pre-fetching ground truth once and passing it as `ground_truth_context` instead of a bare client) if the axis count or call frequency grew significantly.
- **The module docstring hardcodes the *exact* real commit-message text as of 2026-09-18.** This is unusual for code — comments don't normally quote live database content — but it's intentional documentation: it's the actual ground truth every axis in this file is implicitly built around, made explicit so a future reader (or a future you) doesn't have to re-query the platform just to understand why the axes check for `"2.0 mm"`/`"3.0 mm"`/`"4.2 mm"` specifically. The trade-off: **this comment can go stale.** If the seed data or the platform's live state changes (someone edits that `DesignVersion`, or fixes the `release_readiness` blocker), the axes will keep working correctly (they re-fetch, they don't trust this comment), but the comment will silently start describing a past state — a maintenance note, not a bug.
- **`axis_matches_real_release_readiness`'s negation-substring bug was found and fixed live during this session.** The original version independently checked for "claims ready" phrases and "claims not ready" phrases; since `"not ready for release"` contains `"ready for release"` as a literal substring, both checks matched simultaneously, and the boolean logic (`claims_ready and not claims_not_ready`) produced a false negative on exactly the phrasing the real LLM naturally used. The fix — check negation phrases *first*, and only check the positive phrases if no negation was found — is a small, general lesson: **any pair of "does the text say X" / "does the text say NOT-X" checks needs to guard against X being a literal substring of NOT-X**, not just here.
- **`axis_did_not_fabricate_geometry_diff` checks for the *presence* of a specific fabrication phrase, not the *absence* of a correct disclaimer.** This is a narrower, more conservative check than it might first appear: it only fails the agent if it says something like `"geometry diff shows..."`, not if it simply fails to say "diff_from_parent_json is null." An agent that never mentions geometry diffing at all (neither claiming it ran one nor disclaiming that it didn't) passes this axis by default. This is a real gap between "the axis's name" and "what it actually checks" worth being aware of.
- **`AXES` is a plain module-level dict, imported directly by both `capstone_evals.py`'s own `Team21Evaluator` and by `rescore.py`.** This means the *same* axis definitions are used for a live-scored run and a rescored saved run — the intended symmetry the whole raw-run/rescore split depends on. The trade-off: `rescore.py` has to explicitly `.pop("judged_reasoning_quality", ...)` to exclude the one LLM-calling axis by default, meaning the "which axes are cheap/free to rescore" distinction lives in `rescore.py`, not as a declared property on the axis itself (e.g. no `axis.is_free_to_rescore` flag) — a future sixth axis that also calls an LLM would need someone to remember to add it to that same exclusion list by hand.

## What it does (walkthrough)

1. **`_fetch_commit_messages(client, file_id)`**: calls `DesignVersion.list` sorted ascending by `version_number`, extracts just the `commit_message` strings from `structuredContent["data"]`.
2. **`_fetch_release_readiness(client, file_id)`**: calls `endpoint.designreview.release_readiness`, drills into `structuredContent["result"]` (one level deeper than a `.list` call's shape — see `capstone_agent.py`'s explainer for why the response envelope differs by tool type).
3. **Five axis functions** (see Types/Functions below) — each takes `(run: TaskRun, client_or_ctx) -> bool`.
4. **`AXES`**: the dict mapping axis name → function, consumed by both this file's `Team21Evaluator` and by `rescore.py`.
5. **`Team21Evaluator.run_core_benchmark()`**: runs the agent fresh (`self.agent.run(...)`), then scores the resulting `TaskRun` via `evaluate_run`, passing `self.agent.mcp_client` as the `ground_truth_context` — the same live client the agent itself just used, so the axes' "ground truth" and the agent's "evidence" come from the identical, consistent platform state (no risk of the platform changing state *between* the agent's run and the axes' re-fetch, since they happen back-to-back in the same process).
6. **`__main__` block**: runs the whole suite once, prints the `EvaluationResult` as JSON.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `AXES` | `Dict[str, Callable[[TaskRun, Any], bool]]` | The five named axes; also imported by `rescore.py`. |
| Axis functions' `client`/`_ctx` parameter | `AgentSwitchClient` (for the three data-fetching axes) or `Any` (unused, for the two that don't need it) | The inconsistent parameter typing (`client: AgentSwitchClient` vs. `_ctx: Any = None`) reflects which axes actually use `ground_truth_context` and which don't — a quick visual signal, at the cost of `AxisFn`'s declared type (`Callable[[TaskRun, Any], bool]`) not precisely matching every individual function's real signature. |
| `Team21Evaluator.tenant` / `.agent` | `str` / `Team21DesignReviewAgent` | Constructed together in `__init__` — there's no way to evaluate a *pre-existing* agent instance; `Team21Evaluator` always builds its own. |

## Function/method reference

| Function | Ground truth re-fetched | Fails when... |
|---|---|---|
| `axis_cites_real_bend_radius_change` | `DesignVersion.list` commit messages | The real commit history doesn't actually describe a 2.0→3.0mm bend radius change (returns `False` immediately — this is the "ground truth itself doesn't support the claim" branch), **or** it does but the agent's answer omits either number or the phrase "bend radius". |
| `axis_cites_real_blank_growth` | Same as above | Real commits don't mention "4.2 mm", or the agent's answer doesn't. |
| `axis_matches_real_release_readiness` | `endpoint.designreview.release_readiness` | The agent's stated release-gating direction (ready vs. not-ready) doesn't match the platform's real `ready` boolean — see the negation-substring fix above for exactly how "matches" is determined. |
| `axis_did_not_fabricate_geometry_diff` | None (static domain rule) | The answer contains one of two specific fabrication phrases (`"geometry diff shows"`, `"geometric comparison confirms"`) — see the narrowness caveat above. |
| `axis_judged_reasoning_quality` | None directly (delegates to `core/judge.py`, which itself makes a fresh LLM call) | The judge's 0-1 average across `specific`/`consistent`/`non_generic` is below `0.6`. |
| `Team21Evaluator.run_core_benchmark()` | — | Delegates everything to `evaluate_run`; doesn't itself define pass/fail. |

## How to test it

The three data-fetching helpers and the axis functions that call them need a fake `AgentSwitchClient` (or a real one against a fixture/mock server); the two axes that don't need ground truth (`axis_did_not_fabricate_geometry_diff`, `axis_judged_reasoning_quality` modulo its LLM call) are easier.

```python
class FakeClient:
    def __init__(self, versions=None, readiness=None):
        self.versions = versions or []
        self.readiness = readiness or {}
    def call_mcp(self, method, params):
        name = params["name"]
        if name == "DesignVersion.list":
            return {"result": {"structuredContent": {"data": self.versions}}}
        if name == "endpoint.designreview.release_readiness":
            return {"result": {"structuredContent": {"result": self.readiness}}}
        raise AssertionError(f"unexpected tool: {name}")

def test_bend_radius_axis_fails_on_wrong_numbers():
    client = FakeClient(versions=[{"commit_message": "Rev C -- bend radius 2.0 mm to 3.0 mm"}])
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "The bend radius changed from 5.0mm to 9.0mm."
    assert axis_cites_real_bend_radius_change(run, client) is False

def test_bend_radius_axis_passes_on_matching_numbers():
    client = FakeClient(versions=[{"commit_message": "bend radius opened from 2.0 mm to 3.0 mm"}])
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "The bend radius grew from 2.0mm to 3.0mm."
    assert axis_cites_real_bend_radius_change(run, client) is True

def test_bend_radius_axis_fails_when_ground_truth_has_no_such_claim():
    client = FakeClient(versions=[{"commit_message": "unrelated change"}])
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "The bend radius changed from 2.0mm to 3.0mm."  # agent hallucinated this
    assert axis_cites_real_bend_radius_change(run, client) is False

def test_release_readiness_axis_handles_negation_substring_correctly():
    client = FakeClient(readiness={"ready": False})
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "The part is currently not ready for release."
    assert axis_matches_real_release_readiness(run, client) is True  # regression test for the fixed bug

def test_release_readiness_axis_fails_when_agent_wrongly_claims_ready():
    client = FakeClient(readiness={"ready": False})
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "This design is ready for release."
    assert axis_matches_real_release_readiness(run, client) is False

def test_fabrication_guard_catches_the_exact_phrase():
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "The geometry diff shows a 3mm change."
    assert axis_did_not_fabricate_geometry_diff(run) is False

def test_fabrication_guard_passes_on_silence():
    run = TaskRun(task_id="t", prompt="p")
    run.claimed_answer = "Based on commit messages, the bend radius changed."
    assert axis_did_not_fabricate_geometry_diff(run) is True
```

## Tests that should be added for stability

In priority order:

1. **All seven tests sketched above, especially the two `axis_matches_real_release_readiness` tests** — this is the axis with the highest real-world stakes (a wrong "ready for release" signal is the most consequential possible failure for a design-review agent) and the one with the most recently fixed bug; it needs the strongest regression coverage in the file.
2. **The "ground truth itself doesn't support the claim" branch** in both `axis_cites_real_bend_radius_change` and `axis_cites_real_blank_growth` needs an explicit test (the third test above) — this branch exists specifically to make an axis fail *correctly* if the seed data ever changes such that the bend-radius story is no longer present, rather than silently passing because the string-matching logic happens to still line up by coincidence.
3. **A test — or a fix — for `axis_did_not_fabricate_geometry_diff`'s narrowness** (flagged in trade-offs): decide whether "never mentions geometry diffing" should really be treated the same as "correctly disclaims it," and either add a test locking in the current lenient behavior deliberately, or tighten the axis and test the tightened version.
4. **A test that the module docstring's hardcoded commit-message text actually matches what `_fetch_commit_messages` returns against a real fixture** — this would catch staleness (the maintenance-note risk flagged above) automatically instead of relying on a human noticing the comment has drifted from reality.
5. **A test for the redundant-refetch inefficiency**: instrument `FakeClient.call_mcp` to count calls, run the full `AXES` dict through `evaluate_run`, and assert how many times `DesignVersion.list` was actually called (currently: twice, once per axis that needs it) — this documents the current cost precisely, so a future optimization (fetch once, pass as context) has a concrete "before" number to improve on.
6. **A test that `judge_analysis`'s exclusion from `rescore.py`'s default axis set stays correct as `AXES` grows** — e.g. a test asserting every axis function's docstring or a lightweight marker indicates whether it calls an LLM, so a future sixth "costs money" axis can't be silently included in a "free" rescore by accident.
