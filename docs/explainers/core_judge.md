# `core/judge.py` — the single-pass LLM-as-judge

*Reading order: **6th**. Depends on `core/llm_gateway.py` (read 3rd). Read this alongside `core/eval_framework.py` (7th) — it's used by exactly one axis there.*

## 10,000-ft view

One function, `judge_analysis(text)`, that asks an LLM to grade a piece of text against a 3-criterion rubric and hands back a small typed score. It exists because most of `capstone_evals.py`'s axes can check the agent's claim against real platform data (ground truth), but "does this reasoning read as specific and non-generic" has no database row to check against — that one criterion genuinely needs a second opinion, so this file is a narrow, deliberately-boxed-in exception to the rest of the eval suite's ground-truth-only philosophy.

## Why it's written this way (intent & trade-offs)

- **One judge call, not a panel.** The provenance note says this plainly: `S17Code`'s `judge.py` runs a multi-model judge panel with disagreement tracking (multiple judges score the same text; if they disagree beyond some threshold, that's itself a signal worth surfacing). This file collapses that to exactly one call. The trade-off: a single judge call has no way to detect "the judge itself got this wrong" — an unlucky or biased single verdict is indistinguishable from a correct one. This was accepted because a panel means N× the LLM calls and cost for what is, in this project, one axis out of five — disproportionate for what it's checking.
- **`tier="fast"`, not `"quality"`.** The rubric call deliberately uses the cheap/fast provider tier from `routing.yaml`, not the same tier the main synthesis call uses. This is a real, considered cost trade-off: judging is a sanity check, not the primary work product, so it doesn't need the best available model.
- **JSON-mode output, parsed with a regex fallback (`re.search(r"\{.*\}", raw, re.DOTALL)`), not a strict JSON parse of the raw response.** Even with `json_mode=True` requested, some providers wrap their JSON in prose or markdown fences despite being asked not to. The regex search is a pragmatic "find the first `{...}` blob anywhere in the text" rather than trusting the response to be *pure* JSON. The trade-off: this regex is greedy (`.*` with `DOTALL`) and will grab from the *first* `{` to the *last* `}` in the whole response — if a provider ever returns two JSON objects in one response (unlikely but not impossible), this would concatenate spans incorrectly and fail to parse, or worse, silently parse a corrupted merge if the intervening text also happens to look JSON-ish.
- **A 0-5 scale per criterion, normalized via `.average` to 0-1 by dividing by 15 (3 criteria × 5 max).** This mirrors the shape of every other axis in `capstone_evals.py` returning a boolean, but `judge_analysis` itself returns a richer `JudgeScore` — the boolean-ization (`score.average >= 0.6`) happens one layer up, in `capstone_evals.py::axis_judged_reasoning_quality`, not here. This keeps `core/judge.py` reusable for a future consumer that wants the raw 0-5 breakdown, not just a pass/fail.
- **No retry, no fallback if the JSON parse fails.** If the regex doesn't find a `{...}` blob, or the found blob isn't valid JSON, `json.loads` raises — uncaught, by design (there's no try/except in `judge_analysis`). This means a malformed judge response crashes the *scoring* of that one axis (though `core/eval_framework.py::evaluate_run` catches any axis exception and marks it `False`, so the *overall evaluation* survives — just that one axis is unfairly marked failed rather than "the judge couldn't be reached").

## What it does (walkthrough)

1. **`RUBRIC`** — a module-level format-string template. Three criteria (`specific`, `consistent`, `non_generic`), each 0-5, plus a free-text `rationale`. The prompt explicitly demands "ONLY a JSON object... no prose before or after" — an instruction the regex-fallback parsing exists precisely because models don't always obey.
2. **`JudgeScore`** — the parsed result (see Types below).
3. **`judge_analysis(analysis, gateway=None)`**:
   - Constructs (or reuses) an `LLMGateway`.
   - Fills the rubric template with the text to be judged, calls it at `temperature=0.0` (deterministic-as-possible — a judge's grading shouldn't itself be random) and `json_mode=True`, on the `"fast"` tier.
   - Extracts and parses the JSON blob, defaulting any missing field to `0` (or `""` for `rationale`) rather than raising on a partially-formed response — so a response missing just the `rationale` key, say, still produces a usable score.
   - Wraps the parsed values in a `JudgeScore` and returns it.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `RUBRIC` | `str` (module constant, a `.format()` template with one `{analysis}` placeholder) | The entire judging instruction, kept as one editable constant rather than built up programmatically — makes the exact wording easy to review and tune in one place. |
| `JudgeScore.specific` / `.consistent` / `.non_generic` | `int` (0-5 each) | The three rubric criteria's raw scores, coerced with `int(...)` from whatever the LLM returned (so a response returning `"4"` as a string, or `4.0` as a float, still lands as a clean `int`). |
| `JudgeScore.rationale` | `str` | The judge's free-text justification — not used programmatically anywhere today, but preserved for a human reviewing *why* an axis failed. |
| `JudgeScore.average` (property, not a stored field) | `float`, 0-1 | Computed on access, not cached — recomputing it is cheap enough that caching would be needless complexity. |

## Function/method reference

| Function | Inputs → Output | Notes |
|---|---|---|
| `judge_analysis(analysis, gateway=None)` | `(str, Optional[LLMGateway])` → `JudgeScore` | If `gateway` is omitted, constructs a fresh `LLMGateway()` per call — meaning repeated calls without passing a shared gateway each get their own cache/rate-limit state and don't benefit from each other's caching. `capstone_evals.py` currently calls this without passing a gateway, so this inefficiency is live, not hypothetical. |

## How to test it

The hard part to test is the LLM call itself; the parsing logic around it is easy to test in isolation by mocking `LLMGateway.call`.

```python
class FakeGateway:
    def __init__(self, response_text):
        self.response_text = response_text
    def call(self, *args, **kwargs):
        return self.response_text

def test_parses_clean_json():
    fake = FakeGateway('{"specific": 4, "consistent": 5, "non_generic": 3, "rationale": "ok"}')
    score = judge_analysis("some text", gateway=fake)
    assert score.specific == 4 and score.consistent == 5 and score.non_generic == 3
    assert score.average == round((4 + 5 + 3) / 15.0, 2)

def test_parses_json_wrapped_in_prose():
    fake = FakeGateway('Sure, here you go:\n{"specific": 2, "consistent": 2, "non_generic": 2}\nHope that helps!')
    score = judge_analysis("x", gateway=fake)
    assert score.specific == 2

def test_missing_fields_default_to_zero():
    fake = FakeGateway('{"specific": 5}')
    score = judge_analysis("x", gateway=fake)
    assert score.consistent == 0 and score.non_generic == 0 and score.rationale == ""

def test_malformed_json_raises():
    fake = FakeGateway("not json at all, no braces")
    with pytest.raises(json.JSONDecodeError):
        judge_analysis("x", gateway=fake)
```

## Tests that should be added for stability

In priority order:

1. **All four tests sketched above** — this file currently has zero test coverage, and the parsing logic (the part actually worth testing, since the LLM call itself can't be deterministically tested) is small enough to fully cover in under 10 test cases.
2. **A test for the "two JSON objects in one response" edge case** flagged in the trade-offs section — construct a fake response with two separate `{...}` blobs and pin down what currently happens (likely a parse failure or a garbled merge), so a future fix to the parsing strategy has a regression test to work against.
3. **A test that `gateway=None` actually constructs a working default `LLMGateway`** — currently only exercised implicitly by `capstone_evals.py` calling it with no gateway; a direct test would catch a regression in the default-construction path in isolation.
4. **A test — or a fix — for the "no shared gateway across repeated calls" inefficiency**: if `capstone_evals.py` or a future caller judges many analyses in one process, decide whether `judge_analysis` should accept and reuse a passed-in gateway more consistently (it already can — `capstone_evals.py` just doesn't pass one), and add a test proving a shared gateway's cache is actually hit on a repeated identical judging call.
5. **A test for score boundary behavior**: e.g. all-zero scores (`average == 0.0`), all-max scores (`average == 1.0`), and the exact `0.6` threshold used by `capstone_evals.py::axis_judged_reasoning_quality` — a boundary test here documents the pass/fail cutoff's actual behavior (`>=` vs `>`) in one place instead of leaving it implicit in the caller.
