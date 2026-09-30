# `agentkit/viewer/` — the read-only run viewer

*Reading order: **9th**. Domain-free: it shows whatever the harness wrote, for any pack.*

## 10,000-ft view

```bash
python -m agentkit.viewer                    # http://127.0.0.1:8765/
python -m agentkit.viewer --runs-dir /tmp/runs --port 9000
```

A small local web page over the run folders (`runs/<run_id>/`, `runs/batches/<batch_id>/`). Everything it
shows was already on disk; it only makes the evidence easy to read, which matters when you're explaining a
graded run or finding out why a check failed.

| Page | Shows |
|---|---|
| **Batches** | every batch, newest first: runs, status counts, LLM on/off, calibration ok |
| **All runs** | every run folder, including runs made outside a batch (`capstone_agent.py --task`) |
| **Batch** | task × tenant table with each run's status and failing checks; calibration per mutant; `report.md` |
| **Run → Answer** | the answer, its source (llm / template), claim-audit result, each attempt, warnings |
| **Run → Finding** | the finding as a collapsible tree — generic, so any pack's finding schema works |
| **Run → Checks** | every check with status and detail |
| **Run → Evidence** | the graph as a timeline: node, action, verdict, dependencies, who added it (plan / planner), tries, time bar, reason |
| **Run → Tool calls** | the tool journal; writes are highlighted |
| **Run → Ground truth** | each observation before and after the run; the ones that changed (drift) are marked and diffed |
| **Run → Cost** | LLM calls: provider, model, tokens, cost, latency, stop reason, reported vs estimated usage |

## Why it's written this way

- **No framework, no build, no new dependency.** The server is the standard library's `http.server`; the
  page is one HTML file, one script and one stylesheet. This keeps the "no agentic frameworks" rule easy to
  audit and means nothing to install.
- **Read-only by construction.** Only GET and HEAD are handled; every other method gets 405. Nothing in the
  module writes a file. Starting runs or filing hand-offs is deliberately not here (see Part 3 of the plan).
- **The logic is testable without a socket.** `ViewerApp.handle(path)` returns `(status, content type,
  body)`; `make_server()` is a thin wrapper.
- **Domain-free.** The viewer reads the harness's file formats (task, taskrun, score, graph, journal,
  ground truth, ledger, manifest, calibration); the finding is shown as a generic tree.

## Safety

Run data contains platform text written by other teams, so it's treated as hostile:

| Risk | Guard |
|---|---|
| Path traversal (`../`, `%2F`, `\`, symlinks) | ids must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$`, contain no `..`, and resolve to a direct child of the runs (or batches) folder |
| Serving other files | static files come from a fixed whitelist (`index.html`, `app.js`, `style.css`) |
| Text becoming markup (XSS) | the page builds every element with `textContent`, never `innerHTML`; links go only to `#/` routes; JSON responses also `\u`-escape `<`, `>` and `&` |
| Injected scripts | `Content-Security-Policy: default-src 'self'; script-src 'self'; …`, no inline script, no third-party assets; `X-Content-Type-Options: nosniff` |
| Other machines reading run data | binds to `127.0.0.1` by default; any other `--host` prints a warning |

## API

| Route | Returns |
|---|---|
| `GET /api/batches` | `[{batch_id, started_at, finished_at, skip_llm, write, runs, status_counts, calibration_ok}]` |
| `GET /api/runs` | `[{run_id, task_id, tenant, ended, started_at, score_status}]` |
| `GET /api/batches/<id>` | `{manifest, runs: [entry + score_status, counts, failing], report, calibration}` |
| `GET /api/runs/<id>` | `{task, taskrun, score, graph, journal, before, after, ground_truth_changes, ledger, stdout}` |

A missing file is `null`, and a file that doesn't parse is `{"_unreadable": "<name>"}`. The viewer shows a
half-written or crashed run rather than failing on it.

## How to test

`tests/test_viewer.py` (offline; the to-do list is in its docstring). Build a runs folder in `tmp_path`,
by hand or with an offline sim batch, and call `ViewerApp(runs_dir).handle(path)`. For the HTTP layer,
`make_server(runs_dir, port=0)` runs in a thread.
