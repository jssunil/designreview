# `agentkit/service/` — the agent as a service

*Reading order: **11th**. Files: `core.py`, `http.py`, `mcp.py`, `hooks.py`, `__main__.py`. Config: `config/hooks.toml`. Added 2026-10-21. Written in ASD-STE100
(Simplified Technical English) style.*

## Overview

```bash
python -m agentkit.service                                         # live platform, http://127.0.0.1:8780/
python -m agentkit.service --sim packs/designreview/fixtures --skip-llm   # offline, template answers
python -m agentkit.service --mcp-stdio                             # an MCP server on stdin and stdout
```

The service gives the agent to other programs. A program sends one question. The service sends back one
audited answer. The service uses the same parts as a harness run:

```
POST /v1/ask {question, tenant}
   │
   ▼
AgentService.ask ── slot (max_in_flight) ── busy? ──► 429 + Retry-After
   │
   ▼
run_task(TaskDef(plan = "route", prompt = question), write = False)
   ├── router: plan + parameters from the question (or a refusal)
   ├── engine: the plan's nodes ── span events ──► SSE (when the caller asks for a stream)
   ├── finding (code) ── narrator ── claim audit ── template fallback
   └── runs/svc-<time>-<id>/  (task, run record, journal, graph, ledger, telemetry)
   │
   ▼
{run_id, answer, answer_source, route, outcome, finding, warnings, cost_usd, seconds}
```

## API

| Route | Result |
|---|---|
| `GET /healthz` | `{ok, pack, mode, llm, in_flight, max_in_flight, tenants}`. It needs no token. |
| `GET /v1/playbooks` | The live playbooks: `id`, `title`, `plan`, example `questions`, `writes`. |
| `POST /v1/ask` | Body `{"question": "...", "tenant": "..."}`. The tenant is optional. The result is the answer, the route, the outcome, the finding and the run id. |
| `POST /v1/ask` with `Accept: text/event-stream` | Server-Sent Events while the run works: `run_start`, `node_start`, `node_end`, `tool_*`, `llm_*`, `step_*`. The last event is `answer`, with the same result as above. |
| `GET /v1/runs/<run_id>` | The run record of an earlier service run. |
| `GET /v1/runs/<run_id>/history` | The graph checkpoints of that run, newest first: id, time, source, step and the state of each node. |
| `POST /mcp` (also `/api/mcp`) | The MCP server. See "The agent as an MCP tool". |
| `POST /v1/hooks/<name>` | A signed webhook. The result is 202 with the run id. The run continues, and its record is at `/v1/runs/<run_id>`. See "Triggers". |

A refusal is a normal answer (200), for example an unknown part or a question that can mean four parts.
The errors are JSON `{"error": "..."}`:

| Status | Cause |
|---|---|
| 400 | The body is not a JSON object, the question is empty or longer than 2000 characters, or the tenant is unknown. |
| 401 | A token is set, and the request does not have `Authorization: Bearer <token>`. |
| 404, 405 | An unknown path, or a method that the path does not accept. The response has `Allow`. |
| 413 | The body is larger than 64 KB. |
| 429 | All the places are in use. The response has `Retry-After`. |
| 500 | An internal error. The response has no stack trace. |

## The agent as an MCP tool

Other agents can call this agent as a tool, for example Claude Code, an IDE or the agent of another team.
`mcp.py` is a small MCP server: JSON-RPC 2.0 with `initialize`, the notifications, `ping`, `tools/list`
and `tools/call`. It has two transports:

- **stdio**: `python -m agentkit.service --mcp-stdio`. The MCP client starts the process. Each line is one
  message. While the server works on a message, `print` goes to stderr. Thus no text from the agent can
  break the message stream.
- **HTTP**: `POST /mcp` on the service. A notification gets 202 with no body. The token rule of the
  service applies.

The tools come from the live playbooks of the pack:

| Tool | Source |
|---|---|
| `pb_01`, `pb_02` | One tool for each live playbook. The description has the title and the example questions. |
| `ask` | Any question. The router picks the use case or gives a refusal. |

A blocked or spec-only playbook is not a tool. The name of the tool helps the caller. The router still picks
the plan from the question, and the result tells which plan ran.

| Result field | Contents |
|---|---|
| `content` | The audited answer as text. |
| `structuredContent` | `answer`, `run_id`, `tenant`, `answer_source`, `route`, `outcome`, `ended`. |
| `isError` | True only when the run failed or the service is busy. A refusal is a normal result. There is never a stack trace. |

An unknown tool or bad arguments give the JSON-RPC error -32602. An unknown method gives -32601.

To add the agent to Claude Code, run this command in the project folder:

```bash
claude mcp add designreview -- python -m agentkit.service --mcp-stdio
```

## Triggers: webhooks, cron and n8n

The agent answers when a person asks. A trigger lets another system ask: a schedule, or an event such as a
new version of a part.

**Signed webhooks.** `config/hooks.toml` gives each hook a name and a question template:

```toml
[[hooks]]
name = "new_version"
question = "What changed in the latest revision of {part}, and are there manufacturability problems?"
```

| Rule | Detail |
|---|---|
| Secret | A hook is off until `AGENTKIT_HOOK_<NAME>_SECRET` has a value. A hook that is off gives 404, as an unknown hook does. |
| Signature | `X-Agentkit-Signature: sha256=<hex>`: HMAC-SHA256 of `<timestamp>.` and the body, with the secret. A wrong signature gives 401. |
| Time | `X-Agentkit-Timestamp`: Unix seconds. More than 5 minutes from the clock of the service gives 401. |
| Replay | The same signature two times gives 409. |
| Fields | The `{field}`s of the template come from the JSON body. Each value must be short and plain: letters, digits, spaces and `. _ : / # -`. Thus a field cannot add structure to the question. Another value gives 400. |
| Token | A hook does not need the bearer token. Its signature proves the sender. |

A refused delivery starts no run. An accepted delivery gets 202 at once, and the run continues in the
background. It is the same dry run as `POST /v1/ask`. To send a signed delivery from a script or a cron job:

```bash
AGENTKIT_HOOK_NEW_VERSION_SECRET=... python -m agentkit.service.hooks send http://127.0.0.1:8780 new_version '{"part": "BEV-BT-2400"}'
```

**Cron.** A cron line can ask a question each weekday morning with the bearer token:

```bash
30 7 * * 1-5  curl -s -H "Authorization: Bearer $AGENTKIT_SERVICE_TOKEN" -H "Content-Type: application/json" \
              -d '{"question": "Is BEV-BT-2400 ready for release?"}' http://127.0.0.1:8780/v1/ask >> release.log
```

**n8n.** Import `docs/examples/n8n_daily_release_check.json`: a Schedule Trigger (weekdays at 07:30) and an
HTTP Request to `/v1/ask`. Give the HTTP Request a Header Auth credential, `Authorization: Bearer <token>`. An
n8n workflow can also call the MCP server at `/mcp`.

Playbooks pb-14 (daily digest) and pb-15 (review of each new version) stay "spec". The triggers are ready,
but pb-14 needs its own plan, and pb-15 needs an event from the platform and a write path for the answer.

## Design decisions

- **The same run as the harness.** A question becomes a routed task (`plan = "route"`). Thus the router,
  the plan, the finding, the claim audit and the run folder are the same as in a graded run. The viewer,
  `grade` and `replay` work on a service run.
- **Dry run only.** The service never writes to the platform. A write needs a person's approval, and the
  service has no person in the loop. The CLI and the harness keep the approval step.
- **Back-pressure, no queue.** At most `max_in_flight` questions run at the same time (default 4). One more
  question gets 429 at once. Thus a burst waits at the caller, and the service never collects work that it
  cannot do. This is the rule in `SCALING_AND_DEPLOYMENT.md`.
- **Progress from the spans.** The service adds a handler to the run's tracer. Each span start and end
  becomes an event. An event has names, statuses, times and counts. It never has a prompt or a key. A
  listener that fails (for example, the caller closed the connection) does not stop the run.
- **History for other processes.** The graph checkpoints of each run go to one SQLite file,
  `runs/service.sqlite` (`agentkit.graph.SqliteSaver`). Another process can open it, and list, examine or fork
  any run. `history=False` turns it off.
- **Standard library only.** `http.server` and `queue`. No web framework and no new dependency.
- **Offline mode.** `--sim` replays the captured fixtures, with the clock at the capture time, as the batch
  does. CI uses it.

## Safety

| Risk | Guard |
|---|---|
| Other computers ask questions | The server binds to 127.0.0.1. Another host does not start without `AGENTKIT_SERVICE_TOKEN`. |
| A wrong token | `hmac.compare_digest`, so the time of the compare does not tell the token. |
| Path traversal in `/v1/runs/` | The id must start with `svc-`, must agree with a strict pattern, and must be a direct child of the runs folder. |
| Text that becomes markup | JSON escapes `<`, `>` and `&` as `\u` sequences. |
| Large bodies | 64 KB. The server reads no more. |
| Platform writes | `run_task(write=False)`: each tool call in the journal is a read. |
| Instructions in the question | The question goes to the router as data. The narrator gets the finding inside the data fence. |

## Key types

| Name | Use |
|---|---|
| `AgentService(pack, runs_dir, sim=, skip_llm=, max_in_flight=)` | The service. |
| `ask(question, tenant, on_event)` | Check the question, take a place, run, give the result. |
| `check(question, tenant)`, `slot()`, `run(...)` | The three steps of `ask`, separate for the stream. |
| `Busy`, `BadRequest` | 429 and 400. |
| `ServiceApp.handle(method, path, headers, body)` | The JSON routes, with no socket. |
| `ServiceApp.stream(path, headers, body)` | An error response, or the SSE chunks of one run. |
| `make_server(service, host, port, token=)` | The HTTP server. |
| `McpServer(service)`, `handle(message)`, `tools()`, `call_tool(name, arguments)` | The MCP server. |
| `serve_stdio(server)` | MCP on stdin and stdout. |
| `HookSpec`, `load_hooks()`, `HookGate.question(name, headers, body)`, `signature()`, `send()` | The webhooks. |

## How to test

1. The reference tests are `tests_local/test_service_http.py`, `test_service_mcp.py` and
   `test_service_hooks.py`. They
   use the fixtures and need no network. One MCP test uses our own MCP client
   (`agentkit/transport/seat_rpc.py`) on the HTTP transport.
2. Run `python scripts/ci_service_smoke.py`. It starts the service as its own process on the fixtures and
   asks each routed task's question over HTTP. Then it uses our own MCP client for `tools/list` and
   `tools/call`. CI runs it.
3. Run the service with `--sim packs/designreview/fixtures --skip-llm`. Then send a question:

```bash
curl -s localhost:8780/v1/ask -H "Content-Type: application/json" \
     -d '{"question": "Is the propeller ready for release?"}'
curl -N localhost:8780/v1/ask -H "Accept: text/event-stream" -H "Content-Type: application/json" \
     -d '{"question": "What changed between rev B and rev C of the Bharat EV battery tray?"}'
```
