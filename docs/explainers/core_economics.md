# `core/economics.py` — the cost/token ledger

*Reading order: **2nd**. Small, self-contained, no project imports. Read this before `core/llm_gateway.py`, which is its only consumer.*

## 10,000-ft view

A ~65-line running total: every time the gateway makes an LLM call, it tells this module "here's the prompt, here's the response, here's how long it took," and this module estimates a token count and a dollar cost and appends it to an in-memory list. That's the whole feature. It exists so a run's `Cost ledger: {...}` summary is possible, not to produce billing-accurate numbers.

## Why it's written this way (intent & trade-offs)

- **Estimated tokens, not real tokens.** `estimate_tokens` is `len(text.split()) * 1.3`, not a real tokenizer (`tiktoken` or provider-specific). This is a deliberate trade-off documented in the docstring: adding a tokenizer dependency (and per-provider tokenizer differences — Gemini/Anthropic/Groq don't all tokenize the same way) for a number that's only used for *visibility*, not billing enforcement, isn't worth it. If this ledger were ever used to *enforce* a budget (reject a call before it's made), this trade-off would need revisiting — real token counts would matter then.
- **In-memory only, one `CostLedger` per `LLMGateway` instance.** Nothing is persisted to disk. This matches how the ledger is actually consumed today — `agent.gateway.ledger.summary()` printed at the end of a single process run — but means the ledger resets every time a new `Team21DesignReviewAgent` is constructed. If you wanted cost tracking *across* runs (e.g. "how much has this benchmark suite cost me this week"), this module has nothing for that; it would need to write `LedgerEntry` rows somewhere durable.
- **A static price table (`PRICE_PER_1K_TOKENS`), not a live pricing API.** Explicitly called "illustrative only" in the code. Real provider pricing changes over time and per-model; hardcoding it means this number will silently drift out of date. This was accepted because the ledger's job is *relative* cost visibility (which provider is expensive, is one run unexpectedly costly) rather than an invoice.
- **No budget enforcement (no pre-call rejection).** This is the one piece explicitly *not* ported from `glc_v5`'s `economics/budget.py` (which does 402-on-breach admission control) — see `core/llm_gateway.py`'s docstring and the plan doc for why. `CostLedger` only ever records after the fact; it cannot stop a call.

## What it does (walkthrough)

1. `estimate_tokens(text)` — a pure function, word-count-based token estimate. Returns at least `1` for any non-empty input (avoids a zero-cost entry for a genuinely tiny call).
2. `LedgerEntry` — one row per LLM call (see Types below).
3. `CostLedger.record(...)` — the only way an entry gets added. Computes `tokens = estimate_tokens(prompt) + estimate_tokens(response)`, looks up `PRICE_PER_1K_TOKENS.get(provider, 0.001)` (falls back to a conservative-ish default for any provider not in the table, so a new provider added to `routing.yaml` without a price-table update doesn't crash — it just estimates generically), computes cost, appends, and logs the call as a structured `logger.info` line.
4. `CostLedger.total_cost()` / `.total_tokens()` / `.summary()` — read-only aggregates over `self.entries`.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `PRICE_PER_1K_TOKENS` | `Dict[str, float]` (module constant) | Provider name → approximate USD per 1K tokens (blended input+output). `ollama` is `0.0` since it's local/free. |
| `LedgerEntry.provider` | `str` | Which provider adapter made the call (`"gemini"`, `"groq"`, etc.) — matches the keys used in `core/llm_gateway.py`'s `_adapters` dict. |
| `LedgerEntry.model` | `str` | The specific model string used (e.g. `"gemini-2.5-flash"`), sourced from `routing.yaml`. |
| `LedgerEntry.tokens_estimated` | `int` | Sum of estimated prompt + response tokens for this one call. |
| `LedgerEntry.cost_usd` | `float` | `(tokens_estimated / 1000) * price`, rounded to 6 decimal places (small enough that a naive 2-decimal round would show `$0.00` for almost every call). |
| `LedgerEntry.latency_sec` | `float` | Wall-clock time for the provider call, rounded to 3 decimals. |
| `LedgerEntry.ts` | `float` (Unix timestamp, `default_factory=time.time`) | When the call happened — lets a future consumer bucket entries by time without threading a timestamp through `record()`'s call sites. |
| `CostLedger.entries` | `List[LedgerEntry]` | The whole history for this ledger instance; grows unbounded for the process's lifetime (no eviction — fine for a single benchmark run, would need a cap for a long-lived service). |

## Function/method reference

| Function | Inputs → Output | Notes |
|---|---|---|
| `estimate_tokens(text)` | `str` → `int` | Pure, no I/O, no side effects. Trivial to test. |
| `CostLedger.record(provider, model, prompt, response, latency_sec)` | → `LedgerEntry` | Side effect: appends to `self.entries` and emits a log line. Returns the entry it created (useful for tests that want to assert on the exact value without re-deriving it). |
| `CostLedger.total_cost()` | → `float` | Sum of `cost_usd` across all entries, rounded to 6 decimals. |
| `CostLedger.total_tokens()` | → `int` | Sum of `tokens_estimated`. |
| `CostLedger.summary()` | → `Dict[str, float]` | `{"calls":, "tokens_estimated":, "cost_usd_estimated":}` — what gets printed at the end of `capstone_agent.py`'s `__main__` block. |

## How to test it

This is the easiest module in the whole project to test well: everything is a pure function or an in-memory data structure, nothing touches the network or the filesystem.

```python
def test_estimate_tokens_empty_string_is_zero():
    assert estimate_tokens("") == 0  # explicit `if not text: return 0` guard

def test_estimate_tokens_nonempty_is_at_least_one():
    assert estimate_tokens("hi") >= 1

def test_record_appends_and_computes_cost():
    ledger = CostLedger()
    entry = ledger.record("gemini", "gemini-2.5-flash", "a b c", "d e", 1.23)
    assert entry in ledger.entries
    assert entry.tokens_estimated == estimate_tokens("a b c") + estimate_tokens("d e")
    assert entry.cost_usd == round((entry.tokens_estimated / 1000) * PRICE_PER_1K_TOKENS["gemini"], 6)

def test_unknown_provider_falls_back_to_default_price():
    ledger = CostLedger()
    entry = ledger.record("brand_new_provider", "m", "x", "y", 0.1)
    assert entry.cost_usd == round((entry.tokens_estimated / 1000) * 0.001, 6)

def test_summary_aggregates_multiple_entries():
    ledger = CostLedger()
    ledger.record("gemini", "m", "a", "b", 0.1)
    ledger.record("groq", "m", "c d", "e f", 0.2)
    s = ledger.summary()
    assert s["calls"] == 2
    assert s["tokens_estimated"] == ledger.total_tokens()
```

## Tests that should be added for stability

1. **Lock in the empty-string edge case with an explicit test** (`estimate_tokens("") == 0`) — the `if not text: return 0` guard exists precisely so a call with an empty prompt or response doesn't get inflated to a phantom 1-token cost, but nothing currently proves that guard stays in place through a future refactor.
2. **A test that a missing/unknown provider doesn't crash `record()`** — `PRICE_PER_1K_TOKENS.get(provider, 0.001)` already handles this gracefully, but there's no test pinning that behavior, so a future refactor could accidentally make it a `KeyError`.
3. **A test that `cost_usd` rounding doesn't silently zero out cheap calls** — e.g. a 1-token Ollama call should be exactly `0.0`, not a rounding artifact; a 1-token Gemini call should be a small but non-zero number.
4. **A property-based test** (e.g. via `hypothesis`) that `total_cost() == sum(e.cost_usd for e in entries)` and `total_tokens() == sum(e.tokens_estimated for e in entries)` hold for any sequence of `record()` calls — cheap insurance against a future refactor of the aggregation logic.
