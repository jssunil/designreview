# Upgrade `core/` to a best-of-breed generic agent framework (gateway + DAG + harness + evals)

## Context

`designreview` already has a "first draft" generic framework, committed in `9cb012c
first draft of agentic harness`: `core/llm_gateway.py`, `core/dag_engine.py`,
`core/eval_framework.py`, plus `capstone_agent.py` / `capstone_evals.py` built on top.
It works, but each piece is the simplest possible version of its idea:

- **Gateway** — fixed try-Gemini-then-Groq-then-… fallback chain, no retries within a
  provider, no rate-limit awareness, no cost tracking, no caching.
- **DAG** — a static list of 5 independent tasks executed once in topological order
  (`nx.topological_sort` + a for-loop). No replanning, no concurrency, no persistence,
  and a failed node's error is silently stored as a result while children still run.
- **Evals** — `capstone_evals.py` scores the agent by regex/keyword-matching the
  **agent's own prose output**. `PLAN.md` §7 (the actual grading bar) explicitly
  requires "verifiers that read the database directly... not ones that trust the
  agent's own prose output" — the current evaluator doesn't meet that bar yet.
- There are also stale duplicate drafts at the repo root (`agent_runner.py`,
  `evals_harness.py`, root `llm_gateway.py`) that pre-date the `core/` refactor and
  are now dead weight.

The user asked to survey their own prior EAGv3 coursework under `D:\sjk\eagv3` for
better-architected versions of these same four things (gateway, DAG/loop, harness,
evals) and fold the best ideas from each into this project as the generic framework.
Four parallel Explore passes over `D:\sjk\eagv3` found:

| Concern | Best source found | Why |
|---|---|---|
| **LLM gateway** | `glc_v5\glc\routing\core.py` + `routing\policy.py` + `economics\{pricing,meter,budget}.py` + `cache\semantic.py` | Terminal point of one evolving lineage (`llmgateway7`→`llm_gatewayV9`→`glc_v4`→`glc_v5`). Adds per-provider RPM/RPD/TPM rate limiting + cooldown/backoff, YAML-declarative tier routing with cascade escalation, real cost metering, semantic caching, fail-soft config loading. Confirmed via file-tree diff that v5 is a strict superset of v4 — nothing better was dropped along the way. |
| **DAG / agent loop** | `S17Code\s17code\core\live_graph\{core,store}.py` | Event-sourced `TaskSpec`/`GraphPatch`/`NodeState`, a `GraphStore.ready()` topological "ready set" (not a single static pass), and an async worker-pool `LiveGraphExecutor` that interleaves planning with execution (goal → plan next frontier → run concurrently → observe → replan), instead of running a fixed task list once. `S13Code`'s version adds a Z3-proved admission oracle (`invariants.py`) and hedged/speculative branch racing (`speculation.py`) that are more rigorous but pull in a `z3-solver` dependency and formal-verification complexity — noted below as an explicit, deliberate scope cut. |
| **Harness** | `S18Code\harnesses\{base,loop}.py` + `rescore.py` | A `Harness` protocol + shared `Step`/`TaskRun` dataclasses so every run — whatever it did — produces one uniform on-disk record *before* it is scored, and a `rescore.py` that recomputes scores from those saved raw runs with zero model calls (so a scoring-code bug is a free fix, not a re-run). |
| **Evals** | `S18Code\evals\axes.py` (deterministic, ground-truth-separate-from-claim) + `S17Code\s17code\evals\judge.py` (LLM-as-judge rubric, simplified) | S18's axes never trust the agent's own claim — they recompute the true answer independently and compare. S17's judge pattern is the right tool for the handful of axes that are inherently qualitative (e.g. "is this DFM reasoning specific, not generic boilerplate"). |

Everything else surveyed (channel adapters/voice in `glc_v5`, S17's skill-plugin
manager, S13's Z3 invariants/speculation, full OpenTelemetry/FastAPI service
surface) is called out explicitly as **out of scope** below, with the reason, so the
scope cut is visible rather than silently dropped.

## Recommended approach

Keep the existing `core/` package and file names (minimize churn to what the user
already built); rewrite each module's internals using the patterns above, add two
new small modules for the harness/judge split, and delete the stale root-level
duplicates.

### 1. `core/llm_gateway.py` — add routing, resilience, cost, cache (from `glc_v5`)

Reuse the *shape* of `glc_v5\glc\routing\core.py` (`Router`/`RateState`) and
`glc_v5\glc\routing\policy.py` (YAML-declarative tier ordering + cascade), adapted
to this project's existing sync/httpx style (no FastAPI service — this stays an
importable library the agent calls directly, not a hosted microservice; running a
sidecar service is scope the capstone doesn't need):

- New `core/routing.yaml`: declares provider order/tiers (e.g. `fast` tier =
  Groq/Cerebras, `quality` tier = Gemini/Anthropic) and per-provider `rpm`/cooldown,
  replacing the hardcoded try-in-fixed-order chain.
- `RateState` per provider (from `glc_v5\glc\routing\core.py`): tracks recent call
  timestamps, applies cooldown/backoff after a failure, and is consulted before a
  provider is chosen — so a rate-limited provider is skipped instead of retried
  into a 429.
- One retry-with-backoff attempt per provider before falling through (currently
  zero retries — one transient error permanently skips a whole provider).
- A minimal cost/token ledger (`core/economics.py`, trimmed from `glc_v5`'s
  `economics/{pricing,meter}.py`): a small per-model price table + an in-memory
  running total logged at the end of a run — no budget-admission-control 402s,
  since a capstone script doesn't need pre-call budget enforcement, just visibility.
- A simple exact-prompt-hash response cache (trimmed from `cache/semantic.py` —
  skip the embedding-based semantic matching, which needs an embeddings endpoint
  and adds a dependency disproportionate to the benefit here); cuts cost/latency
  when the harness re-runs the same evidence-gathering calls during eval iteration.
- Keep all seven existing `_call_<provider>` adapters as-is — they work.

### 2. `core/dag_engine.py` — event-sourced live graph (from `S17Code`)

Replace the "build once, run once in topological order" `DAGOrchestrator` with a
trimmed port of `S17Code\s17code\core\live_graph\core.py` +
`s17code\core\live_graph\store.py`:

- `TaskSpec`, `NodeState` (PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED), `GraphPatch`
  (`add`/`connect`/`cancel`/`finish`) as the only mutation vector — an event log,
  not direct graph edits.
- `GraphStore.ready()`: returns PENDING nodes whose *all* predecessors are
  SUCCEEDED, and propagates a FAILED node's failure by cancelling its descendants
  instead of silently running them anyway (current bug in `DAGOrchestrator.execute`).
- `GraphStore` persists one JSON checkpoint per run (S17's pattern) — this is what
  gives the harness its "every run logged to disk" requirement from `PLAN.md` §7.
- Concurrency: S17 uses `asyncio` because its transport is async; this project's
  `as_client.py` MCP transport is synchronous (`urllib`), so use
  `concurrent.futures.ThreadPoolExecutor` over the same `ready()`/`record_outcome`
  loop instead of rewriting the transport to async — same scheduling algorithm,
  adapted executor, explicitly noted as the one deliberate deviation from S17's design.
- A minimal `Planner` hook (simplified `GeneralAgentPlanner` from
  `s17code/planner.py`): after each node's outcome, an optional callback can return
  a `GraphPatch` adding follow-up nodes. Concrete use in `capstone_agent.py`: once
  `DesignVersion.list` returns, if `diff_from_parent_json` is null (per `PLAN.md`
  §8 findings — it always is), the planner adds a node to read `commit_message`
  across the full version chain rather than trusting `DesignComparison` alone; once
  `release_readiness` comes back with blockers, it adds a node to fetch the
  specific blocking checklist category instead of a generic unfiltered checklist
  fetch. This directly replaces today's blind 5-task static list with an agent that
  "holds the goal across many steps" per the brief.
- Explicitly **not** ported: S13's `invariants.py` (Z3-proved admission) and
  `speculation.py` (hedged/racing branches) — real engineering value, but a
  `z3-solver` dependency and formal-verification surface is disproportionate to a
  ~5–10 node evidence-gathering graph. Noted as a possible future addition, not
  built now.
- Also not ported: S17's `Deferred`/wait-resume (pause a node for an external
  webhook/cron callback) — there's nothing in this agent that waits on external
  async events; every MCP/REST call resolves inline.

### 3. `core/harness.py` (new) — uniform run record (from `S18Code`)

Port `S18Code\harnesses\base.py`'s shape: a `Harness` protocol plus shared
`Step` (kind, target, ok, detail) and `TaskRun` (task_id, steps, claimed_success,
seconds, tool_calls, error, ended reason) dataclasses. `capstone_agent.py`'s run
method returns a `TaskRun` instead of a bare dict; the DAG engine's per-node
outcomes become `Step`s. Every run is serialized to `proofs/runs/<task_id>_<ts>.json`
**before** any scoring touches it — mirrors S18's raw-run-then-score split.

### 4. `core/eval_framework.py` — ground-truth axes + `rescore.py` (from `S18Code`)

Rewrite `BaseEvaluator` so axes take a `TaskRun` **and independently re-fetch the
relevant platform state**, instead of grepping the agent's own output text:

- Example: instead of `"bend radius" in analysis and "2.0" in analysis...`, the
  axis re-calls `DesignVersion.list` for the file itself, pulls the real
  `commit_message` text, and checks the agent's claimed numbers actually appear in
  that ground-truth string — same spirit as S18's `verified`/`unverified_pass`
  axes, adapted to this domain's "ground truth" being the platform's own records
  rather than pytest output.
- Add one qualitative axis backed by `core/judge.py` (new, simplified single-pass
  port of `s17code/evals/judge.py`'s rubric idea — no multi-model panel or
  disagreement tracking, just one LLM call scoring "is this reasoning specific and
  non-generic" against a short rubric) for the one axis (DFM reasoning quality)
  that genuinely can't be checked mechanically.
- Add `rescore.py` (root, S18 pattern): reloads `proofs/runs/*.json`, recomputes
  `eval_framework.score()` with zero LLM/API calls, writes `proofs/results.json`.
  Fixing a scoring bug becomes a free re-run instead of re-paying for the agent.
- `tasks/` (new dir): task definitions as plain JSON (prompt + which entity/file to
  target), separate from harness code. Scaffold the existing Battery Tray task as
  `tasks/t01_battery_tray_revc_dfm.json`, and **stub, not fill in**, a second task
  whose correct behavior is refusal (`PLAN.md` §7's explicit requirement) — actual
  graded task content and their pass/fail conditions must be hand-authored by the
  user per the brief's "a test authored by an LLM scores zero" rule; the framework
  only provides the plumbing to run and score whatever tasks are written.

### 5. Wire-up and cleanup

- `capstone_agent.py`: switch from `DAGOrchestrator`/`TaskNode` to the new
  `dag_engine` API, add the two interleaved-planning steps described above, return
  a `TaskRun`.
- `capstone_evals.py`: switch from `BaseEvaluator` to the new ground-truth-axis
  `eval_framework` + `core/harness.py`, write raw runs to `proofs/runs/` first.
- Delete stale duplicates superseded by the `core/`-based versions: root
  `agent_runner.py`, `evals_harness.py`, root `llm_gateway.py`.
- Add a `requirements.txt` (this project currently has no dependency manifest at
  all): `httpx`, `networkx`, `python-dotenv`, `pyyaml`. Deliberately no
  `fastapi`/`uvicorn`/`opentelemetry-*`/`faiss`/`z3-solver` — none of the scope
  cuts above need them.
- Update `SESSION_NOTES.md` §3/§5 (architecture diagram + quick-reference commands)
  to match the new module list, and add a short provenance note (which EAGv3
  session each pattern was lifted from) so the "why does this file look like
  that" question is answered in-repo.

## Verification

1. `python core/llm_gateway.py` (existing self-test entrypoint) still returns
   `GATEWAY_OK`-equivalent output, and a forced-failure test (temporarily unset the
   first provider's key) shows it skipping to the next provider via the new
   `RateState`/routing path rather than the old fixed `if self.gemini_key` chain.
2. `python capstone_agent.py` runs end-to-end against live Suryodaya, produces a
   `proofs/runs/*.json` `TaskRun` on disk, and the console output shows the two new
   interleaved-planning nodes actually firing (commit-message-chain read,
   blocker-specific checklist fetch) — confirms the DAG is replanning, not just
   running a static list.
3. `python capstone_evals.py` reads that `TaskRun`, and at least one axis's
   pass/fail is shown to flip correctly when the ground-truth platform data is
   temporarily different from the agent's claim (sanity-check the axis actually
   re-fetches rather than trusting the agent) — then `python rescore.py` reproduces
   the identical score from the saved raw run with no network/LLM calls, confirming
   the raw-run/rescore split works.
4. Confirm the deleted root files (`agent_runner.py`, `evals_harness.py`, root
   `llm_gateway.py`) have no remaining imports elsewhere (`grep -r` for their
   module names) before removal.
