# `agentkit/service/` — the agent as a service

*Reading order: **11th**. Files: `core.py`, `http.py`, `__main__.py`. Added 2026-10-21. Written in ASD-STE100
(Simplified Technical English) style.*

## Overview

```bash
python -m agentkit.service                                         # live platform, http://127.0.0.1:8780/
python -m agentkit.service --sim packs/designreview/fixtures --skip-llm   # offline, template answers
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

## How to test

1. The reference tests are `tests_local/test_service_http.py`. They use the fixtures and need no network.
2. Run `python scripts/ci_service_smoke.py`. It starts the service as its own process on the fixtures and
   asks each routed task's question over HTTP. CI runs it.
3. Run the service with `--sim packs/designreview/fixtures --skip-llm`. Then send a question:

```bash
curl -s localhost:8780/v1/ask -H "Content-Type: application/json" \
     -d '{"question": "Is the propeller ready for release?"}'
curl -N localhost:8780/v1/ask -H "Accept: text/event-stream" -H "Content-Type: application/json" \
     -d '{"question": "What changed between rev B and rev C of the Bharat EV battery tray?"}'
```
