# Explainer index — Team 21 Design Review agent

This is the single entry point into a set of per-file explainer documents covering every code file in the `capstone_agent.py` / `capstone_evals.py` / `core/` / `rescore.py` / `as_client.py` stack — the agent that answers *"What changed between rev B and rev C, and are there manufacturability problems in this part?"* against the live AgentSwitch platform.

Each linked document covers, for its one file: the 10,000-ft view, why it's written the way it is (intent and trade-offs, not just mechanics), a walkthrough of what it does, every type/variable's purpose, a function-by-function reference, how to test it today, and a prioritized list of tests that should be added for stability. **None of the "tests that should be added" sections have been implemented yet** — this repository currently has zero automated tests anywhere. These documents are the map for writing that test suite, not the test suite itself.

## 10,000-ft view of the whole system

```
                 ┌─────────────────────────────────────────────┐
                 │              capstone_agent.py               │
                 │  Team21DesignReviewAgent  +  DesignReviewPlanner │
                 └───────┬───────────────────────────┬─────────┘
                         │                           │
              (evidence gathering)            (final synthesis)
                         │                           │
          ┌──────────────▼─────────────┐   ┌─────────▼──────────┐
          │   core/dag_engine.py        │   │  core/llm_gateway.py │
          │   LiveGraphExecutor +       │   │  routing.yaml +      │
          │   GraphStore + Planner      │   │  economics.py        │
          └──────────────┬─────────────┘   └─────────┬───────────┘
                         │                            │
                 (tool_executor callback)      (six provider APIs)
                         │
                 ┌───────▼────────┐
                 │  as_client.py   │──── the live AgentSwitch platform
                 │ AgentSwitchClient│      (MCP + REST)
                 └─────────────────┘

Every run's outcome is captured as a core/harness.py TaskRun and saved to
proofs/runs/*.json BEFORE anything scores it. Two ways to score a TaskRun:

   capstone_evals.py::Team21Evaluator     rescore.py
   (runs the agent fresh, scores it)      (loads a saved run, scores it again --
                                            zero LLM calls unless --with-judge)
              │                                        │
              └───────────────┬────────────────────────┘
                               ▼
                  core/eval_framework.py::evaluate_run
                  (ground-truth-separated axes, defined
                   in capstone_evals.py::AXES)
                               │
                     one axis delegates to
                               ▼
                       core/judge.py
                (single-pass LLM-as-judge, for the
                 one axis with no ground-truth record)
```

The whole point of this design, in one sentence: **the agent's evidence-gathering is a graph that can react to what it learns mid-run, and the grading of its final answer never trusts the answer's own prose — it always re-checks against the live platform.**

## Reading order

Read in this order — each file assumes you understand everything before it:

1. **[`as_client.md`](explainers/as_client.md)** — the platform transport. Everything else calls this, directly or indirectly. Start here.
2. **[`core_economics.md`](explainers/core_economics.md)** — the cost/token ledger. Small, no dependencies, good warm-up for reading `core/` files.
3. **[`core_llm_gateway.md`](explainers/core_llm_gateway.md)** — the multi-provider LLM transport. Depends on #2. The most complex single file in `core/`.
4. **[`core_dag_engine.md`](explainers/core_dag_engine.md)** — the event-sourced live graph. No project dependencies, but the most structurally important file — read it slowly.
5. **[`core_harness.md`](explainers/core_harness.md)** — the uniform run record (`TaskRun`/`Step`). Small, no project dependencies.
6. **[`core_judge.md`](explainers/core_judge.md)** — the single-pass LLM-as-judge. Depends on #3.
7. **[`core_eval_framework.md`](explainers/core_eval_framework.md)** — the ground-truth axis engine. Depends on #5; read alongside #6.
8. **[`capstone_agent.md`](explainers/capstone_agent.md)** — the actual Design Review agent. This is where #1, #3, #4, #5 all get wired together. Nothing before this point is domain-specific; everything from here on is.
9. **[`capstone_evals.md`](explainers/capstone_evals.md)** — the ground-truth benchmark suite for the agent in #8. Depends on #7 and #8.
10. **[`rescore.md`](explainers/rescore.md)** — free re-scoring of saved runs. Depends on everything before it; shortest file, makes sense last.

## Cross-cutting trade-offs (apply to more than one file)

- **No FastAPI service, no async runtime, no OpenTelemetry, no `z3-solver`, no multi-model judge panel.** Every one of these was surveyed in prior EAGv3 coursework (`glc_v5`, `S17Code`, `S13Code`) and deliberately not ported — each per-file explainer names exactly what was cut from its own source pattern and why. The common thread: this project is a single-process capstone agent script, not a hosted multi-tenant service, and infrastructure sized for the latter would be pure overhead here.
- **Ground-truth re-fetching happens on every axis, every call, with no shared caching layer between axes.** Cheap at this project's scale (one file, five axes, one benchmark task); would need restructuring if either number grew by an order of magnitude. Flagged individually in `core_eval_framework.md` and `capstone_evals.md`.
- **Two independent "save state to JSON" mechanisms exist** (`core/dag_engine.py`'s `GraphStore._checkpoint`, `core/harness.py`'s `TaskRun.save`) **with inconsistent collision-avoidance** (UUID-suffixed run ids vs. second-precision timestamps) and no shared schema-versioning convention. Flagged in both files' explainers; worth reconciling into one pattern.
- **Several places trust that a specific hardcoded string (a tool name, a field name, a phrase to match) continues to reflect live platform reality** — the commit-message text quoted in `capstone_evals.py`'s docstring, the `BATTERY_TRAY_FILE_ID` UUID, the MCP tool/field names in `capstone_agent.py`. None of these are enforced by a test today; a schema-contract test suite (see `as_client.md` and `capstone_agent.md`'s "tests to add" sections) is the recommended fix, not further hardcoding.
- **Real bugs were found and fixed by actually running the code against the live platform, not by static review**: the wrong generic `list`/`get` MCP tool-name assumption, the `status` vs. `overall_result` field-name mismatch, and the release-readiness axis's negation-substring false negative. All three are documented in detail in their respective files' explainers as concrete lessons, not just "here's a bug that got fixed."

## What's *not* covered here

These explainers cover the agent/framework code only. Not documented here (out of scope for this pass, not forgotten):
- `tasks/*.json` — task definitions as data, not code; see the files themselves and `PLAN.md` §7 for the requirement that graded task content be hand-authored.
- `ENTITY_SCHEMAS.md`, `GAP_REPORT.md`, `PLAN.md`, `SESSION_NOTES.md` — project/domain documentation, already self-explanatory in their own right.
- The plan document that drove this framework's construction: `C:\Users\ANT-PC\.claude\plans\look-at-d-sjk-eagv3-projects-whimsical-zephyr.md` — covers *why* each `core/` pattern was chosen over alternatives found in `D:\sjk\eagv3`; read it if you want the competitive-survey context behind the design decisions these explainers describe.

## The single biggest thing to do next

Every explainer's "tests that should be added" section is currently aspirational — **zero automated tests exist in this repository today.** If you read only one cross-cutting recommendation from all ten documents, make it this: start with `core_dag_engine.md`, `core_harness.md`, and `core_eval_framework.md`'s test lists — those three files are pure logic with no network/LLM dependency, are fully testable *today* with no refactoring, and cover the structural core (the graph scheduler, the run record, the scoring engine) that everything else in the system depends on being correct.
