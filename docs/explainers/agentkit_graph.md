# `agentkit/graph/` — the live task graph

*Reading order: **3rd**. Files: `engine.py`, `node_result.py`, `plan_loader.py`, `rule_planner.py`; plus
`agentkit/registry.py` and `agentkit/agents/planned.py`.*

## 10,000-ft view

A plan (TOML) lists the first nodes; nodes run as soon as their dependencies allow, in parallel on a thread
pool; after each node finishes, declarative rules may add follow-up nodes. Every node ends with a verdict, and
the whole graph is checkpointed after every step.

## Why it's written this way

- **Verdicts, not success/failure.** `resolved`, `declined` (a refusal — normal, not a crash), `handed_off`,
  `failed`, `bypassed` (never ran because a dependency it required didn't resolve). A refusal therefore doesn't
  take the answer down with it.
- **`requires = "resolved" | "settled"`.** `resolved`: run only if every dependency resolved, else bypass with
  the reason. `settled`: run once every dependency finished in any verdict — a partial join.
- **Actions are an allowlist.** A node names a registered *action*, never a raw tool. A patch naming anything
  else is rejected, so neither a plan nor the planner can reach a tool the pack didn't expose. The design-review
  pack registers no write actions, and a write action in a dry run is declined without being called.
- **Transport errors map to verdicts.** `missing`, `not_in_seat`, `denied` → declined; `flaky` → retried up to
  the action's `retries` (never for writes) then failed; anything else → failed; a bug in an action → failed
  with `error_kind = "internal"`, siblings keep running.
- **Patches are all-or-nothing.** Unknown action, duplicate id, missing dependency, an edge into a node that
  already started, a cycle, or exceeding the node budget (30) raises before anything changes.
- **Resumable checkpoints.** `graph.json` (format `agentkit-graph-v1`) round-trips; nodes that were mid-flight
  when a run died go back to pending on resume.
- **Plans and rules are data**, validated by pydantic (`PlanModel`, `NodeModel`, `RuleModel`, `WhenModel`):
  unknown keys, a bad `requires`, a rule with no condition or no effect are rejected at load time. Placeholders
  like `"{file_id}"` are filled from run parameters (types kept); an unknown placeholder is an error.

## What it does

`run_plan(env, registry, plan)` builds a `LiveGraphExecutor` with a `RulePlanner` over the plan's rules and
returns the `GraphStore`. `record_steps(run, store)` copies each node's verdict, reason, error kind, attempts,
who added it and timing into the run record. `answer_with_pack(...)` (see `agentkit_answer.md`) builds the
finding and the answer.

A rule, in TOML:

```toml
[[rules]]
name = "failed_checklists_when_gate_blocked"
[rules.when]
node = "release_gate"
nonempty = ["result.blockers", "result.reason_codes"]   # or equals / truthy / falsy
[[rules.add]]
id = "failed_checklists"
action = "list_failed_checklists"
args = { file_id = "{file_id}" }
after = ["release_gate"]
```

## Key types

| Name | Purpose |
|---|---|
| `RunEnv(client, run_id, params, dry_run, tenant, gateway)` | what an action receives besides its args |
| `TaskSpec` | one node: id, action, args, after, requires, result, attempts, timing |
| `GraphPatch(add, link)` | the only way to change the graph |
| `GraphStore` | nodes + validation + ready-set + checkpoint |
| `LiveGraphExecutor` | runs the ready set, applies planner patches |
| `NodeResult(verdict, data, reason, error_kind)` | how a node ended |
| `Registry` / `ActionSpec` | a pack's actions (with `retries`, `writes`), probes, checks, mutants, extras |

## How to test

`tests/test_dag_engine.py` — a `Registry()` with small fake actions and a fake client; no MCP needed.
