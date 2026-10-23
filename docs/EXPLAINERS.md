# Explainer index — Team 21 Design Review agent

*Written in ASD-STE100 (Simplified Technical English) style, 2026-10-14.*

This page is the start point for the explainers of each module. The agent works on the live AgentSwitch
platform. It answers this question:

*"What changed between rev B and rev C, and are there manufacturability problems in this part?"*

Each explainer has these parts:

- an overview;
- the design decisions;
- what the module does;
- its key types;
- how to test it. The hand-written test template in `tests/` has the detailed checklist.

Other documents:

- [`FIRST_PRINCIPLES.md`](FIRST_PRINCIPLES.md) starts from the problem and goes down to the math: the
  principles, the algorithms, the trade-offs, the evaluation, hallucination, the token budget and reuse.
- [`SCALING_AND_DEPLOYMENT.md`](SCALING_AND_DEPLOYMENT.md) gives the plan for 10,000 users at the same time and
  the cloud deployment.
- [`packs/designreview/playbooks/`](../packs/designreview/playbooks/README.md) has one file for each use case:
  2 live, 3 blocked and 10 spec only. It also has the register of the numbers that the rules use.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) tells how generic the architecture is: the pack seam, the extension
  points and the limits. Read it first.
- [`COST_ACCURACY.md`](COST_ACCURACY.md) compares the cost and the accuracy of each LLM route (gap G12).
- [`RESEARCH_GAP_ANALYSIS.md`](RESEARCH_GAP_ANALYSIS.md) compares the harness, the evals and the
  implementation with published arXiv practice. It also lists the gaps and the tests that remain.

## The full system

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
            │            mutants, replay ─► prove checks can fail │
            │  sim/      captured platform, offline, with faults  │
            │  telemetry/ run tree per run (never graded)          │
            │  viewer/   read-only browser page over run folders  │
            │  service/  HTTP API: question in, audited answer out │
            └─────────────────────────────────────────────────────┘
   packs/designreview/: actions · plans · router · finding · claims · template · prompts · probes · checks · mutants · tasks · fixtures
```

In short:

1. Code calculates the facts from the platform.
2. The LLM only writes these facts as text, and an audit examines that text.
3. The grading compares the result with the platform. The harness reads the platform separately, saves all
   data to disk, and shows that each check can fail.

## Reading order

All explainers use ASD-STE100 (Simplified Technical English).

0. [`config.md`](explainers/config.md): the TOML configuration, the pydantic models that validate it, and how `.env` loads.
1. [`agentkit_transport.md`](explainers/agentkit_transport.md): the MCP client, the REST reader, the tool-call journal, argument validation.
2. [`agentkit_llm.md`](explainers/agentkit_llm.md): the LLM gateway, the providers and tiers, the record of which model answered, the cost ledger.
3. [`agentkit_graph.md`](explainers/agentkit_graph.md): the live task graph, verdicts, rules, the registry, the retry policy, timeouts, channels, superstep mode, the approval pause, checkpoint history and fork.
4. [`agentkit_answer.md`](explainers/agentkit_answer.md): the claim audit, the narrator, the cascade, the fence and the quarantine for platform data.
5. [`agentkit_retrieval.md`](explainers/agentkit_retrieval.md): the topic chunker, the BM25 index, the parent-child index, retrieval quality.
6. [`agentkit_record.md`](explainers/agentkit_record.md): the run record that is safe from a crash.
7. [`agentkit_harness.md`](explainers/agentkit_harness.md): tasks, ground truth, grading, reliability statistics, calibration (mutants and end-to-end replay), approval and resume, routed tasks.
8. [`agentkit_sim.md`](explainers/agentkit_sim.md): fixtures, the offline platform, faults, the CI fault matrix.
9. [`agentkit_telemetry.md`](explainers/agentkit_telemetry.md): the run tree: spans, timings, tokens, retries.
10. [`agentkit_viewer.md`](explainers/agentkit_viewer.md): the read-only browser page for batches and runs.
11. [`agentkit_service.md`](explainers/agentkit_service.md): the agent as a service: one question in, one audited answer out, over HTTP, with progress events.
12. [`packs_designreview.md`](explainers/packs_designreview.md): the domain: the finding, the verifiers, the router, the hand-offs, the tasks, the playbooks and the constants register.
13. [`capstone_agent.md`](explainers/capstone_agent.md): the agent entry point.
14. [`capstone_evals.md`](explainers/capstone_evals.md): run the benchmark and grade it again.
15. [`harness_runner.md`](explainers/harness_runner.md): the official runner: the smoke checks, our agent's tasks and checks in `results.json`, the format check.
16. [`as_client.md`](explainers/as_client.md): the compatibility client.
17. [`core_legacy.md`](explainers/core_legacy.md): the old modules in `core/`, and why they stay.

## Decisions for the full system

- **No agent frameworks.** The loop, the graph, the planner, the retrieval and the harness are plain Python.
  The dependencies are `httpx`, `pydantic`, `python-dotenv` and `pytest`. Only the legacy engine needs
  `networkx`.
- **Layers.** `agentkit/` has no design-review entity, tool, id or tenant. The defaults are in
  `config/project.toml`. A second domain is a new `packs/<name>/`. It needs no change to `agentkit`.
- **A refusal is a result.** A missing record or an absent tool ends a node as `declined`, and the finding
  as `declined`. The answer says so. The grader gives a pass when the platform confirms the refusal.
- **Evidence before judgement.** The run record goes to disk as `running` before the work starts. The tool
  journal, the graph checkpoint and the ground truth are on disk before a check reads them. Checks never use
  the network.
- **Observable, not graded.** Each run writes `telemetry.json` (task, node, tool and llm spans). The timings
  go into `report.md` and the viewer, never into a score. Thus a second grading gives the same bytes
  (`grade --check`).
- **Platform text is data.** The narrator gets the finding inside an `<untrusted_platform_data>` fence. The
  prompt tells it never to obey instructions in the fence. A person can approve each live write before it
  occurs.
- **Honest about the platform.** This build has no CAD kernel. Thus the revision changes come from the commit
  messages, and the answer never states a geometry comparison.

## Tests

- `tests/` is the graded, hand-written suite. See [`tests/README.md`](../tests/README.md).
- `tests_local/` is an AI-written reference suite for development. Git ignores it.
