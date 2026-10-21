# `agentkit/llm/` — the LLM gateway and the cost ledger

*Reading order: **2nd**. Files: `gateway.py`, `economics.py`, `models_check.py`. Config: `config/routing.toml`.*
*Written in ASD-STE100 (Simplified Technical English) style, 2026-10-14.*

## Overview

The gateway is one in-process client for many LLM providers. It uses plain `httpx` and no provider SDK.

- `call(prompt, ...) -> str` gives only the text. Simple callers use it.
- `call_detailed(...) -> LLMReply` gives the text, the provider, the model, the token counts, the stop reason
  and the latency. The narrator uses it.

The file `config/routing.toml` has two parts:

- **Providers.** Each row is one provider and one model.
- **Tiers.** Each tier is a list of providers. The gateway tries them in that sequence.

## Providers and tiers (2026-10-14)

| Row | Model | API | Key variable | Tier use |
|---|---|---|---|---|
| `openai_luna` | gpt-6-luna | OpenAI (by URL) | `OPENAI_API_KEY2` | fast: 1st |
| `openai_sol` | gpt-6.1-sol | OpenAI (by URL) | `OPENAI_API_KEY2` | quality, default: 1st |
| `deepseek_flash` | deepseek-flash | DeepSeek (by URL) | `DEEPSEEK_API_KEY` | fast: 2nd |
| `deepseek` | deepseek-v4-pro | DeepSeek (by URL) | `DEEPSEEK_API_KEY` | quality, default: 2nd |
| `moonshot` | kimi-k2.6 | Moonshot (by URL) | `MOONSHOT_API_KEY` | all tiers: 3rd |
| `gemini_lite` | gemini-3.5-flash-lite | Gemini (native) | `GEMINI_API_KEY_2` | fast: 4th |
| `gemini` | gemini-3-flash-preview | Gemini (native) | `GEMINI_API_KEY_2` | quality, default: 4th |
| `anthropic_haiku` | claude-haiku-5-5 | Anthropic (native) | `ANTHROPIC_API_KEY` | fast: 5th |
| `anthropic` | claude-sonnet-5-5 | Anthropic (native) | `ANTHROPIC_API_KEY` | quality, default: 5th |
| `groq`, `cerebras`, `nvidia`, `openrouter` | see `routing.toml` | OpenAI-compatible | own key | after the five above |
| `lmstudio` | google/gemma-4-e4b | local server (by URL) | none | second to last |
| `ollama` | gemma4:latest | local server | none | last |

The **fast** tier gives the cheap first draft. The **quality** tier gets a draft only when the claim audit
rejects the cheap draft. The **default** tier is for other callers.

The gateway also adds an `openai` row when the official runner injects `OPENAI_API_KEY` into the process.
That row then goes first in each tier. A copy of `OPENAI_API_KEY` in `.env` does not move it.

Each row can change its model through its `model_env` variable in `.env`. For example,
`GEMINI_MODEL=gemini-3.8-flash` changes the `gemini` row.

## Row settings

| Setting | Use |
|---|---|
| `model`, `model_env` | The model. The `.env` variable in `model_env` overrides `model`. |
| `key_env`, `keyless` | The key variable. A `keyless` row (a local server) sends no Authorization header. |
| `key_envs` | More key variables for the row. The gateway uses the keys in turn. See "More keys for one row". |
| `cooldown_s`, `auth_cooldown_s` | The cool-downs of the row in seconds, after a rate limit or a busy server (default 20), and after 401, 402 or 403 (default 300). |
| `url`, `url_env` | Any OpenAI-compatible endpoint. Give the base URL or the full `.../chat/completions` URL. `url_env` overrides `url`. |
| `api` | `"anthropic"` or `"gemini"`. A second row on that native API, under its own name. |
| `temperature` | A fixed temperature. Use it for a model that accepts one value only (kimi-k2.6: 1). |
| `max_tokens_field` | `"max_completion_tokens"` for OpenAI reasoning models. They reject `max_tokens`. |
| `reasoning_effort` | Sent as is. gpt-6-luna accepts `"none"`. The lowest value for gpt-6.1-sol is `"low"`. |
| `rpm`, `timeout` | The request limit for each minute, and the request timeout in seconds. |

A row with its own name has its own place in a tier, its own rate state and its own cool-down. Thus a busy
`anthropic_haiku` does not stop `anthropic`.

## Which model answered

The run folder keeps the provider and the model for each LLM call:

| File | Contents |
|---|---|
| `llm_ledger.json` | One entry for each call: provider, model, tokens, cost, latency, stop reason. |
| `telemetry.json` | One `llm` span for each call: tier, provider, model, tokens, stop reason. |
| `taskrun.json` | The narrate step: the provider and the model that wrote the answer, and the same for each attempt. |
| `report.md` (batch) | The Performance table has a "Models (provider:model)" column. |
| `llm_calls.jsonl` | One line for each attempt: tier, provider, model, the exact system prompt, the prompt and the reply, tokens, cost, latency, and `cached` or `error`. |

These files never hold a key. Only `llm_calls.jsonl` holds prompts.

## Model-visible means logged (since 2026-10-20)

Each LLM attempt goes into the call log of the ledger (`CostLedger.calls`, type `LLMCall`). The log has
three kinds of line:

- an answered call, with the reply, the tokens and the cost;
- a cache hit (`cached: true`), which costs nothing and has no ledger entry;
- a failed attempt (`error`), with the error text and no reply.

The gateway removes each key value from each string before it records the line. The harness writes the
log to `llm_calls.jsonl` in the run folder. A resumed run adds its lines after the earlier ones. The viewer
shows the lines under "Prompts and replies" in the Cost tab, as text only.

Thus a person can see what the model saw for each answer, and why the claim audit passed or failed it. The
log is evidence only, like telemetry: the grader never reads it. Set `AGENTKIT_LOG_PROMPTS=0` to stop it.

## More keys for one row (since 2026-10-21)

A row can have more than one key: `key_env`, then each name in `key_envs` that has a value. Each key has its
own request window (`rpm`) and its own cool-down.

- **In turn.** Each call starts at the next key. Thus the load goes to all the keys.
- **A fault of a key.** A rate limit (429), a bad key or no credit (401, 402, 403), or a spent budget cools that
  key only. The gateway then uses the next key at once, with no wait. When all the keys of the row cool down,
  the gateway goes to the next row of the tier.
- **A fault of the server.** A busy or failed server (5xx, network error) cools all the keys of the row. Then
  the gateway goes to the next row.
- **One key.** A row with one key works as before: three attempts with waits, then the next row.
- **No key text in records.** The ledger, `llm_calls.jsonl` and telemetry have `key_index` (0 is `key_env`).
  They never have the key. The gateway removes each key of each row from error messages.

## Design decisions

- **Tiers are configuration.** A pydantic model validates `routing.toml`. These errors stop the gateway
  before it starts: an unknown provider in a tier, `rpm = 0`, a duplicate row, a misspelt setting.
- **Retries by error class.** The gateway retries 429, 408, 409, 5xx, 529, network errors, timeouts and
  empty replies. The wait between attempts is 0.5, 1, 2, 4 and 4 seconds. Then the provider cools down, and
  the gateway tries the next provider. A row with more keys uses its next key after a 429, with no wait.
- **No retry for a bad request.** For 400, 404 and 422, the gateway goes to the next provider immediately.
- **Long cool-down for account errors.** For 401, 402 and 403 (bad key, no credit), the key cools down
  for 5 minutes (`auth_cooldown_s`). These errors do not stop during a run.
- **A spent budget is not a rate limit (since 2026-10-19).** The LLM endpoint of the platform answers 429 with
  `budget_exhausted` when the token budget of the team is spent. The gateway does not retry this 429. The
  provider stays down for the rest of the run, and the answer falls back to the template at once. A test run
  against a stub endpoint gave one request for each task, and the tasks still passed.
- **Real token counts.** Each adapter reads the token counts that the provider sends. Gemini thinking tokens
  count as output tokens. Anthropic cache tokens count as input tokens. The ledger shows each call that has
  estimated counts.
- **One stop reason.** The gateway changes each provider's stop reason to `end_turn`, `max_tokens`,
  `refusal` or `other`. The cache does not keep a cut-off reply or a refusal.
- **The gateway removes reasoning text.** Some open models write `<think>...</think>` into the answer.
  `strip_reasoning()` removes these blocks before the ledger, the cache and the audit see the text.
- **No temperature for Claude 5.x.** Claude Opus 5, Sonnet 5, Haiku 5, Fable and Opus 4.7/4.8 reject the
  `temperature` parameter. The gateway does not send it to these models.
- **Keys stay out of logs.** The Gemini key goes in the `x-goog-api-key` header, not in the URL. The gateway
  removes key values from each error message.
- **TLS uses the operating-system trust store.** The `certifi` list in httpx rejects the certificate chain on
  this workstation. `ssl.create_default_context()` accepts that chain and still verifies it.
- **Offline guard.** When `AGENTKIT_OFFLINE=1`, a real network call raises `OfflineViolation`. The narrator
  does not catch it. Thus an offline run that tries to use an LLM fails, and the failure is visible.
- **Exact-match cache.** The cache key has each parameter that can change the answer: prompt, system prompt,
  temperature, `max_tokens`, JSON mode and tier.

## Cost controls

- **Audit-gated cascade.** The pack sets the narration tiers to `("fast", "quality")`. The fast tier writes
  one draft. If the claim audit accepts it, that draft is the answer. If the audit rejects it, the quality
  tier gets the draft and the audit's correction note. The quality tier can write a draft and one rewrite.
- **The template is the last step.** If no draft passes, the template gives the answer.
- **Budget.** `max_llm_cost_usd_per_run` in `config/project.toml` sets a cost limit for each run. The
  variable `AGENTKIT_MAX_LLM_COST_USD` overrides it. No new call starts after the ledger reaches the limit.
- **Cache-friendly prompts.** The stable text is in the system prompt. The data of each run comes after it.
  Anthropic requests mark a system prompt of 4096 characters or more as cacheable.
- **Prices.** `economics.py` has a price for each row, in USD for each million tokens. The prices are
  approximate. Local rows cost 0.

## `.env` values and child processes

`load_env()` copies `.env` into the process environment. It does not change a variable that the process
already has. It also writes the names that it copied to `AGENTKIT_DOTENV_KEYS`.

The batch runs each task in a child process. The child gets the copied values in its environment. The child
reads `AGENTKIT_DOTENV_KEYS` and thus knows that these values came from `.env`. Without this list, the child
used the `.env` copy of `OPENAI_API_KEY` as a runner key and put `openai` first in each tier.

## Key types

| Name | Purpose |
|---|---|
| `LLMGateway(routing_path, transport=, max_attempts=, sleep=, env=)` | The client. Tests give `transport=httpx.MockTransport(...)`. |
| `LLMReply` | Text, provider, model, tokens, stop reason (`.complete`), `reasoning_chars`. |
| `ProviderSpec`, `RoutingConfig` | The pydantic models for `routing.toml`. |
| `chat_url(base)` | Adds `/chat/completions` to a base URL. |
| `strip_reasoning(text) -> (text, removed)` | The reasoning filter. |
| `ProviderFault(provider, status, retryable)` | An internal, classified failure. |
| `RateState` | The request window and the cool-down of one row. |
| `CostLedger`, `LedgerEntry` | The provider, model, tokens, cost and latency of each call. |

## Operating notes (2026-10-14)

- `GEMINI_API_KEY` has no prepaid credit (HTTP 402). The Gemini rows use `GEMINI_API_KEY_2`, a free-tier key.
- On the free tier, the pro models have no quota (HTTP 429). Gemini 3.5 to 3.8 Flash were busy (HTTP 503).
- Cerebras sends HTTP 402. The account needs payment.
- NVIDIA retired `meta/llama-3.3-70b-instruct` on 2026-08-26. Remove `NVIDIA_MODEL` from `.env`, or set a
  model that NVIDIA lists.

## How to test

1. Run `python -m agentkit.llm.models_check`. It reads the model list of each provider. It costs no tokens.
   A configured model that the list does not have shows as `RETIRED`.
2. Run `python -m agentkit.llm.gateway [tier]`. It shows the providers that have keys and makes one small call.
3. Run `python -m pytest tests_local/test_url_providers.py tests_local/test_llm_gateway.py
   tests_local/test_llm_call_log.py tests_local/test_key_rotation.py -q`. These tests are offline. They use `httpx.MockTransport`.
4. Run `python -m pytest tests_local -m gateway_live -q` for one real call to each provider.

The hand-written template for this module is `tests/test_llm_gateway.py`.
