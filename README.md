# AgentSwitch — Design Review Seat (`designreview`)

> **EAGv3 Capstone Project** | **Team 21**  
> **Assigned Seat:** Design Review (`designreview`) — Design Files, Versions, Reviews  
> **Core Request:** *"What changed between rev B and rev C, and are there manufacturability problems in this part?"*

---

## 📌 Project Overview

**AgentSwitch** is a multi-agent, enterprise-grade business operations platform spanning 424 entity types deployed across two simulated manufacturing enterprises:
1. **Suryodaya Precision Works** (India — GST / Ind AS)
2. **Keystone Precision Works LLC** (US — Sales & Use Tax / ASC)

Each team operates within an assigned **seat** with specific entity permissions, API scopes, and MCP interfaces. Our seat, **Team 21 (`designreview`)**, is tasked with building an autonomous, MCP-driven agent that can perform multi-step geometric/BOM revision comparisons, inspect design revisions, validate engineering checklists, and assess manufacturability (DFM/DFA) issues.

---

## 🎯 The Core Agent Goal

Our agent must handle the following high-bar engineering evaluation autonomously over Model Context Protocol (MCP) and REST APIs:

```text
"What changed between rev B and rev C, and are there manufacturability problems in this part?"
```

What the agent actually does to answer it:
- **Revision delta from the engineers' own notes.** Rev letters ("Rev B", "Rev 2") are read from each
  `DesignVersion.commit_message`, so rev B is mapped to the right version even when it isn't v2. This
  platform build has no CAD kernel (geometry diffs, wall-thickness and interference checks return `501`),
  so the answer says plainly that no geometry comparison was run.
- **Manufacturability from platform evidence.** The release-readiness gate (ready / reasons / critical
  blockers), open high/critical `DesignFeedback`, failed DFM checklist results, and the company's
  `DesignStandard` records relevant to the change.
- **Refusal when the data or the seat can't support an answer** — an unknown file, or a request that needs
  a tool this seat isn't given (e.g. the platform's AI review).

---

## 🏗️ Architecture

Three layers; a lower layer never imports a higher one.

```
agentkit/            domain-free framework (no design-review names; a lint test enforces it)
  transport/         MCP client (typed errors, re-login, read-only retries), REST reader, tool-call journal
  llm/               multi-provider LLM gateway (classified retries, real token usage, cost ledger)
  graph/             live task graph: verdicts, allowlisted actions, rule planner, checkpoints
  answer/            claim audit + narrator (LLM words a finding; audited; template fallback)
  retrieval/         topic chunker + BM25 evidence index
  record/            crash-safe run record (TaskRun) and atomic writes
  harness/           tasks, ground-truth capture, checks, batch runner, grader, verifier self-test
  sim/               platform fixtures + offline SimPlatform with injectable faults
packs/designreview/  the domain pack: actions, finding, claim rules, template, probes, verifiers,
                     mutants, prompts/*.md, plans/*.toml, tasks/*.toml, fixtures/*.json
config/              tenants.toml, routing.toml, project.toml (default pack / tenant)
```

**How one question is answered**

1. **Plan** (`packs/designreview/plans/rev_diff_dfm.toml`) — the design file is read first; revisions,
   release gate, feedback and standards follow in parallel; a declarative rule adds a read of failed DFM
   checklists only when the gate reports blockers. Nodes can only name registered actions (an allowlist);
   the pack has no write actions, and every run is a dry run.
2. **Evidence** — every MCP call goes through a journal (`tool_journal.jsonl`); the graph is checkpointed
   after every step (`graph.json`).
3. **Finding** (`packs/designreview/finding.py`) — the gradable answer is computed in code: rev → version
   mapping, change notes and their quantities, release verdict, blockers, DFM signals, refusals.
4. **Answer** — the LLM only words the finding. The wording is audited against it (no invented quantities,
   record ids or standards; no "ready" when the gate says not ready; no geometry claims; required facts
   present), rewritten once if wrong, and replaced by a deterministic template if it still fails or the
   LLM is down.

**How a run is graded (the harness)**

Each task is a TOML file (`packs/designreview/tasks/`). For every task the harness:
reads the platform facts its verifiers need **with its own login** (before and after the run) →
runs the agent **in a subprocess** → saves everything to `runs/<run_id>/`
(`task.json`, `taskrun.json` written first as `ended="running"`, `tool_journal.jsonl`, `graph.json`,
`ground_truth_before/after.json`, `llm_ledger.json`) → **grades from those files only**, so a verifier fix
is a free re-grade. Check results are `pass / fail / drift / skip / error`; `drift` means the data moved
under the run, and `error` never counts as a pass. The verifiers re-derive rev letters, quantities and
readiness wording with their own parsers, so an agent bug can't pass its own check.

| Task | What it proves |
|---|---|
| `t01_battery_tray_rev_diff` | B→v2, C→v3; 2.0→3.0 mm bend radius, +4.2 mm blank; NOT ready with a critical blocker |
| `t02_unknown_file_refusal` | refuses after one lookup; reads nothing else, invents nothing |
| `t03_ai_review_refusal` | checks the seat catalogue, never calls the absent tool, declines |
| `t04_bench_vice_no_geometry` | numeric revs; reports the 180 g saving; claims no geometry comparison |
| `t05_propeller_rev_mapping` | rev B is v1 — mapped by commit note, not by position |
| `t06_keystone_boilerplate_notes` | **Keystone.** Rev B/C notes only say "drawing and model updated together": reports exactly that, invents no changes |
| `t07_keystone_latest_dfm_fixes` | **Keystone.** Notes carry no rev labels: compares the latest two versions ("DFM fixes: increased draft angles, added rib reinforcement") and recalls the company's DFM guidelines |
| `t08_battery_tray_handoff` | NOT ready; proposes one hand-off each to **manufacturing** (cracking bend radius, weld access) and **quality** (PPAP level 3), cites exactly those items, shows the escalation each would file and files nothing; names the past-due "DFM review closed" milestone |
| `t09_keystone_no_handoff_late_schedule` | **Keystone.** Every open issue is design review's own, so it proposes **no** hand-off (inventing one fails); names three past-due critical-path milestones and never says "on track" |

**Verifier self-test.** `agentkit.harness.mutants` takes passing runs, breaks one thing in a copy
(a wrong rev mapping, an invented quantity, a false "ready" claim, a sneaky write, a crashed run …) and
requires the matching check to fail. Any mutant that slips through, or any check nothing can make fail,
fails calibration.

**Offline.** `python -m agentkit.sim.capture --tenant <tenant>` records every exchange that tenant's tasks
need into `packs/designreview/fixtures/<tenant>.json`; `--sim packs/designreview/fixtures` replays each
task against its own tenant's fixture (all nine tasks in about a second),
with faults such as `drop_tool:…`, `fail_once:…:flaky`, or `edit_text:old=>new` (another team edits a
record during the run → graded `drift`).

**Constraints.** No agent/workflow frameworks (no LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen,
n8n …): the loop, graph, planner, retrieval and harness are plain Python. Dependencies: `httpx`,
`pydantic` (validates config/plan/task/fixture files), `python-dotenv`, `pytest`; config is TOML read by
the standard library's `tomllib`.

---

## 📂 Repository Map

| Path | Purpose |
| :--- | :--- |
| [`PLAN.md`](PLAN.md), [`GAP_REPORT.md`](GAP_REPORT.md), [`research.md`](research.md) | Seat plan, competitive gap report, research notes |
| [`ENTITY_SCHEMAS.md`](ENTITY_SCHEMAS.md), [`agentswitch.md`](agentswitch.md) | `designreview` entities / state machines; platform guide |
| [`capstone_agent.py`](capstone_agent.py) | Run the agent: `--task <file.toml>` (or the default T01 question) |
| [`capstone_evals.py`](capstone_evals.py) | Run the whole task set and grade it |
| [`rescore.py`](rescore.py) | Re-grade saved runs (files only; no network, no LLM) |
| [`as_client.py`](as_client.py) | Compatibility client (`AgentSwitchClient`); `.seat()` returns the new MCP client |
| [`agentkit/`](agentkit/) · [`packs/designreview/`](packs/designreview/) · [`config/`](config/) | Framework · domain pack · configuration |
| [`agentkit/viewer/`](agentkit/viewer/) | Read-only run viewer: `python -m agentkit.viewer` |
| [`core/`](core/) | Legacy import paths (`core.harness`, `core.llm_gateway`, `core.economics` re-export `agentkit`); legacy `dag_engine`, `eval_framework`, `judge` |
| [`tests/`](tests/) | Graded, **hand-written** test suite — see [`tests/README.md`](tests/README.md) |
| [`docs/EXPLAINERS.md`](docs/EXPLAINERS.md) | Per-module explainers |
| [`docs/CHANGES.md`](docs/CHANGES.md) | What changed, and why, after the refactor |

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.11+ (uses the standard library's `tomllib`); developed on 3.13
- `pip install -r requirements.txt`

### 2. Environment (`.env` in the project root — never committed)

```env
AS_EMAIL=team21@theschoolofai.in
AS_SURYODAYA_PASSWORD=...
AS_KEYSTONE_PASSWORD=...
# LLM providers: set any you have; config/routing.toml decides the order per tier
ANTHROPIC_API_KEY=...        # ANTHROPIC_MODEL=... overrides the routing default
GEMINI_API_KEY=...
GROQ_API_KEY=...
OPEN_ROUTER_API_KEY=...
```

TLS is always verified (against the OS trust store, so it also works behind a TLS-inspecting proxy);
`AS_INSECURE_TLS=1` is an explicit opt-out for the platform client only.

### 3. Run

```bash
# One question, one run folder under runs/
python capstone_agent.py --task packs/designreview/tasks/t01_battery_tray_rev_diff.toml

# The whole task set, graded (live platform, LLM answers)
python capstone_evals.py                    # = python -m agentkit.harness.batch --grade
python capstone_evals.py --skip-llm         # template answers, no LLM cost

# Re-grade the newest batch from its saved files (free)
python rescore.py

# Prove the verifiers catch a misbehaving agent
python -m agentkit.harness.mutants

# Offline: capture once, then replay (optionally with faults)
python -m agentkit.sim.capture --tenant suryodaya
python -m agentkit.sim.capture --tenant keystone
python -m agentkit.harness.batch --sim packs/designreview/fixtures --skip-llm --grade \
       --fault "edit_text:Blank length grows 4.2 mm=>Blank length grows 5.5 mm"

# Browse batches and runs in a browser (read-only, this machine only)
python -m agentkit.viewer                   # http://127.0.0.1:8765/   [--runs-dir runs] [--port 8765]
```

The **run viewer** shows each batch (task × tenant, failing checks, calibration) and each run in tabs:
the answer with its claim audit, the finding, every check, the evidence graph as a timeline, the tool
journal, ground truth before/after (what changed during the run), and LLM cost. It only reads the run
folders; it can't start runs or change anything.

### 4. Tests

```bash
python -m pytest                            # graded hand-written suite (tests/)
python -m pytest -m "live and not writes"   # live read-only platform tests
```
`tests_local/` (git-ignored) is an AI-written reference suite used during development:
`python -m pytest tests_local -m "not live and not agent and not gateway_live"`.

---

## ⚙️ Workflow State Machines

The `designreview` seat manages 6 primary flow entities with strict state machines:

1. **`DesignFile`**: `uploaded` ➔ `processing` ➔ `ready` ➔ `approved` *(or `error`)*
2. **`DesignReview`**: `draft` ➔ `in_review` ➔ `approved` *(or `rejected` / `reopened`)*
3. **`DesignFeedback`**: `open` ➔ `in_progress` ➔ `resolved` *(or `dismissed` / `reopened`)*
4. **`DesignProject`**: `planning` ➔ `active` ➔ `on_hold` ➔ `completed` *(or `archived`)*
5. **`DesignSupplierRequest`**: `draft` ➔ `sent` ➔ `received` ➔ `accepted` *(or `rejected`)*
6. **`DesignShare`**: `active` ➔ `revoked` / `expired`

*(See [`ENTITY_SCHEMAS.md`](ENTITY_SCHEMAS.md) for full state transition diagrams and permission matrix)*

---

## 🐞 Platform bugs found

Found while building and testing (see [`GAP_REPORT.md`](GAP_REPORT.md) and `platform_bugs.json`):
- Feedback panel crashes on non-string `tags` (`e.tags.split is not a function`) — filed.
- `/api/mcp` responses declare `Transfer-Encoding: chunked` but send an unchunked body, breaking standard
  HTTP clients (worked around in `agentkit/transport/wire.py`).
- Candidates tracked as strict `xfail` live tests: `release_impact` lists items via other files' design
  BOMs (C-1); `released_version_id` points at another file's version (C-2).
