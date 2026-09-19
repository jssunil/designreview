# `core/llm_gateway.py` — the multi-provider LLM transport

*Reading order: **3rd**. Depends on `core/economics.py` (read first) and `core/routing.yaml` (a config file, not code, but read it alongside this — it's what makes the ordering below non-hardcoded).*

## 10,000-ft view

One class, `LLMGateway`, with one public method, `.call(prompt, ...)`, that returns a string. Internally it tries a list of providers in a configured order, skips ones that are missing an API key or currently rate-limited/cooling down, retries once with a short backoff on failure, caches identical calls, and records every successful call's estimated cost. Everything downstream (`capstone_agent.py`'s synthesis call, `core/judge.py`'s rubric call) talks to `.call()` and never touches a provider's HTTP API directly.

## Why it's written this way (intent & trade-offs)

- **A plain importable class, not a hosted microservice.** The provenance note in the docstring is explicit: this design is a trimmed port of `glc_v5`'s `Router`/`RateState`, which in the source project runs as a FastAPI sidecar service that other processes call over HTTP. Here it's just a Python object the agent constructs directly. **Trade-off accepted**: no cross-process sharing of rate-limit state (two separate Python processes each think they have the full rate limit budget to themselves) and no ability to update routing config without restarting the process — both fine for a single capstone agent script, both would matter if this were serving multiple concurrent agents.
- **Rate-aware routing via `RateState`, not just try/except fallback.** The first-draft gateway (see git history) tried every provider in a fixed order and only moved on when an exception was thrown — meaning a rate-limited provider would get hit with a request, fail, and be retried on the *next* call too, forever paying the 429-latency tax. `RateState.available()` checks *before* calling, using a 60-second sliding window (`_call_times`) plus an explicit cooldown after a failure (`_cooldown_until`) — so a provider that just failed is skipped outright for `cooldown_sec` (default 20s), not retried into a second failure.
- **One retry-with-backoff per provider (`for attempt in range(2)`), not zero, not many.** Zero retries (the first draft) means one transient network blip permanently skips a whole provider for that call. Many retries risks burning through the rate-limit budget on a provider that's genuinely down. Two attempts with a flat 1.5s sleep between them is a deliberately modest middle ground — **not** exponential backoff (unlike `glc_v5`'s fuller implementation), which is a real, documented simplification: this file's retry policy is intentionally simpler than its source.
- **Exact-match cache, not semantic/embedding-based.** `glc_v5`'s `cache/semantic.py` matches *similar* prompts via embeddings, catching near-duplicate calls a hash-based cache would miss. This file's `ResponseCache` only matches byte-identical `(system_prompt, prompt, temperature)` triples. The trade-off is explicit in the docstring: an embeddings dependency (and the API cost of computing them) isn't worth it just to avoid re-paying for the harness re-running the *exact same* evidence-gathering prompt during eval iteration — the actual use case this project has.
- **The cache key does not include provider or tier.** This is a quieter trade-off, not called out in the code comments: calling the same `(system_prompt, prompt, temperature)` on the `"fast"` tier and later on the `"quality"` tier will return the *first* call's cached answer, from whichever provider happened to answer it, even though you asked for a different tier the second time. This is fine for this project's actual usage (each call site always uses the same tier for the same kind of prompt) but would surprise someone who assumed "tier" controls quality per call.
- **`self.nvidia_key` is read from the environment but never used.** `NVIDIA_API_KEY` is loaded in `__init__` but there's no `_call_nvidia` adapter, no `"nvidia"` entry in `_adapters`/`_key_present`, and no NVIDIA entry in `routing.yaml`. This is dead code left over from the first draft's docstring claim of NVIDIA support — worth either wiring up a real adapter or removing the unused field so the code doesn't imply a capability that doesn't exist.
- **Only Gemini's `httpx.Client` sets `verify=False`.** Every other provider adapter uses default (verified) TLS. This asymmetry is inherited from the first draft, not a deliberate per-provider security decision — worth checking whether it's still needed (a corporate proxy issue specific to `generativelanguage.googleapis.com`?) or just copy-paste residue.

## What it does (walkthrough)

1. **Module load**: if run as `__main__`, patches `sys.path` so `core.economics` resolves (see the comment inline — this only matters for `python core/llm_gateway.py`, not for normal imports from the project root). Loads `.env`. Builds a module-level `logger`.
2. **`RateState`** (dataclass): per-provider call-time tracking. `available()` prunes `_call_times` to the last 60 seconds and compares its length to `rpm_limit`; also short-circuits `False` if still inside a `_cooldown_until` window from a recent failure.
3. **`ResponseCache`**: a small LRU-ish cache (`maxsize=256`, FIFO eviction via `_order`, not true LRU — the oldest-inserted key is evicted, not the least-recently-*used* one). Keyed by a SHA-256 hash of `system_prompt|prompt|temperature`.
4. **`_load_routing_config(path)`**: reads `routing.yaml` if it exists, else returns `{}` — the gateway degrades to `DEFAULT_TIER_ORDER` rather than crashing if the config file is missing or empty.
5. **`LLMGateway.__init__`**: reads six env vars for API keys/URLs, loads routing config, builds three per-provider lookup dicts (`_provider_configs`, `_rate_states`, and the two callable maps `_adapters`/`_key_present`), and constructs a fresh `ResponseCache` and `CostLedger`.
6. **`LLMGateway.call(...)`** — the orchestration loop:
   - Check the cache first; return immediately on a hit (no rate-limit check, no ledger entry — a cache hit is free and instant by design).
   - Iterate `_candidate_order(tier)`. For each provider: skip if no key; skip (and log why) if `RateState.available()` is `False`; otherwise attempt the call, with one retry.
   - On success: record the call in `RateState`, `CostLedger`, and `ResponseCache`, then return.
   - On total failure of every candidate: raise `RuntimeError` with every provider's skip/failure reason concatenated — this is what makes `errors.append(...)` valuable for debugging a "why did every provider fail" incident.
7. **Six `_call_<provider>` adapters**: each builds that provider's specific request body/headers, POSTs via `httpx.Client`, raises `RuntimeError` on a non-200, and extracts the text from that provider's specific response shape. These are unchanged in *transport* from the first draft — only the `model` parameter is new (previously hardcoded per adapter, now sourced from `routing.yaml` with a hardcoded fallback if the config doesn't specify one).
8. **`__main__` block**: a manual smoke test — one call, print the response and the ledger summary.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `ROUTING_CONFIG_PATH` | `Path` (module constant) | `core/routing.yaml`, resolved relative to this file so it works regardless of the process's working directory. |
| `DEFAULT_TIER_ORDER` | `List[str]` (module constant) | The fallback provider order used if `routing.yaml` is missing/empty/doesn't define the requested tier. |
| `RateState.rpm_limit` | `int` | Requests-per-minute ceiling for this provider, sourced from `routing.yaml`'s `rpm` field (default `60`). |
| `RateState._call_times` | `List[float]` | Timestamps of recent calls, pruned to a 60-second sliding window on every `available()` check. |
| `RateState._cooldown_until` | `float` (Unix timestamp) | If `time.time() < this`, the provider is treated as unavailable regardless of the RPM window — set by `record_failure()`. |
| `ResponseCache._store` | `Dict[str, str]` | Hash → cached response text. |
| `ResponseCache._order` | `List[str]` | Insertion order of keys, used for FIFO eviction once `maxsize` is exceeded. |
| `LLMGateway.gemini_key` / `.groq_key` / `.cerebras_key` / `.anthropic_key` / `.nvidia_key` / `.openrouter_key` | `Optional[str]` | Raw API keys from the environment; `None` if unset, which is exactly what `_key_present` checks. |
| `LLMGateway.ollama_url` | `str` | Local Ollama server URL, defaults to `http://localhost:11434`. |
| `LLMGateway._config` | `Dict[str, Any]` | The parsed `routing.yaml` (or `{}`). |
| `LLMGateway._provider_configs` | `Dict[str, Dict[str, Any]]` | Provider name → its `routing.yaml` entry (`model`, `rpm`). |
| `LLMGateway._rate_states` | `Dict[str, RateState]` | One `RateState` per provider named in `routing.yaml`; `setdefault` in `.call()` lazily creates one for a provider not in the config (e.g. if `routing.yaml` is missing entirely). |
| `LLMGateway.cache` | `ResponseCache` | Shared across all calls made through this gateway instance. |
| `LLMGateway.ledger` | `CostLedger` | Shared across all calls; see `core/economics.py`. |
| `LLMGateway._adapters` | `Dict[str, Callable[..., str]]` | Provider name → the bound `_call_<provider>` method. |
| `LLMGateway._key_present` | `Dict[str, Callable[[], bool]]` | Provider name → a zero-arg predicate for "do we have credentials for this one" (Ollama's is always `True`, since it's local). |

## Function/method reference

| Function/Method | Inputs → Output | Notes |
|---|---|---|
| `RateState.available(now)` | `Optional[float]` → `bool` | Accepts an explicit `now` for testability (no need to mock `time.time()` globally). |
| `RateState.record_call(now)` | → `None` | Side effect only. |
| `RateState.record_failure(cooldown_sec, now)` | → `None` | Side effect only; sets the cooldown window. |
| `ResponseCache._key(...)` | `(Optional[str], str, float)` → `str` | `@staticmethod`, pure, deterministic. |
| `ResponseCache.get` / `.set` | → `Optional[str]` / `None` | `.set` handles eviction inline. |
| `_load_routing_config(path)` | `Path` → `Dict[str, Any]` | Pure function; swallows a missing file by returning `{}`, but **does not** catch a malformed YAML file — a syntax error in `routing.yaml` will raise from `yaml.safe_load`, not degrade gracefully. |
| `LLMGateway._candidate_order(tier)` | `str` → `List[str]` | Three-level fallback: requested tier → `"default"` tier → hardcoded `DEFAULT_TIER_ORDER`. |
| `LLMGateway.call(...)` | → `str` | The only method other files call. Raises `RuntimeError` (not a typed exception) on total failure — a caller can't distinguish "all rate-limited" from "all genuinely erroring" without parsing the message string. |
| `LLMGateway._call_gemini` / `_call_groq` / `_call_cerebras` / `_call_anthropic` / `_call_openrouter` / `_call_ollama` | `(prompt, system_prompt, temp, max_tok, json_mode, model=None)` → `str` | Each raises `RuntimeError` on non-200; none catch or retry internally — that's `.call()`'s job, one layer up. |

## How to test it

The adapters are the hard part to test (they need `httpx` mocked, since hitting six real provider APIs in a test suite is slow, costly, and flaky). The routing/rate-limit/cache logic around them is pure and easy to test in isolation.

- **Fully unit-testable with no network**: `RateState` (pass explicit `now` values, no real sleeping needed), `ResponseCache` (hash determinism, eviction order), `_load_routing_config` (missing file → `{}`; a real YAML fixture → the right dict), `_candidate_order` (missing tier falls back to `"default"`; missing config falls back to `DEFAULT_TIER_ORDER`).
- **Needs `httpx` mocking** (e.g. `httpx.MockTransport` or `respx`): each `_call_<provider>` adapter — assert the right URL/headers/body shape is sent, and that a non-200 response raises with the response body inlined (not swallowed).
- **Needs the adapters mocked, not `httpx`**: `LLMGateway.call()`'s orchestration — patch `_adapters["gemini"]` to raise once then succeed (verifies retry), patch it to always raise (verifies fallthrough to the next provider), set a provider's key to `None` (verifies skip), pre-exhaust a `RateState` (verifies skip-and-log rather than call).
- **Integration, needs real keys**: the `__main__` smoke test itself — good as a manual "is my `.env` correct" check, not as an automated CI test (it costs real money and is non-deterministic in wall-clock time).

## Tests that should be added for stability

In priority order:

1. **A test that a rate-limited/cooling-down provider is actually skipped, not retried into another failure** — this is the entire point of porting `RateState` from `glc_v5`, and there is currently no test proving it works. Construct a gateway, manually call `state.record_failure()` on the first provider, call `.call()`, and assert the ledger's one entry is *not* from that provider.
2. **A test that the retry-then-fallback ordering is correct**: provider A fails both attempts → provider B is tried next, in the configured order, not a random one. Currently unverified.
3. **A test that a cache hit skips rate-limiting, retry, and cost-ledger recording entirely** — the code clearly intends this (`return cached` happens before the loop even starts), but there's no test pinning "a cached call adds zero ledger entries."
4. **A test that `routing.yaml` with a malformed tier (referencing a provider name not in `providers:`) fails loudly rather than silently skipping that provider forever** — right now, a typo in a tier list just means that provider's `_provider_configs.get(provider, {})` returns `{}`, its model falls back to the hardcoded default, and nothing warns you the config has a dangling reference.
5. **A test — and probably a fix — for `estimate`-adjacent behavior when `_load_routing_config` hits a malformed (not just missing) YAML file.** Right now this raises an uncaught `yaml.YAMLError` from deep inside `LLMGateway.__init__`; decide whether that should instead degrade to `DEFAULT_TIER_ORDER` like a missing file does, and test whichever behavior is chosen.
6. **A regression test locking in that the cache key intentionally excludes provider/tier** — either as documentation-via-test of the accepted trade-off, or as the trigger to fix it if a future need (e.g. wanting genuinely different cached answers per tier) makes the current behavior wrong.
7. **Dead-code cleanup test-or-removal**: either add an `NVIDIA_API_KEY`-backed adapter and a test for it, or delete `self.nvidia_key` and its dict entries — right now it's untested, unused, and slightly misleading about what this gateway actually supports.
