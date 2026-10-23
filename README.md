# AgentSwitch — Design Review Seat (`designreview`)

| | |
|---|---|
| **Project** | EAGv3 Capstone Project, **Team 21** |
| **Assigned seat** | Design Review (`designreview`): Design Files, Versions, Reviews |
| **Team members** | Sunil Jakkaraju ([@jssunil](https://github.com/jssunil)), Bharath KR ([@bharathkrd](https://github.com/bharathkrd)) |
| **Core request** | *"What changed between rev B and rev C, and are there manufacturability problems in this part?"* |

*Written in ASD-STE100 (Simplified Technical English) style, 2026-10-15. Updated 2026-10-19. CI examines this
file with `scripts/ste_check.py`.*

---

## 📌 Project Overview

**AgentSwitch** is a multi-agent business-operations platform for enterprises. It has 424 entity types. Two
simulated manufacturing companies use it:

1. **Suryodaya Precision Works** (India — GST / Ind AS)
2. **Keystone Precision Works LLC** (US — Sales & Use Tax / ASC)

Each team works in one **seat**. A seat has specified entity permissions, API scopes and MCP interfaces. Our
seat is **Team 21 (`designreview`)**. The seat brief asks for an autonomous agent that uses MCP to do these
tasks:

- compare design revisions;
- examine engineering checklists;
- find manufacturability (DFM and DFA) problems.

---

## 🎯 The Core Agent Goal

The agent must answer this question autonomously, through the Model Context Protocol (MCP) and REST APIs:

```text
"What changed between rev B and rev C, and are there manufacturability problems in this part?"
```

What the agent does to answer it:

- **The revision change comes from the notes of the engineers.** The agent reads rev letters ("Rev B",
  "Rev 2") from each `DesignVersion.commit_message`. Thus it maps rev B to the correct version, also when rev B
  is not v2.
- **No geometry claims.** This platform build has no CAD kernel. Geometry diffs, wall-thickness checks and
  interference checks give `501`. Thus the answer says clearly that the agent did not compare geometry.
- **Manufacturability comes from platform evidence:**
  - the release-readiness gate (ready, reasons, critical blockers);
  - open high and critical `DesignFeedback`;
  - failed DFM checklist results;
  - the `DesignStandard` records of the company that apply to the change.
- **A refusal when the data or the seat cannot support an answer.** One example is an unknown file. Another
  example is a request for a tool that this seat does not have, such as the AI review of the platform.
- **A refusal when the question is not clear.** If a part name matches more than one file, the agent names
  the candidates. It does not guess.

---

## 📋 Use cases (playbooks)

[`packs/designreview/playbooks/`](packs/designreview/playbooks/README.md) has one file for each use case of the
seat: 2 live, 3 blocked and 10 spec only. The head of each file gives the status, the plan, example questions,
the seat tools and the tasks that test it. The body tells a person how the use case works and its limits.

| Status | Use cases |
|---|---|
| **Live** | pb-01 revision change and manufacturability review; pb-02 release readiness, schedule and hand-offs |
| **Blocked** | pb-03 the AI review of the platform. pb-04 a wall-thickness analysis. The seat does not have these tools. pb-05 the release of a file: it needs the `design_admin` role and a person's decision. |
| **Spec only** | Release impact, BOM change, supplier response time and open review feedback. Characteristics with no tolerance, assemblies that use a part, external shares and checklist coverage. A daily digest, and a review of each new version. |

The playbooks do not drive the agent. The plans and the routes do that. `scripts/playbooks_check.py` proves
each status in CI:

- the questions of a live or blocked playbook route to its plan, and the agent declines spec-only questions
  today;
- each seat offers each tool, and a "missing" tool is absent;
- each task and each plan belongs to one playbook.

[`constants.toml`](packs/designreview/playbooks/constants.toml) lists 16 numbers that the rules use, with the
source of each. The check fails when the code has a different value.

---

## 🏗️ Architecture

The code has three layers. A lower layer never imports a higher layer.

```
agentkit/            domain-free framework (no design-review names; a lint test enforces it)
  agents/            run a plan, record its steps, word the answer; router: question → plan + params;
                     LLM planner for questions that no route matches (experimental, off: AGENTKIT_PLANNER=llm)
  memory/            what changed since the last audited answer about a record (experimental, off)
  transport/         MCP client (typed errors, re-login, read-only retries, argument check), REST reader, tool-call journal
  llm/               multi-provider LLM gateway (tiers, classified retries, real token usage, cost ledger, call log, model check)
  graph/             live task graph: verdicts, allowlisted actions, rule planner, timeouts, checkpoints
  answer/            claim audit + narrator (cheap draft → quality tier → template), data fence, quarantine
  retrieval/         topic chunker, BM25 evidence index, parent/child index, quality metrics, relevance gate
  record/            crash-safe run record (TaskRun) and atomic writes
  harness/           tasks, ground-truth capture, checks, batch runner, grader, verifier self-test
  sim/               platform fixtures + offline SimPlatform with injectable faults
  telemetry/         run tree for each run (task → node → tool / llm); OTLP export without an SDK
  viewer/            read-only browser page over the run folders
  service/           the agent as a service: HTTP API (JSON and Server-Sent Events), MCP server, signed webhooks; dry run only
  playbooks.py       playbook files and the constants register: load, check against plans, routes and tools
packs/designreview/  the domain pack: actions, routes, finding, claim rules, template, probes, verifiers,
                     mutants, prompts/*.md, plans/*.toml, tasks/*.toml, fixtures/*.json,
                     playbooks/*.md (one per use case) + constants.toml
config/              tenants.toml, routing.toml (LLM providers and tiers), project.toml (defaults, LLM cost limit), hooks.toml (webhooks)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the layers and the pack seam.

### How the agent answers one question

1. **Route.** A task can give only the question (`plan = "route"`). Then the router of the pack chooses the
   plan and finds the design file by its name. If the name is not clear, the router declines.
2. **Plan** (`packs/designreview/plans/rev_diff_dfm.toml`). The agent reads the design file first. Then it
   reads the revisions, the release gate, the feedback and the standards in parallel. A declarative rule adds
   a read of the failed DFM checklists only when the gate shows blockers.
3. **Actions.** Nodes can name only registered actions (an allowlist). The only write action is
   `raise_handoffs`. It runs only when you give `--write` and the task permits writes. Each other run is a
   dry run.
4. **Evidence.** Each MCP call goes through a journal (`tool_journal.jsonl`). The journal also validates the
   arguments before each call. The engine saves a checkpoint of the graph after each step (`graph.json`).
5. **Finding** (`packs/designreview/finding.py`). Code calculates the answer that the harness grades:
   - the rev-to-version mapping;
   - the change notes and their quantities;
   - the release verdict and the blockers;
   - the DFM signals;
   - the refusals.
6. **Answer.** The LLM only writes the finding as text:
   1. A cheap model (the `fast` tier) writes a draft.
   2. The claim audit compares the draft with the finding. It rejects invented quantities, record ids and
      standards, "ready" when the gate says "not ready", geometry claims, and missing facts.
   3. A rejected draft goes to a stronger model (the `quality` tier), with the verified values.
   4. If no draft passes, or no LLM is available, a deterministic template gives the answer.

### LLM providers

`config/routing.toml` sets the providers and their sequence in each tier. The gateway skips a provider that
has no key in `.env`.

| Tier | Sequence |
|---|---|
| `fast` (cheap drafts) | OpenAI gpt-6-luna → DeepSeek deepseek-flash → Moonshot kimi-k2.6 → Gemini 3.5 Flash-Lite → Claude Haiku 5.5 → Groq, Cerebras, NVIDIA → LM Studio → Ollama |
| `quality` (rejected drafts) | OpenAI gpt-6.1-sol → DeepSeek deepseek-v4-pro → Moonshot kimi-k2.6 → Gemini 3 Flash → Claude Sonnet 5.5 → OpenRouter, NVIDIA → LM Studio → Ollama |

A new OpenAI-compatible provider is one `[[providers]]` row with a `url`. It needs no code change. Each run
records the provider and the model of each LLM call. See
[`docs/explainers/agentkit_llm.md`](docs/explainers/agentkit_llm.md).

### How the harness grades a run

Each task is a TOML file in `packs/designreview/tasks/`. For each task, the harness does these steps:

1. It reads the platform facts that its verifiers need, **with its own login**, before the run.
2. It runs the agent **in a subprocess**.
3. It reads the platform facts again after the run.
4. It saves all data to `runs/<run_id>/`: `task.json`, `taskrun.json` (written first, as
   `ended="running"`), `tool_journal.jsonl`, `graph.json`, `ground_truth_before/after.json`,
   `llm_ledger.json`, `telemetry.json`.
5. It **grades from those files only**. Thus a correction to a verifier costs nothing: grade again.

The check results are `pass`, `fail`, `drift`, `skip` and `error`:

- `drift` means that the data changed during the run.
- `error` never counts as a pass.

The verifiers use their own parsers for rev letters, quantities and readiness wording. Thus an error in the
agent cannot make its own check pass.

| Task | What it proves |
|---|---|
| `t01_battery_tray_rev_diff` | B→v2, C→v3; bend radius 2.0→3.0 mm, blank +4.2 mm; NOT ready, with a critical blocker. |
| `t02_unknown_file_refusal` | Refuses after one lookup. Reads nothing else and invents nothing. |
| `t03_ai_review_refusal` | Examines the seat catalogue, never calls the absent tool, and declines. |
| `t04_bench_vice_no_geometry` | Numeric revs. Reports the saving of 180 g. Makes no geometry claim. |
| `t05_propeller_rev_mapping` | Rev B is v1. The mapping comes from the commit note, not from the position. |
| `t06_keystone_boilerplate_notes` | **Keystone.** The rev B and C notes say only "drawing and model updated together". The answer reports exactly that and invents no changes. |
| `t07_keystone_latest_dfm_fixes` | **Keystone.** The notes have no rev labels. The agent compares the latest two versions ("DFM fixes: increased draft angles, added rib reinforcement") and recalls the DFM guidelines of the company. |
| `t08_battery_tray_handoff` | NOT ready. Proposes one hand-off to **manufacturing** (cracking bend radius, weld access) and one to **quality** (PPAP level 3). Cites exactly those items. Shows the escalation that each would file, and files nothing. Names the past-due milestone "DFM review closed". |
| `t09_keystone_no_handoff_late_schedule` | **Keystone.** Each open problem is work for design review, so the agent proposes **no** hand-off (an invented hand-off fails). Names three past-due critical-path milestones. Never says "on track". |
| `t10_routed_battery_tray` | **Routed** (`plan = "route"`). The task gives only the question. The router must choose the plan and find DF-2026-00001 by its name. Then the checks of t01 apply. |
| `t11_routed_ambiguous_propeller` | **Routed refusal.** "The propeller" matches four files, and no revision makes the choice smaller. The agent declines and names them. |
| `t12_routed_keystone_schedule` | **Routed, Keystone.** Only the question of t09. The router must find the housing cover, not the float arm or the pivot of the same drive. |

### The verifiers test themselves

- **Mutants.** `agentkit.harness.mutants` takes passing runs and changes one thing in a copy. Examples are a
  wrong rev mapping, an invented quantity, a false "ready" claim, a hidden write and a crashed run. The
  matching check must fail. Calibration fails if a mutant passes, or if no mutant can make a check fail.
- **Replay faults.** `agentkit.harness.replay --calibrate` goes one level deeper. It changes the result of one
  graph node (for example, the release gate says "ready"). It runs all nodes after it again, offline.
- **The replay result.** The replay makes the finding and the answer again. The target check must fail. A control replay with the original result
  must still pass.

### Offline runs

- `python -m agentkit.sim.capture --tenant <tenant>` records each exchange that the tasks of that tenant
  need. It writes `packs/designreview/fixtures/<tenant>.json`.
- `--sim packs/designreview/fixtures` replays each task on the fixture of its tenant. All 12 tasks run in a
  few seconds.
- You can add faults, for example `drop_tool:…`, `fail_once:…:flaky` or `inject_text:…` (an instruction in
  platform data).
- With `edit_text:old=>new`, another team changes a record during the run. The grade must be `drift`.

### Constraints

- **No agent or workflow frameworks.** The loop, the graph, the planner, the router, the retrieval, the
  harness and the telemetry are plain Python.
- **Few dependencies:** `httpx`, `pydantic` (it validates the config, plan, task and fixture files),
  `python-dotenv` and `pytest`.
- **Configuration is TOML.** The `tomllib` module of the standard library reads it.

---

## 📂 Repository Map

| Path | Purpose |
| :--- | :--- |
| [`PLAN.md`](PLAN.md), [`GAP_REPORT.md`](GAP_REPORT.md), [`research.md`](research.md) | Seat plan, competitive gap report, research notes |
| [`ENTITY_SCHEMAS.md`](ENTITY_SCHEMAS.md), [`agentswitch.md`](agentswitch.md) | `designreview` entities and state machines; platform guide |
| [`capstone_agent.py`](capstone_agent.py) | Run the agent: `--task <file.toml>` (or the default T01 question) |
| [`capstone_evals.py`](capstone_evals.py) | Run the full task set and grade it |
| [`rescore.py`](rescore.py) | Grade saved runs again (files only; no network, no LLM) |
| [`as_client.py`](as_client.py) | Compatibility client (`AgentSwitchClient`); `.seat()` gives the new MCP client |
| [`agentkit/`](agentkit/) · [`packs/designreview/`](packs/designreview/) · [`config/`](config/) | Framework · domain pack · configuration |
| [`packs/designreview/playbooks/`](packs/designreview/playbooks/README.md) | One file for each use case (live, blocked, spec only) and the constants register |
| [`harness/`](harness/) | The official platform runner (`python -m harness.runner`): smoke checks and our agent's tasks in `results.json` |
| [`agentkit/viewer/`](agentkit/viewer/) | Read-only run viewer: `python -m agentkit.viewer` |
| [`agentkit/service/`](agentkit/service/) | The agent as a service: `python -m agentkit.service` (HTTP, dry run only) |
| [`core/`](core/) | Legacy import paths (`core.harness`, `core.llm_gateway`, `core.economics` give the `agentkit` names); legacy `dag_engine`, `eval_framework`, `judge` |
| [`scripts/`](scripts/) | CI scripts: fault matrix, retrieval evaluation, playbook check, viewer smoke check, service smoke check, `results.json` check, secret scan, STE check |
| [`tests/`](tests/) | Graded, **hand-written** test suite. See [`tests/README.md`](tests/README.md). |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How generic the agent is: layers, the pack seam, extension points, limits |
| [`docs/EXPLAINERS.md`](docs/EXPLAINERS.md) | Explainers for each module |
| [`docs/FIRST_PRINCIPLES.md`](docs/FIRST_PRINCIPLES.md) | The design from the problem down to the math: principles, algorithms, trade-offs |
| [`docs/SCALING_AND_DEPLOYMENT.md`](docs/SCALING_AND_DEPLOYMENT.md) | The plan for 10,000 users at the same time, and the cloud deployment |
| [`docs/COST_ACCURACY.md`](docs/COST_ACCURACY.md) | The measured cost and accuracy of each LLM route |
| [`docs/RESEARCH_GAP_ANALYSIS.md`](docs/RESEARCH_GAP_ANALYSIS.md) | Comparison with published arXiv practice; the gaps and their status |
| [`docs/CHANGES.md`](docs/CHANGES.md) | What changed, and why |

---

## 🚀 Getting Started

### 1. Prerequisites

- Python 3.11 or later (the code uses `tomllib` from the standard library). The team develops on 3.13.
- `pip install -r requirements.txt`

### 2. Environment (`.env` in the project root — never committed)

```env
AS_EMAIL=team21@theschoolofai.in
AS_SURYODAYA_PASSWORD=...
AS_KEYSTONE_PASSWORD=...
# LLM providers: set the ones that you have. config/routing.toml sets the sequence in each tier.
OPENAI_API_KEY2=...          # OPENAI_MODEL2 / OPENAI_FAST_MODEL2 change the GPT-6 models
DEEPSEEK_API_KEY=...         # DEEPSEEK_API_URL changes the endpoint
MOONSHOT_API_KEY=...         # MOONSHOT_API_URL changes the endpoint
GEMINI_API_KEY_2=...         # GEMINI_MODEL / GEMINI_FAST_MODEL change the models
ANTHROPIC_API_KEY=...        # ANTHROPIC_MODEL / ANTHROPIC_FAST_MODEL change the models
GROQ_API_KEY=...
OPEN_ROUTER_API_KEY=...
LMSTUDIO_URL=http://127.0.0.1:1235/v1   # local LM Studio server, no key
```

TLS verification is always on. It uses the trust store of the operating system, so it also works behind a
proxy that inspects TLS. `AS_INSECURE_TLS=1` stops the verification, for the platform client only.

The official runner injects `OPENAI_API_KEY`. That key then goes first in each tier. A copy of it in `.env`
does not change the sequence.

### 3. Run

```bash
# One question, one run folder under runs/
python capstone_agent.py --task packs/designreview/tasks/t01_battery_tray_rev_diff.toml

# The full task set, graded (live platform, LLM answers)
python capstone_evals.py                    # = python -m agentkit.harness.batch --grade
python capstone_evals.py --skip-llm         # template answers, no LLM cost

# Grade the newest batch again from its saved files (free)
python rescore.py

# Check the playbooks against the plans, routes, tasks and tool lists (--write rebuilds their index)
python scripts/playbooks_check.py

# The CI gate, offline: each task 3 times (pass^k), then show that a second grading is identical
python -m agentkit.harness.batch --sim packs/designreview/fixtures --skip-llm --batch-id ci --repeat 3 --grade
python -m agentkit.harness.grade --check runs/batches/ci

# Show that the verifiers find an agent that does not work correctly
python -m agentkit.harness.mutants
# ...end to end: change the result of one node in the saved graph, run the next nodes again, grade
python -m agentkit.harness.replay --calibrate runs/batches/ci --sim packs/designreview/fixtures
python -m agentkit.harness.replay runs/<run_id> --fault gate_reports_ready --sim packs/designreview/fixtures

# Offline: capture one time, then replay (with faults if you want)
python -m agentkit.sim.capture --tenant suryodaya
python -m agentkit.sim.capture --tenant keystone
python -m agentkit.harness.batch --sim packs/designreview/fixtures --skip-llm --grade \
       --fault "edit_text:Blank length grows 4.2 mm=>Blank length grows 5.5 mm"

# Is each configured LLM model still available? (reads the model lists, costs no tokens)
python -m agentkit.llm.models_check

# LIVE WRITES: t08 files its hand-offs as escalations to the configured assignee, verifies them, then withdraws them
python -m agentkit.harness.batch --only t08_battery_tray_handoff --write --grade
# ...or stop before each write until a person approves it
python -m agentkit.harness.run_one --task packs/designreview/tasks/t08_battery_tray_handoff.toml \
       --run-dir runs/t08-approval --run-id t08-approval --write --require-approval
python -m agentkit.harness.run_one --resume runs/t08-approval --approve file_handoffs   # or --reject
python -m agentkit.harness.cleanup --tenant suryodaya --dry-run   # is anything of ours still open?

# Look at batches and runs in a browser (read-only, this computer only)
python -m agentkit.viewer                   # http://127.0.0.1:8765/   [--runs-dir runs] [--port 8765]

# Send the spans of a run or a batch to an OpenTelemetry collector (OTLP/HTTP, no SDK)
python -m agentkit.telemetry.otlp runs/batches/<batch_id> --endpoint http://127.0.0.1:4318

# Serve the agent over HTTP: one question in, one audited answer out (dry run, this computer only)
python -m agentkit.service [--sim packs/designreview/fixtures --skip-llm]   # http://127.0.0.1:8780/
curl -s localhost:8780/v1/ask -H "Content-Type: application/json" -d '{"question": "Is the propeller ready for release?"}'

# The agent as an MCP tool for other agents (one tool for each live playbook, plus "ask")
claude mcp add designreview -- python -m agentkit.service --mcp-stdio     # or POST /mcp on the HTTP service

# Let another system ask: a signed webhook (config/hooks.toml; secret AGENTKIT_HOOK_<NAME>_SECRET)
python -m agentkit.service.hooks send http://127.0.0.1:8780 new_version '{"part": "BEV-BT-2400"}'
```

A cron line or an n8n workflow (`docs/examples/n8n_daily_release_check.json`) can also ask on a schedule.
See [`docs/explainers/agentkit_service.md`](docs/explainers/agentkit_service.md).

Each run folder also has `llm_calls.jsonl`: the exact system prompt, prompt and reply of each LLM attempt,
with no key in it. Set `OTEL_EXPORTER_OTLP_ENDPOINT`, and each run sends its spans to that collector.

The **run viewer** shows each batch: task and tenant, failed checks, calibration. It shows each run in tabs:

- the answer, with its claim audit;
- the finding;
- each check;
- the evidence graph as a timeline;
- the run tree (Timeline);
- the tool journal;
- the ground truth before and after the run (what changed during the run);
- the LLM cost, with the provider and the model of each call, and the exact prompt and reply of each attempt.

The viewer only reads the run folders. It cannot start runs or change anything.

### 4. Tests

```bash
python -m pytest                            # graded hand-written suite (tests/)
python -m pytest -m "live and not writes"   # live read-only platform tests
```

`tests_local/` is an AI-written reference suite for development. Git ignores it.
Run it with `python -m pytest tests_local -m "not live and not agent and not gateway_live"`.

---

## 🚀 Official Platform Harness Run (Release 8.1)

The platform evaluates the repository of each agent. It gets the saved branch from GitHub and runs an
isolated, automated benchmark against new enterprise instances. Start it with **"Our harness" → "Submit for
a run"**.

### Runner execution sequence

1. **Repository checkout.** The runner clones the exact commit on the saved branch from GitHub.
2. **Installation.** The runner executes `install` (`pip install -r requirements.txt`). Access to PyPI and
   GitHub is available during this step.
3. **Execution.** The runner executes `run` (`python -m harness.runner`) one time for each listed instance,
   on a new copy of that instance. The platform discards the writes. **No internet access** is available
   during this step. All communication must go through the given `AGENTSWITCH_BASE_URL` and
   `OPENAI_BASE_URL`.
4. **Scoring.** The runner reads `results.json` and reports the pass or fail status of each task and the
   overall score. Each team can run it one time every 3 days. The default timeout is 20 minutes.

### Configuration: `agentswitch-harness.toml`

This file is at the root of the repository:

```toml
install = "pip install -r requirements.txt"
run = "python -m harness.runner"
results = "results.json"
instances = ["suryodaya", "keystone"]
timeout_minutes = 20
```

### Server and local environments

The runner works in two modes:

| Mode | Source of the credentials | Environment variables |
|---|---|---|
| **Official server run** | The platform runner injects them. | `AGENTSWITCH_BASE_URL`, `AGENTSWITCH_TOKEN`, `AGENTSWITCH_INSTANCE`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| **Local development** | `.env`, or the `as_client` fallback | `.env` credentials. Run `python -m harness.runner` (default: Suryodaya) or `python -m harness.runner --instance keystone`. |

### Evaluated checks (`harness/runner.py`)

Each run on an instance has two parts. Both write entries to `results.json`.

**Part 1: platform smoke checks (9 entries).** These checks show that the platform and the seat work, and
that the seat still has what our agent needs:

1. **`mcp_tools_list`**: the MCP JSON-RPC 2.0 handshake works, and the seat tool catalogue is available.
   - **`required_tools_present`**: the seat has each tool that our pack needs, and each tool accepts the
     arguments that we send.
   - **`required_tools_compatible`**: each argument that we send has the same name and type as in our captured
     data. No tool has a new argument that it must get. Other schema changes (for example new optional filters)
     go into the evidence. They do not make the check fail.
2. **`mcp_tool_call`**: a direct MCP call to `TeamHarness.get_mine` gives the saved repository URL of the team.
3. **`agent_answers_with_tool`**: in the LLM tool-calling loop of the platform, the model calls
   `TeamHarness.get_mine` and answers with the repository link.
4. **`design_file_retrieval`**: a direct read of the target design file (`DesignFile.get`) and its revision
   history (`DesignVersion.list`).
5. **`release_readiness_gate`**: a direct read of `endpoint.designreview.release_readiness`.
6. **`refusal_unknown_entity`**: the platform refuses a design file UUID that does not exist
   (`00000000-0000-0000-0000-000000000000`). An exception is a failure, not a refusal.
7. **`agent_design_review_verdict`**: the LLM of the platform uses one tool and gives a release verdict. The
   verdict must agree with the gate of check 5. Our readiness parser reads the verdict and knows negation.
   Before 2026-10-16, the check looked for keywords. Thus "nothing is blocking, it is ready" passed, because
   it has the word "block". An answer that says both "ready" and "not ready" now fails.

**Part 2: our agent (`harness/agent_tasks.py`).** The runner runs the design-review tasks of the instance
through `agentkit`. Suryodaya has 8 tasks and Keystone has 4. Each task is a dry run with the LLM of the
platform. Our verifiers then grade each run against the data of the platform. The runner writes:

- one entry for each task: `<instance>:task:<task id>`. The score is the partial credit. The evidence gives the
  failed checks, the source of the answer and the model;
- one entry for each check that applied: `<instance>:check:<task id>:<check>:<name>`. A failed check gives
  the expected value and the observed value, for example `agent None, platform False`.

A check that ends as `fail`, `error` or `drift` is `passed: false`. A task that crashed is `passed: false`. An
exception becomes one failed entry with the exception and its file and line. A skipped check gets no entry.
The runner exits 1 when an entry failed.

The check entries include the **golden values** of the tasks. These are 39 exact answers that a person
checked in the seed data. An example for t01 is "rev B is v2 and rev C is v3". See
[`docs/explainers/agentkit_harness.md`](docs/explainers/agentkit_harness.md) for the format and for how to add a golden.

On 2026-10-16, a live run gave 147 of 147 entries on Suryodaya (266 seconds) and 93 of 93 on Keystone (150
seconds). The limit of the server is 200 entries and 20 minutes.

**Login of a local run.** The runner uses a token only when the process got it from outside, as on the
server. A token that is only a copy in `.env` expires. Thus a local run logs in with the password of the
tenant.

Check the runner before you submit it:

```bash
python -m harness.runner --instance suryodaya --sim packs/designreview/fixtures --out runs/r.json   # offline
python -m harness.runner --instance suryodaya --sim packs/designreview/fixtures \
       --fault "fail_always:endpoint.designreview.release_readiness:flaky" --out runs/f.json        # must fail
python scripts/check_results_json.py runs/r.json --instance suryodaya --min-agent-tasks 8 --expect all-pass
python -m harness.runner --instance keystone --out runs/k.json                                      # live
```

CI runs the offline commands on each push.

### Output specification: `results.json`

```json
{
  "tasks": [
    {
      "id": "suryodaya:mcp_tools_list",
      "title": "MCP tools/list works with the seat token",
      "passed": true,
      "score": 1.0,
      "evidence": "323 tools available"
    }
  ],
  "summary": "suryodaya: 7/7 checks passed"
}
```

- `passed`: a strict boolean (`true` or `false`).
- `score`: a float from 0.0 to 1.0.
- `tasks`: 1 to 200 task entries.

---

## ⚙️ Workflow State Machines

The `designreview` seat manages 6 primary flow entities. Each has a strict state machine:

1. **`DesignFile`**: `uploaded` ➔ `processing` ➔ `ready` ➔ `approved` *(or `error`)*
2. **`DesignReview`**: `draft` ➔ `in_review` ➔ `approved` *(or `rejected` / `reopened`)*
3. **`DesignFeedback`**: `open` ➔ `in_progress` ➔ `resolved` *(or `dismissed` / `reopened`)*
4. **`DesignProject`**: `planning` ➔ `active` ➔ `on_hold` ➔ `completed` *(or `archived`)*
5. **`DesignSupplierRequest`**: `draft` ➔ `sent` ➔ `received` ➔ `accepted` *(or `rejected`)*
6. **`DesignShare`**: `active` ➔ `revoked` / `expired`

*(See [`ENTITY_SCHEMAS.md`](ENTITY_SCHEMAS.md) for the full state transition diagrams and the permission
matrix.)*

---

## 🐞 Platform bugs found

We found these bugs while we built and tested the agent (see [`GAP_REPORT.md`](GAP_REPORT.md) and
`platform_bugs.json`):

- The feedback panel crashes on `tags` that are not strings (`e.tags.split is not a function`). We filed it.
- `/api/mcp` responses declare `Transfer-Encoding: chunked` but send a body that is not chunked. This breaks
  standard HTTP clients. `agentkit/transport/wire.py` has a workaround.
- Two candidates are strict `xfail` live tests:
  - `release_impact` lists items through the design BOMs of other files (C-1);
  - `released_version_id` points at a version of another file (C-2).

---

## 🛡️ Security & Secret Hygiene

Two independent scanners examine the full Git history on each push and pull request
(`.github/workflows/ci.yaml`):

```bash
python scripts/secret_scan.py --history     # stdlib scanner: AgentSwitch JWTs, LLM provider keys, literal passwords, committed .env
python scripts/secret_scan.py               # the same rules over every tracked file
gitleaks git . --config .gitleaks.toml --redact --verbose   # gitleaks 8.30.1 (SHA-256-pinned in CI), default rules + ours
```

- **Status (2026-10-15):** both are clean across all commits on each branch.
- **Findings never show the secret.** They show only the rule, the file, the line, the first four characters
  of the value and its length.
- **Exceptions** go only in `.gitleaks.toml`, with a reason. CI ignores inline `gitleaks:allow`. A
  `.gitleaksignore` file makes the job fail.
- **Policy.** Git ignores environment files (`.env`, `secrets.local.txt`) and private test files
  (`tests_local/`, `proofs/`).

## ✅ Continuous integration

`.github/workflows/ci.yaml` runs on pushes and pull requests to `main`. It is fully offline: no secrets, no
`.env`, `AGENTKIT_OFFLINE=1`. It runs on Ubuntu and Windows, with Python 3.11 and 3.13:

1. the hand-written suite: `pytest tests -m "not live and not agent and not gateway_live and not writes"`;
2. each task 3 times on the captured fixtures with template answers, then the gate
   `**12 of 12 tasks pass on every run.**`;
3. `grade --check`: a second grading of the saved runs must give identical scores;
4. `agentkit.harness.mutants`: each planted fault must cause a failure. Then
   `agentkit.harness.replay --calibrate`: each node-level replay fault must fail its check end to end, and
   each control replay must still pass;
5. a fault-injection matrix, `scripts/ci_fault_matrix.py`. It replays tasks with 13 platform faults. Examples
   are a record that changes during the run, a flaky read, a missing tool and an instruction in platform
   data. The agent must never crash. Each run must get exactly the
   grade in `packs/designreview/ci_faults.toml`, down to the check;
6. retrieval quality, `scripts/retrieval_eval.py --check`: recall@5, precision@5 and MRR on the labelled
   queries must stay at or above the floors;
7. the playbook check, `scripts/playbooks_check.py`. Each playbook status must agree with the plans, the
   routes, the tasks and the captured tool lists. The index must be current, and `constants.toml` must agree
   with the code;
8. a viewer smoke check, `scripts/ci_viewer_smoke.py`. It starts the run viewer on the CI batch and opens each
   run, the report, the calibration and each telemetry tree. The viewer must stay read-only and confined;
9. an OTLP export smoke check, `scripts/ci_otlp_smoke.py`. It sends the spans of the CI batch to a stub
   collector on 127.0.0.1, and every span of every run must arrive;
10. a service smoke check, `scripts/ci_service_smoke.py`. It starts the agent service on the fixtures and
    asks the question of each routed task over HTTP, also as a stream. Each answer must have the expected
    route or refusal. Then our own MCP client lists the tools and calls one;
11. the official runner, offline, for each instance. `scripts/check_results_json.py` examines each
   `results.json`. Then a planted fault must give failed entries with evidence, and the runner must exit 1;
12. the docs style job, `scripts/ste_check.py`: this README, the architecture documents, the explainers and
    the playbooks must follow the measurable ASD-STE100 rules;
13. the two secret scans above. The workflow pins each action to its commit SHA.

---

## 👥 Contributors & Team

- **Sunil Jakkaraju** ([@jssunil](https://github.com/jssunil)) — Team Lead / Core Agent Architecture, AgentKit transport & graph execution, Release 8.1 harness integration.
- **Bharath KR** ([@bharathkrd](https://github.com/bharathkrd)) — Contributor ([PR #4](https://github.com/jssunil/designreview/pull/4)), hand-written edge-case test suite for revision letter parsing in [`tests/test_finding_and_claims.py`](tests/test_finding_and_claims.py).
