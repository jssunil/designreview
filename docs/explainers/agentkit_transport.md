# `agentkit/transport/` — talking to AgentSwitch

*Reading order: **1st**. Files: `wire.py`, `seat_rpc.py`, `rest_reader.py`, `journal.py`. Depends only on `agentkit/config.py`.*

## 10,000-ft view

Everything the agent and the harness say to the platform goes through here. `wire.py` is the only code
that opens a socket; `seat_rpc.py` speaks JSON-RPC 2.0 to `/api/mcp`; `rest_reader.py` is a separate,
GET-only REST client for the verifiers; `journal.py` records every tool call a run makes.

## Why it's written this way

- **A one-method `Wire` seam.** `send(method, url, headers, body, *, safe_to_repeat) -> WireReply` is all the
  rest of the code needs, so tests and the offline simulator replace the network without patching urllib.
- **Typed failures, not strings.** A tool call either returns its payload or raises `SeatCallFailure` whose
  `kind` says what to do next: `missing` (refuse), `bad_args`, `bad_transition`, `not_in_seat` (structural
  refusal), `denied`, `token_expired`, `flaky` (a read may retry), `unclassified`. The platform's own
  `error.data.code` decides the kind wherever it exists. JSON-RPC errors arrive with HTTP 200, so the envelope
  is always inspected; `result.isError` is a failure too.
- **Retry only what can't have been applied.** Reads (`.list`, `.get`, declared read-only endpoints) are
  retried on connection errors and 502/503/504; a write gets exactly one attempt, because a gateway error can
  arrive after the server applied it. A 401 means the request was never processed, so it triggers one shared
  re-login and one resend — safe even for writes, and done once even when ten threads hit it together.
- **TLS verified by default.** The platform's certificates verify; `AS_INSECURE_TLS=1` is an explicit opt-out.
- **A live framing fault is absorbed.** On 2026-09-28 every `/api/mcp` response declared
  `Transfer-Encoding: chunked` but sent a plain JSON body, which standard clients reject. `read_body()` detects
  a body that isn't really chunked and reads to end-of-stream (the server also sends `Connection: close`).
- **The verifiers get their own path.** `RestReader` logs in separately and only GETs, so a bug in how the
  agent reads the platform can't pass its own check.
- **The journal is the evidence.** `JournaledSeatClient` appends one line per call (seq, tool, args, read or
  write, ok/error kind, a small result summary, seconds) to `tool_journal.jsonl`. Graders use it to prove "a dry
  run never wrote" without trusting the agent's own report. The token and headers are never logged.

## What it does

1. `SeatSession` holds one tenant's login (from `config/tenants.toml`) and the token, with a lock for re-login.
2. `SeatRpcClient.initialize()` does the MCP handshake; `seat_catalog()` pages `tools/list` (cursor) and caches
   it; `offers(tool)` checks it; `invoke_tool(name, args)` returns the unwrapped payload
   (`structuredContent` → JSON in `content[].text` → raw result); `fetch_every_page()` pages a `.list` tool and
   de-duplicates by id.
3. `RestReader.get / list_all / raw` read REST with 404 → `missing`.
4. `JournaledSeatClient(client, path)` wraps any client with `invoke_tool`.

## Key types

| Name | Purpose |
|---|---|
| `SeatCallFailure(kind, tool, raw, field_errors, http_status)` | every failure; `.retryable` is true only for `flaky` |
| `SeatSession` | login + token + renew lock, shared by all users of one tenant |
| `SeatRpcClient` | MCP tools for one seat; thread-safe ids |
| `RestReader` | GET-only REST for ground truth |
| `UrllibWire` / `WireReply` / `WireUnreachable` | the network seam |
| `JournaledSeatClient`, `read_journal()` | per-run call log |

## How to test

Offline with a fake `Wire` (see `tests/test_seat_rpc.py`). Live: `SeatRpcClient.for_tenant("suryodaya")`.
