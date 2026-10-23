# `agentkit/graph/` — the live task graph

*Reading order: **3rd**. Files: `engine.py`, `node_result.py`, `plan_loader.py`, `rule_planner.py`,
`policies.py`, `channels.py`, `checkpoint.py`; also `agentkit/registry.py` and `agentkit/agents/planned.py`.
Written in ASD-STE100 (Simplified Technical English) style, 2026-10-14.*

## Overview

1. A plan (a TOML file) gives the first nodes.
2. A node starts when its dependencies let it start. Nodes run in parallel on a thread pool.
3. After each node stops, declarative rules can add follow-up nodes.
4. Each node ends with a verdict.
5. The engine saves a checkpoint of the full graph after each step.

## Design decisions

### Verdicts and dependencies

- **Verdicts, not success and failure.** There are five verdicts:

  | Verdict | Meaning |
  |---|---|
  | `resolved` | The node did its work. |
  | `declined` | A refusal. This is a normal result, not a crash. |
  | `handed_off` | The node gave the work to a person or another team. |
  | `failed` | The node did not do its work. |
  | `bypassed` | The node did not run, because a necessary dependency did not resolve. |

  Thus a refusal does not stop the answer.
- **`requires = "resolved"` or `"settled"`.**
  - `resolved`: the node runs only if each dependency resolved. If not, the engine bypasses it and gives the
    reason.
  - `settled`: the node runs when each dependency stopped, with any verdict. This is a partial join.

### Actions and safety

- **Actions are an allowlist.** A node names a registered *action*, never a raw tool. The engine rejects a
  patch that names something else. Thus a plan or the planner cannot get a tool that the pack did not expose.
- **No write in a dry run.** The engine never calls a write action in a dry run:
  - If the action registers a `preview`, the engine calls the preview. The node ends as `handed_off` with
    `{"would_file": ..., "filed": false}`. An empty preview gives `resolved`, "nothing to file".
  - Without a preview, the node is `declined`.

  `raise_handoffs` is the only write action of the design-review pack. It has a preview only.
- **Transport errors become verdicts.**
  - `missing`, `not_in_seat` and `denied` give `declined`.
  - `flaky` gets retries from the retry policy of the action (never for writes). Then it gives `failed`.
  - Each other error gives `failed`.
  - An error in the code of an action gives `failed` with `error_kind = "internal"`. The other nodes continue.

### Retries and timeouts

- **The retry policy (since 2026-10-13, `policies.py`).** `RetryPolicy(max_attempts, initial_interval,
  backoff_factor, max_interval, jitter, retry_on)` operates as follows:
  - before attempt n+1, it waits `min(max_interval, initial × backoff^(n−1))` seconds;
  - with `jitter` on, it adds 0 to 1 second;
  - `max_attempts` includes the first attempt;
  - `retry_on` is a list of exception types or a predicate. The default is "a FLAKY transport failure".

  Before, the only setting was `retries=<int>`, with hard-coded doubling. `retries=n` still works and gives the
  old waits (`RetryPolicy.from_retries`). A write always gets one attempt. Jitter is off by default. Thus
  offline runs always wait the same time.
- **Action timeouts (since 2026-10-14).** `@registry.action(name, timeout_s=...)` runs each attempt in its own
  thread, with the context of the node. The engine stops waiting after `timeout_s`. The node ends as
  `failed` with `error_kind = "timeout"`. The other branches continue.
- **A timed-out attempt continues in the background.** Python cannot stop a thread. Thus the
  abandoned attempt continues in the background, and the engine ignores its result. Use timeouts only on read
  actions.

### Patches, checkpoints and validation

- **A patch changes all or nothing.** The engine raises an error before any change in these conditions:
  - an unknown action;
  - a duplicate id;
  - a missing dependency;
  - an edge into a node that already started;
  - a cycle;
  - more nodes than the node budget (30).
- **Checkpoints that you can resume.** `graph.json` (format `agentkit-graph-v1`) loads back to the same
  graph. When a run stops unexpectedly, the nodes that were running go back to `pending` on resume.
- **Optional fields in the checkpoint (since 2026-10-13).** `graph.json` can also have `channels`, but only
  when a plan declares channels. A paused node can have `interrupt` and `resume`. Ordinary runs write the
  same file as before.
- **Fallback rules (since 2026-10-15).** A rule can watch a verdict other than `resolved`:
  `verdict = "failed"`, `"declined"` or `"handed_off"`. For such a rule, the verdict alone is enough as the
  condition, because a node that did not resolve has no data to test. A rule on a resolved node still needs a
  data condition (`nonempty`, `equals`, `truthy` or `falsy`). A fallback node usually sets
  `requires = "settled"`, because the node that it waits for did not resolve.
- **Plans and rules are data.** Pydantic validates them (`PlanModel`, `NodeModel`, `RuleModel`, `WhenModel`).
  The loader rejects unknown keys, a bad `requires`, and a rule with no condition or no effect. Run
  parameters fill placeholders such as `"{file_id}"`, and their types stay the same. An unknown placeholder
  is an error.
- **Tracing (since 2026-10-12).** Each node that runs is a `node` span (action, verdict, attempts, queue
  wait). Each planner call is a `step` span. The engine submits work with
  `agentkit.telemetry.bind_context`.
- **Spans in pool threads.** Because of `bind_context`, the spans in pool threads are under the task span of
  the run. Retries are span events. See `agentkit_telemetry.md`.

## Modes, channels, pausing and history (since 2026-10-13)

| Feature | Before | After |
|---|---|---|
| Scheduling | Eager only. A node starts when it can. The planner sees the outcomes in the sequence in which nodes stop. | Eager is still the default. With **`mode = "superstep"`** (in the plan file, or `run_plan(mode=)`), all ready nodes run in parallel on the same snapshot. When all stop, the engine records the outcomes, applies the channel writes and asks the planner, **in node-id sequence**. Thus thread timing cannot change the state, the patches or the checkpoints. `max_steps` (50) gives `StepLimitExceeded`. |
| Shared state | None. Each node writes only its own `NodeResult`. | Optional **channels**, declared in the plan (`[channels]`): `last_value`, `topic` (append), `reducer` (`sum`, `max`, `min`, `extend`, `merge`, `union`, with `Overwrite`), `ephemeral`, `barrier` (join). A node writes `NodeResult(writes={...})`. An action reads `read_channels()`. Two writers of one `last_value` in one superstep give `ChannelConflict`. Thus a race becomes an error. |
| A pause for a person | Not possible. | An action calls `interrupt(payload)`. The node is **paused**, with no verdict, and its dependants wait. `store.resume(node, answer)` and `continue_from()` run it again, and `interrupt()` gives the answer. `approve_writes=True` does this before each real write. Only `True` or `{"approved": true}` lets the write run. Each other answer gives `declined`. |
| History | `graph.json` has only the latest state. | With a `CheckpointSaver` (`MemorySaver`, `JsonlSaver` to `checkpoints.jsonl`, or `SqliteSaver`), each step is also a checkpoint: `{id, ts, parent_id, metadata{source: input/loop/fork, step, nodes, ran}, graph}`. The ids sort by time. `store.history(before=, limit=, filter=)` gives the newest first. This is off when no saver is given. |
| Fork | — | `store.fork({node: NodeResult}, from_checkpoint=None)` gives a new store with those results replaced. Everything after them is undone: plan nodes go back to `pending`, and nodes from the planner go, so the rules decide again. The channels are made again. The source store does not change. `agentkit.harness.replay` uses this. |
| Rules | A rule could fire two times and collide after a new run. | A rule never adds a node id that exists. It never links into a node that started. |

The real value of these features:

- The shipped plans stay eager. Superstep gives the same findings on the offline tasks, and a test examines
  this. But superstep waits for the slowest node at each step.
- The project uses three of these features each day. They are the retry policy, fork and replay (CI
  calibration), and the approval gate for live writes.

*The modes, channels, pause and resume, and checkpoint history come from an external open-source agent-graph
design. This module is a new implementation in plain Python. It does not import or need that design.*

## What it does

- `run_plan(env, registry, plan)` makes a `LiveGraphExecutor` with a `RulePlanner` on the rules of the plan.
  It gives the `GraphStore`.
- `record_steps(run, store)` copies the verdict, reason, error kind, attempts, source and timing of each node
  into the run record.
- `answer_with_pack(...)` makes the finding and the answer. See `agentkit_answer.md`.

A rule in TOML:

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

## History in SQLite (since 2026-10-21)

`SqliteSaver` (`sqlite_saver.py`) keeps the checkpoints of many runs in one SQLite file. It has the same
`put`, `get` and `list(before, limit, filter)` as `MemorySaver`. Each checkpoint has a thread id: the run
that it is part of.

- `SqliteSaver(path).for_thread(run_id)` gives the saver of one run. Its `get` and `list` see only that run.
- `SqliteSaver(path)` without a thread sees all the runs. `threads()` gives each run with its number of
  checkpoints. `put` uses the `run_id` in the checkpoint metadata.
- The file uses WAL mode and a busy timeout. Thus readers do not stop the writer, and two processes can
  write the same file.
- `run_task(..., saver=)` and `resume_task(..., saver=)` take a saver. When `graph.json` is missing,
  `resume_task` continues from the newest checkpoint in the saver.

The agent service puts the history of each run in `runs/service.sqlite`.

## Key types

| Name | Purpose |
|---|---|
| `RunEnv(client, run_id, params, dry_run, tenant, gateway)` | What an action gets, in addition to its arguments. |
| `TaskSpec` | One node: `id`, `action`, `args`, `after`, `requires`, `result`, `attempts`, timing. |
| `GraphPatch(add, link)` | The only way to change the graph. |
| `GraphStore` | Nodes, validation, the ready set and the checkpoint; `channels`, `paused()`, `resume()`, `history()`, `fork()`. |
| `LiveGraphExecutor(mode=, channels=, saver=, approve_writes=, max_steps=)` | Runs the ready set and applies the patches of the planner. |
| `NodeResult(verdict, data, reason, error_kind, writes)` | How a node ended, with optional channel writes. |
| `RetryPolicy` | When to try an action again, and how long to wait. |
| `Channel` types, `ChannelState` | Shared state with declared merge rules. |
| `Checkpoint`, `MemorySaver`, `JsonlSaver` | The history. |
| `SqliteSaver(path)`, `.for_thread(run_id)`, `.threads()` | The history of many runs in one SQLite file. Other processes can read and write the same file. |
| `interrupt()`, `approved()` | A pause of a node for a person. |
| `Registry`, `ActionSpec` | The actions of a pack (with `retries` or `retry`, `writes`, `timeout_s` and `preview` for a write action), the probes, checks, mutants, replay faults and extras. |

## How to test

1. The hand-written template is `tests/test_dag_engine.py`. Use a `Registry()` with small fake actions and a
   fake client. You do not need MCP.
2. For superstep: put a `random.uniform` sleep in an action. This changes the sequence in which nodes stop.
   The superstep results must not change.
3. For the history: use `MemorySaver` and `fork` on a graph of three nodes. The reference tests for
   `SqliteSaver` are in `tests_local/test_sqlite_saver.py`.
4. For a pause: use an action that calls `interrupt()`.
5. For a timeout: use an action that sleeps longer than its `timeout_s`.
