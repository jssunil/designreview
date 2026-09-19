# `as_client.py` — the platform transport

*Reading order: **1st**. Every other file in this project either calls this directly or calls something that calls this. Nothing here depends on `core/`.*

## 10,000-ft view

A ~170-line, dependency-light HTTP client for AgentSwitch. It does three things: log in and hold a bearer token, make an authenticated REST call, and wrap the JSON-RPC 2.0 envelope that MCP tool calls travel in. Everything above this file — the gateway, the DAG engine, the agent, the evals — is talking to AgentSwitch *through* this file, never directly through `urllib`/`httpx` itself.

## Why it's written this way (intent & trade-offs)

- **`urllib.request`, not `httpx` or `requests`.** The rest of the project (the LLM gateway) uses `httpx`. This file deliberately doesn't. It's stdlib-only, so it has zero install step and can't break from a dependency-version mismatch — a reasonable choice for the one module that *must* work before anything else does (you can't even get an MCP tool catalogue without it). The trade-off: no connection pooling, no async, and manual `ssl` context handling instead of a library doing it for you.
- **`ssl._create_unverified_context()`.** TLS verification is turned off for every request. This is almost certainly working around a corporate/dev-cert issue on `agentswitch.theschoolofai.in`, not a considered security stance — worth revisiting if this client is ever pointed at a different host, since it silently accepts a MITM'd connection.
- **Token held in memory only (`self.token`), re-logged-in lazily.** `request()` calls `self.login()` if `self.token` is falsy, but never re-logs-in on a 401 (expired token) — a long-running process (like the harness looping over many tasks) will eventually hit an expired-token `HTTPError` and crash rather than silently re-authenticating. This is a known gap, not a design choice — see "tests to add" below.
- **One `call_mcp` for every JSON-RPC method.** Rather than one method per RPC verb, there's a single generic `call_mcp(method, params)`. This is the right call: the MCP surface here is 300+ tool names plus meta-methods (`initialize`, `tools/list`, `tools/call`) — hardcoding a method per tool would mean regenerating this file every time the platform adds a tool.
- **`rpc_id` defaults to `1` for every call, never incremented.** Fine for this client's actual usage pattern (synchronous request/response, one in flight at a time) since nothing here matches responses back to requests by id. It would silently misbehave if this client were ever used to pipeline multiple in-flight JSON-RPC requests — it isn't, so this is a reasonable simplification, not a live bug.

## What it does (walkthrough)

1. **Module load**: tries `python-dotenv`; if unavailable, hand-rolls a `.env` parser so `AS_*` env vars are set without a hard dependency.
2. **`get_environment_config(env_name)`**: a small pure function mapping `"suryodaya"`/`"keystone"` to a `{email, url, password}` dict, sourced from env vars. Raises `ValueError` on a missing password rather than silently trying to log in with `None`.
3. **`AgentSwitchClient.__init__`**: resolves config, builds an unverified SSL context (`self._ctx`), and does *not* log in yet — login is lazy.
4. **`.login()`**: POSTs `email`/`password` to `/api/auth/login`, stores the returned `token`. On an `HTTPError`, reads the error body and re-raises as a plain `RuntimeError` with that body inlined — this is what makes AgentSwitch's real error messages ("Missing password for...", schema-validation errors, etc.) visible to a caller instead of a bare `HTTP Error 401`.
5. **`.request(method, path, payload, headers)`**: the one place every REST call funnels through. Auto-logs-in if no token, attaches `Authorization: Bearer <token>`, and unwraps `HTTPError` the same way `.login()` does.
6. **`.call_mcp(method, params, rpc_id)`**: builds the `{"jsonrpc": "2.0", "id":, "method":, "params":}` envelope and POSTs it to `/api/mcp` via `.request()`. **Callers must check for an `"error"` key in the returned dict themselves** — this method does not raise on a JSON-RPC-level error, only on an HTTP-level one (a JSON-RPC error still comes back as HTTP 200). `capstone_agent.py`'s `_mcp_tool_executor` is the place that does this check.
7. **`.init_mcp()`**: runs the two-call MCP handshake (`initialize`, then `notifications/initialized`) that every session needs before any tool works.
8. **`.list_mcp_tools()`**: a thin one-liner over `call_mcp("tools/list")`.
9. **`__main__` block**: a manual smoke test — log in, print identity/allowed apps, run the MCP handshake, print the first 10 tool names. This is the fastest way to check "is my `.env` correct and is the platform up" before running anything heavier.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `get_environment_config`'s return | `Dict[str, str]` with keys `email`, `url`, `password` | The three things needed to authenticate against one tenant; kept as a plain dict rather than a dataclass since it's consumed once, immediately, by `__init__`. |
| `AgentSwitchClient.base_url` | `str` | The tenant's root URL (`https://agentswitch.theschoolofai.in` or the Keystone equivalent). |
| `AgentSwitchClient.token` | `Optional[str]` | Bearer token; `None` until the first successful `.login()`. Its optionality is the mechanism that drives the lazy-login behavior in `.request()`. |
| `AgentSwitchClient.user_info` | `Optional[Dict[str, Any]]` | Cached result of `.get_me()`; `None` until called. Not read anywhere else in this file — it exists for callers that want identity info without a second `/api/auth/me` round-trip. |
| `AgentSwitchClient._ctx` | `ssl.SSLContext` | The unverified TLS context reused across every request via `urllib.request.urlopen(req, context=self._ctx)`. |

## Function/method reference

| Function | Inputs → Output | Notes |
|---|---|---|
| `get_environment_config(env_name)` | `str` → `Dict[str,str]` | Raises `ValueError` on unknown tenant name or missing password. Pure — no I/O. |
| `AgentSwitchClient.login()` | none → `str` (token) | The only method that can populate `self.token`. Side effect: mutates `self.token`. |
| `AgentSwitchClient.get_me()` | none → `Dict[str, Any]` | Side effect: mutates `self.user_info`. |
| `AgentSwitchClient.request(method, path, payload, headers)` | → `Dict[str, Any]` | The universal REST call. Side effect: may trigger a login. |
| `AgentSwitchClient.call_mcp(method, params, rpc_id)` | → `Dict[str, Any]` (raw JSON-RPC envelope) | Does **not** raise on a JSON-RPC `"error"` — that's the caller's job. |
| `AgentSwitchClient.init_mcp()` | → `Dict[str, Any]` (the `initialize` result) | Must be called once per client instance before any `tools/call`. |
| `AgentSwitchClient.list_mcp_tools()` | → `Dict[str, Any]` | Thin wrapper; exists for readability at call sites. |

## How to test it

There are currently **no automated tests anywhere in this repository**. For this file specifically, the honest constraint is: almost everything it does is "make a real network call to a live, shared platform" — so the test strategy has to separate the part that's pure logic (safe to unit-test with no network) from the part that's an integration test (needs either the live platform or a mock HTTP layer).

- **Pure/unit-testable today, with no mocking**: `get_environment_config` — feed it env vars via `monkeypatch.setenv`, assert the returned dict, assert `ValueError` on a missing password and on an unknown tenant name.
- **Needs a mock HTTP layer** (e.g. `httpx`'s `MockTransport` doesn't apply since this uses `urllib`; use `unittest.mock.patch("urllib.request.urlopen")` or swap in a `responses`/`requests-mock`-style library, or refactor the transport to `httpx` so it's mockable the same way the gateway is — see trade-offs above): `.login()` success and failure paths, `.request()`'s auto-login-if-no-token behavior, `.call_mcp()`'s envelope construction (assert the POST body has the right `jsonrpc`/`id`/`method`/`params` shape without caring what comes back).
- **Integration test, needs `.env` + live platform** (mark with a `requires_live_api`-style pytest marker so CI can skip it): `.login()` against real Suryodaya, `.init_mcp()` + `.list_mcp_tools()` returning a non-empty tool list, and a full round trip calling a real tool like `DesignFile.get`.

## Tests that should be added for stability

In priority order (highest-value first):

1. **A test that a JSON-RPC error response (HTTP 200, `{"error": {...}}` body) is distinguishable from a JSON-RPC success** — this is the exact footgun PLAN.md §3 calls out ("JSON-RPC errors return HTTP 200 — must check the envelope, not just the status code"), and there is currently no test proving any caller does this correctly. A regression here would silently start treating tool errors as successful, empty results.
2. **A test for expired-token handling.** Right now a 401 mid-session raises a bare `RuntimeError` and there's no retry-with-re-login. Decide the intended behavior (auto-relogin-once-and-retry vs. fail loud) and write a test that pins it down — right now it's undefined/untested behavior.
3. **A test that `.request()` never sends a payload body on a bodyless call** (`payload=None` → `data=None` in the `urllib.request.Request` — confirm no `Content-Length: 0`-style edge case breaks a GET).
4. **A test for the manual `.env` fallback parser** (the `except ImportError` branch) — it's dead code on any machine with `python-dotenv` installed, which means it currently has zero test coverage and could silently rot.
5. **A contract test against a fixture of a real `tools/list` response** (save the JSON once, replay it) so schema assumptions made elsewhere in this codebase (e.g. `capstone_agent.py`'s hardcoded tool names/param shapes) have something to be checked against without hitting the live network on every test run.
