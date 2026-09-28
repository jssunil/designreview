# Explainer index — Team 21 Design Review agent

One entry point into the per-module explainers for the agent that answers *"What changed between rev B and
rev C, and are there manufacturability problems in this part?"* on the live AgentSwitch platform.
Each explainer covers: the 10,000-ft view, why it's written the way it is, what it does, its key types, and
how to test it (the matching hand-written test template in `tests/` has the detailed checklist).

## The whole system

```
                      capstone_agent.py --task  /  capstone_evals.py (batch + grade)
                                      │
            ┌─────────────────────────┴──────────────────────────┐
            │                agentkit (domain-free)               │
            │                                                     │
   plan.toml ─► graph/  live task graph ──► transport/ ──► AgentSwitch MCP (journaled)
            │       │                                             │
            │       ▼ node results                                │
   pack ──► finding (code) ─► answer/  LLM words it ─► audit ─► template fallback
            │                     │                               │
            │                     └─► llm/  multi-provider gateway │
            │                                                     │
            │  harness/  ground truth (own login) before/after ─► run folder ─► checks ─► score / report
            │            mutants ─► prove every check can fail    │
            │  sim/      captured platform, offline, with faults  │
            └─────────────────────────────────────────────────────┘
   packs/designreview/: actions · plans · finding · claims · template · prompts · probes · checks · mutants · tasks · fixtures
```

In one sentence: **the facts are computed in code from the platform, the LLM only words them and is audited,
and grading compares the result with the platform — read separately, saved to disk, and proven able to fail.**

## Reading order

0. [`config.md`](explainers/config.md) — TOML config and the pydantic models that validate it
1. [`agentkit_transport.md`](explainers/agentkit_transport.md) — MCP client, REST reader, tool-call journal
2. [`agentkit_llm.md`](explainers/agentkit_llm.md) — LLM gateway and cost ledger
3. [`agentkit_graph.md`](explainers/agentkit_graph.md) — live task graph, verdicts, rules, registry
4. [`agentkit_answer.md`](explainers/agentkit_answer.md) — claim audit and narrator
5. [`agentkit_retrieval.md`](explainers/agentkit_retrieval.md) — topic chunker and BM25 index
6. [`agentkit_record.md`](explainers/agentkit_record.md) — crash-safe run record
7. [`agentkit_harness.md`](explainers/agentkit_harness.md) — tasks, ground truth, grading, calibration
8. [`agentkit_sim.md`](explainers/agentkit_sim.md) — fixtures, offline platform, faults
9. [`packs_designreview.md`](explainers/packs_designreview.md) — the domain: finding, verifiers, tasks
10. [`capstone_agent.md`](explainers/capstone_agent.md) — the agent entry point
11. [`capstone_evals.md`](explainers/capstone_evals.md) — run and re-grade the benchmark
12. [`as_client.md`](explainers/as_client.md) — the compatibility client
13. [`core_legacy.md`](explainers/core_legacy.md) — what's left in `core/` and why

## Cross-cutting decisions

- **No agent frameworks.** The loop, graph, planner, retrieval and harness are plain Python; dependencies are
  `httpx`, `pydantic`, `python-dotenv`, `pytest` (+ `networkx` for the legacy engine only).
- **Layering.** `agentkit/` names no design-review entity, tool, id or tenant; defaults live in
  `config/project.toml`. A second domain is a new `packs/<name>/`, not an `agentkit` change.
- **Refusal is a result.** A missing record or an absent tool ends a node `declined`, the finding
  `declined`, and the answer says so — graded as a pass when the platform confirms it.
- **Evidence before judgement.** The run record is written as `running` before work starts; the tool journal,
  graph checkpoint and ground truth are on disk before any check reads them; checks never touch the network.
- **Honest about the platform.** This build has no CAD kernel, so revision changes come from commit messages
  and the answer never claims a geometry comparison.

## Tests

`tests/` is the graded, hand-written suite (see [`tests/README.md`](../tests/README.md)).
`tests_local/` (git-ignored) is an AI-written reference suite used during development.
