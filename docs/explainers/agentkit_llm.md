# `agentkit/llm/` — the LLM gateway and cost ledger

*Reading order: **2nd**. Files: `gateway.py`, `economics.py`. Config: `config/routing.toml`.*

## 10,000-ft view

One in-process client for seven providers (Gemini, Groq, Cerebras, Anthropic, NVIDIA, OpenRouter, Ollama).
`call(prompt, …) -> str` for simple callers; `call_detailed(…) -> LLMReply` (text, provider, model,
input/output tokens, normalised `stop_reason`, latency) for the narrator. No SDKs — plain `httpx`.

## Why it's written this way

- **Tiers are config.** `routing.toml` lists providers (model, `model_env` override, key variable, rpm,
  timeout) and tiers (`default`, `fast`, `quality`). It is validated by a pydantic model: an unknown provider
  in a tier, rpm = 0, a duplicate or a misspelt key stops the gateway from starting.
- **Retries by error class.** 429, 408, 409, 5xx, 529 (overloaded), network errors, timeouts and empty
  replies are retried with capped backoff (0.5, 1, 2, 4, 4 … s), then the provider cools down and the next is
  tried. 400/404/422 go straight to the next provider. 401/402/403 (bad key, no quota) cool the provider down
  for 5 minutes — they won't fix themselves mid-run.
- **Real usage, not estimates.** Each adapter reads the provider's own token counts (Gemini's thinking tokens
  count as output; Anthropic cache tokens count as input). The ledger prices input and output separately and
  flags any call whose usage had to be estimated.
- **`stop_reason` is normalised** to `end_turn | max_tokens | refusal | other`. A truncated or refused reply is
  never cached; a refusal or an empty reply moves to the next provider; the narrator treats `max_tokens` as
  unusable.
- **Secrets stay out of logs.** The Gemini key is sent in the `x-goog-api-key` header, not the URL; every
  error message is scrubbed of key values.
- **TLS against the OS trust store.** httpx's bundled `certifi` list rejects this workstation's certificate
  chain (TLS-inspecting proxy); `ssl.create_default_context()` accepts it and still verifies.
- **Anthropic specifics.** No `temperature` for models that reject sampling parameters (Opus 5, Opus 4.7/4.8,
  Sonnet 5, Fable); extra `max_tokens` headroom where thinking is on by default; text is taken only from
  `text` blocks, since a `thinking` block can come first. `.env` `ANTHROPIC_MODEL` wins over the routing default.
- **Cache** is an exact-match LRU over every parameter that changes the answer (prompt, system, temperature,
  max_tokens, json_mode, tier), thread-safe.

## Key types

| Name | Purpose |
|---|---|
| `LLMGateway(routing_path, transport=, max_attempts=, sleep=, env=)` | the client; `transport` lets tests use `httpx.MockTransport` |
| `LLMReply` | text + provider + model + tokens + stop_reason (`.complete`) |
| `ProviderFault(provider, status, retryable)` | internal classified failure |
| `RoutingConfig` / `ProviderSpec` | pydantic models for `routing.toml` |
| `RateState` | per-provider rpm window + cooldown (thread-safe) |
| `CostLedger` / `LedgerEntry` | per-call provider, model, tokens, cost, latency |

## Operating notes (checked 2026-09-28)

Groq and Cerebras serve `gpt-oss-120b` (not `llama-3.3-70b`); NVIDIA retired `meta/llama-3.3-70b-instruct`
on 2026-08-26 — if `.env` still sets `NVIDIA_MODEL` to it, remove the line. Cerebras returns HTTP 402 when
the key has no quota; the gateway cools it down and moves on.

## How to test

`tests/test_llm_gateway.py` (offline, `httpx.MockTransport`); `-m gateway_live` for one real call per provider.
`python -m agentkit.llm.gateway [tier]` prints which providers have keys and makes one tiny call.
