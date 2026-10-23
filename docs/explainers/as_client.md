# `as_client.py` — the compatibility client

*Reading order: **16th**. Read it only if you use this client directly. New code uses `agentkit/transport`.
Written in ASD-STE100 (Simplified Technical English) style, 2026-10-14.*

## Overview

This file stays in the project for old scripts. Two of them are `file_platform_bugs.py` and the graded
`tests/conftest.py`.

| Name | Use |
|---|---|
| `get_environment_config(env)` | Gives `{"email", "url", "password"}`. The values come from `config/tenants.toml`. |
| `AgentSwitchClient(env)` | Has `login()`, `get_me()`, `request(method, path, payload)`, `init_mcp()` and `list_mcp_tools()`. |
| `AgentSwitchClient.call_mcp(method, params)` | Gives the raw JSON-RPC envelope, with the errors. The caller must examine it. |
| `AgentSwitchClient.seat()` | Gives an `agentkit` `SeatRpcClient` that uses the same login. |

## Changes from the first draft

- The client verifies TLS. Set `AS_INSECURE_TLS=1` to stop the verification.
- Each request has a timeout.
- The JSON-RPC ids increase with each request.
- `read_body()` reads the chunked responses that the platform frames incorrectly.
- When Python imports this module, the module loads `.env`. The graded `tests/conftest.py` needs this.

## How to test

The hand-written template is `tests/test_as_client.py`. Replace `as_client.urllib.request.urlopen` with
`monkeypatch`.
