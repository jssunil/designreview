# `as_client.py` — the compatibility client

*Reading order: **12th** (only if you use it directly). New code uses `agentkit/transport`.*

Kept so existing scripts (`file_platform_bugs.py`, the graded `tests/conftest.py`) keep working:

- `get_environment_config(env) -> {"email", "url", "password"}` — now resolved from `config/tenants.toml`.
- `AgentSwitchClient(env)` — `login()`, `get_me()`, `request(method, path, payload)`, `call_mcp(method, params)`
  (returns the raw JSON-RPC envelope, errors included — the caller checks it), `init_mcp()`, `list_mcp_tools()`.
- `AgentSwitchClient.seat()` — an `agentkit` `SeatRpcClient` reusing this login.

Changes from the first draft: TLS verified by default (`AS_INSECURE_TLS=1` opts out), a timeout on every
request, increasing JSON-RPC ids, `read_body()` for the platform's mis-framed chunked responses, and
**importing the module loads `.env`** (the graded `tests/conftest.py` relies on this).

How to test: `tests/test_as_client.py` — monkeypatch `as_client.urllib.request.urlopen`.
