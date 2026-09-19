# `core/dag_engine.py` — the event-sourced live graph

*Reading order: **4th**. No project imports (only `networkx` + stdlib). This is the most structurally important file in `core/` — read it slowly.*

## 10,000-ft view

A task graph that isn't fixed at the start of a run. You hand it an initial list of `TaskSpec` nodes; it runs everything whose dependencies are satisfied (concurrently, on a thread pool); after each node finishes, an optional `Planner` gets to look at what just happened and inject *more* nodes into the same graph before the next round runs. A failed node cancels its own dependents instead of letting them run on garbage input. Every mutation is written to a JSON checkpoint on disk. This is what turns "run 5 fixed API calls" into "gather evidence, notice something, go gather more evidence."

## Why it's written this way (intent & trade-offs)

- **Mutation only through `GraphPatch` (`add`/`cancel`), never direct graph edits.** This is the single most important design decision in the file. It means every change to the graph's shape is a discrete, loggable event — `apply_patch()` is the only method that touches `self.graph.add_node`/`add_edge`. The trade-off: slightly more ceremony to add a node (you build a `GraphPatch`, you don't just call `graph.add_node()`) in exchange for a graph whose entire history is reconstructable from a sequence of patches, and a natural place (`apply_patch`) to enforce the "still a DAG after this change" invariant on every single mutation, not just at the end.
- **`ThreadPoolExecutor`, not `asyncio`.** The docstring is explicit about this: it's a deviation from the `S17Code` source design (which uses `asyncio` because its transport is async), made because `as_client.py`'s MCP transport is synchronous `urllib`. Threads let independent MCP calls actually overlap in wall-clock time without rewriting the transport layer. The trade-off: this file now has to reason about thread-safety (see "what it does" below for why that's actually fine), which an asyncio single-threaded design wouldn't need to.
- **A failed node cancels its descendants (`_cancel_subtree` from `record_outcome`).** The first-draft `DAGOrchestrator` (see git history) had no such thing: `nx.topological_sort()` would walk every node regardless of an earlier node's failure, storing `{"error": ...}` as that node's "result" and letting anything depending on it run anyway with a garbage input. This file treats a failure as contagious by default. The trade-off: there's no way, today, for a node to say "I failed, but my sibling doesn't actually need my output, let it run anyway" — cancellation always propagates to every descendant, not just the ones that structurally depend on the *specific field* that failed. That's a real limitation if a future task graph has a node with multiple semi-independent uses of its output.
- **One JSON checkpoint file per run, rewritten in full on every mutation (`_checkpoint`).** Not an append-only event log — the whole current state is serialized every time. Simple to reason about and to read back (`GraphStore` itself has no `.load()` — only `capstone_agent.py`'s separate `TaskRun.save()`/`.load()` persists the *harness* record; this checkpoint is a lower-level, DAG-internal debugging artifact, not something anything currently reads back in). The trade-off: for a much larger graph this would mean re-writing an ever-growing file on every single node completion, which doesn't scale — fine for this project's ~5-10 node graphs.
- **No `Deferred`/wait-resume mechanism.** Explicitly scoped out (see docstring): `S17Code`'s source design supports a node pausing itself for an external async callback (a webhook, a cron tick) to resume later. Nothing in this agent's workflow needs that — every MCP/REST call it makes resolves synchronously within the same process. If this engine were ever reused for a workflow that *does* need to wait on something external, this is the documented gap to fill.
- **No `S13Code`-style formally-verified admission oracle or speculative/hedged branch racing.** Also explicitly scoped out — a `z3-solver` dependency and proof surface for a handful of evidence-gathering nodes was judged disproportionate. This is the trade-off most worth revisiting if this engine ever grows to orchestrate something with real safety-critical concurrent-write concerns.

## What it does (walkthrough)

1. **`NodeState`** — a 5-value enum (`PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED`) that is also a `str` subclass, so `spec.state.value` gives you the plain string for JSON serialization without an extra conversion step.
2. **`TaskSpec`** — one node. Mutable (not frozen) — `LiveGraphExecutor` and `GraphStore` both mutate a `TaskSpec`'s `state`/`result`/`error`/timestamps in place rather than replacing it, which is why the *same* `TaskSpec` instance you pass to `LiveGraphExecutor.run()` is the one whose fields have been filled in by the time you inspect `store.all_specs()` afterward.
3. **`GraphPatch`** — the event type. `add` for new nodes (their `dependencies` must reference node ids *already in the graph* — you can't add two mutually-dependent nodes in the same patch; add the first, then the second in a later patch). `cancel` for marking a subtree cancelled.
4. **`Planner` (a `Protocol`, not a base class)** — structural typing: anything with a matching `on_outcome(store, node_id, spec)` method satisfies this, with no inheritance required. `capstone_agent.py`'s `DesignReviewPlanner` is the one implementation in this project.
5. **`GraphStore`**:
   - `apply_patch`: adds nodes, then adds edges (two passes, because an edge can't be added until both its endpoints exist — this is why `add` is processed as "all nodes first, then all edges" rather than one node+edges at a time), verifies the result is still a DAG (raises `ValueError` if not — this is the one hard safety check in the whole file), processes cancellations, then checkpoints.
   - `_cancel_subtree`: recursive, no cycle-guard needed because `apply_patch` already guarantees acyclicity before this can ever run. Only flips `PENDING → CANCELLED` (a `RUNNING` or already-`SUCCEEDED` node is left alone even if you ask to cancel it — you can't retroactively cancel work that already happened).
   - `ready()`: the scheduler primitive — a node qualifies if it's `PENDING` and *every* predecessor (not just one) is `SUCCEEDED`. A node with zero predecessors is trivially ready (the `all()` over an empty list is `True`).
   - `record_outcome`: the only place a node's terminal state is set. `error is not None` is the branch selector (not "is the result falsy") — this means a legitimately falsy successful result (e.g. an empty list from an MCP `.list` call) is correctly recorded as `SUCCEEDED`, not misread as a failure.
   - `all_specs` / `pending_or_running` / `results`: read-only views. `results()` is what `capstone_agent.py` calls to build its evidence dict for the LLM prompt — only `SUCCEEDED` nodes' results are included, so a failed or cancelled node's absence from the prompt is implicit, not an explicit "N/A" the LLM has to be told to ignore.
6. **`LiveGraphExecutor.run(run_id, initial_tasks)`** — the actual loop:
   - Seed the graph with `initial_tasks` via one `GraphPatch`.
   - **Tick**: while anything is `PENDING`/`RUNNING`, compute `ready()`. If nothing is ready (and nothing is currently running, since the previous tick's futures were already fully drained — see below), break out — this is the "stuck" safety valve for a malformed graph, not an expected path in normal operation.
   - Submit every ready node to the thread pool, marking each `RUNNING` *before* submission (so a concurrently-computed `ready()` on the next tick, if this were re-entered, wouldn't double-submit the same node — moot today since the loop is single-threaded at the orchestration level, but a real correctness property worth keeping).
   - `as_completed(futures)` drains this tick's batch **in the calling (main) thread**, one at a time — `store.record_outcome()` and any `planner.on_outcome()`-triggered `store.apply_patch()` therefore never run concurrently with each other, even though the *node execution itself* (`_execute_node`, calling out to MCP) does run concurrently across worker threads. This is the thread-safety property that makes the whole design work without locks: worker threads only ever read a `TaskSpec`'s `tool_name`/`tool_args` (never mutated after creation) and return a plain value; all graph-structure mutation happens on one thread.
   - After each individual outcome is recorded, the planner is consulted immediately (not batched until the whole tick finishes) — so if two nodes in the same tick both complete, and the planner reacts to the first one by adding a new node, that new node could theoretically become ready and get scheduled in the very next tick, without waiting for the second node in the current batch to also finish. This is what "interleaved" means in the docstring.
   - Returns the finished (or stuck) `GraphStore`.
7. **`_execute_node`**: the thread-pool work unit. Trivial — logs, then delegates to `tool_executor` if the spec has a `tool_name`. A node with no `tool_name` (or no executor configured) simply "succeeds" with a `None` result — useful for a purely structural/grouping node, though nothing in this project currently uses that.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `NodeState` | `str, Enum` | The five-state lifecycle every node moves through, in one direction only (no state currently transitions backward). |
| `TaskSpec.id` | `str` | Unique node identifier; used as the `networkx` node key. |
| `TaskSpec.tool_name` / `.tool_args` | `Optional[str]` / `Dict[str, Any]` | What `_execute_node` hands to the injected `tool_executor` — in this project, always an MCP tool name and its JSON-RPC arguments. |
| `TaskSpec.dependencies` | `List[str]` | Node ids this node's edges originate from; validated against the graph at `apply_patch` time. |
| `TaskSpec.result` / `.error` | `Any` / `Optional[str]` | Mutually exclusive in practice (`record_outcome` sets exactly one) but not enforced by the type system — nothing stops a caller from setting both by hand. |
| `TaskSpec.started_at` / `.finished_at` | `Optional[float]` | Unix timestamps, set by `LiveGraphExecutor`/`GraphStore` respectively — not user-supplied. |
| `GraphPatch.add` / `.cancel` | `List[TaskSpec]` / `List[str]` | The two only things a patch can express. An empty patch (`GraphPatch()`) is valid and a no-op — `LiveGraphExecutor` checks `if patch and (patch.add or patch.cancel)` before applying one, so a planner returning `GraphPatch()` instead of `None` behaves identically to returning `None`. |
| `GraphStore.FORMAT` | `str` class constant | `"designreview-dag-v1"`, written into every checkpoint — a version tag for a future reader to know how to parse the file if the schema ever changes. |
| `GraphStore.graph` | `networkx.DiGraph` | The actual graph; each node's data dict has exactly one key, `"spec"`, holding the `TaskSpec`. |
| `GraphStore.checkpoint_dir` | `Optional[Path]` | If `None`, `_checkpoint()` is a no-op — checkpointing is opt-in per `GraphStore` instance, not a hardcoded path. |
| `LiveGraphExecutor.tool_executor` | `Optional[Callable[[str, Dict[str, Any]], Any]]` | The injection point that connects this generic engine to `as_client.py`'s MCP calls — `core/dag_engine.py` itself has zero knowledge of MCP, AgentSwitch, or HTTP. |
| `LiveGraphExecutor.max_workers` | `int` (default `4`) | Thread pool size — an arbitrary but reasonable cap for a handful of concurrent MCP calls against a shared platform; not derived from anything (no measurement backs the number `4`). |

## Function/method reference

| Function/Method | Inputs → Output | Notes |
|---|---|---|
| `_safe_json(value)` | `Any` → `Any` | Tries `json.dumps`; on `TypeError` (a non-serializable object snuck into a result), returns `str(value)` instead of crashing the checkpoint write. |
| `GraphStore.apply_patch(patch)` | → `None` | Can raise `ValueError` twice over: unknown dependency, or a cycle. Both are real invariant violations, not recoverable — callers should let these propagate, not swallow them. |
| `GraphStore._cancel_subtree(node_id)` | → `None` | Recursive; touches only `PENDING` nodes. |
| `GraphStore.ready()` | → `List[TaskSpec]` | O(nodes × avg in-degree) — fine at this project's scale, would need an index for a much larger graph. |
| `GraphStore.record_outcome(node_id, result, error)` | → `TaskSpec` | Returns the same (mutated) spec passed in, for convenience at call sites. |
| `GraphStore._checkpoint()` | → `None` | No-op if `checkpoint_dir` is unset. Uses `default=str` in `json.dumps` as a second safety net beyond `_safe_json` (belt-and-suspenders for non-serializable data). |
| `LiveGraphExecutor.run(run_id, initial_tasks)` | → `GraphStore` | The one entry point. Never raises on a node failure (failures are recorded, not propagated as exceptions) — it can raise on a malformed initial graph (via `apply_patch`'s validation). |
| `LiveGraphExecutor._execute_node(spec)` | `TaskSpec` → `Any` | Runs on a worker thread; any exception it raises is caught by `run()`'s `except Exception as e` and converted into `record_outcome(error=str(e))` — so a tool executor's exception message is exactly what ends up in `TaskSpec.error`. |

## How to test it

This is the highest-value file in the project to build real unit test coverage for, because it's pure in-memory logic with one clean injection point (`tool_executor`) — no network, no LLM, no real MCP calls needed.

```python
def fake_tool_executor(tool_name, tool_args):
    return {"tool": tool_name, "args": tool_args}

def test_linear_chain_runs_in_order():
    a = TaskSpec(id="a", name="a", tool_name="t")
    b = TaskSpec(id="b", name="b", tool_name="t", dependencies=["a"])
    store = LiveGraphExecutor(tool_executor=fake_tool_executor).run("run1", [a, b])
    assert store.all_specs()[0].state == NodeState.SUCCEEDED
    assert store.results()["b"] == {"tool": "t", "args": {}}

def test_failed_node_cancels_descendants():
    def failing_executor(tool_name, tool_args):
        raise RuntimeError("boom")
    a = TaskSpec(id="a", name="a", tool_name="t")
    b = TaskSpec(id="b", name="b", tool_name="t", dependencies=["a"])
    store = LiveGraphExecutor(tool_executor=failing_executor).run("run2", [a, b])
    specs = {s.id: s for s in store.all_specs()}
    assert specs["a"].state == NodeState.FAILED
    assert specs["b"].state == NodeState.CANCELLED  # never ran

def test_planner_adds_followup_node():
    class OneShotPlanner:
        def __init__(self): self.fired = False
        def on_outcome(self, store, node_id, spec):
            if node_id == "a" and not self.fired:
                self.fired = True
                return GraphPatch(add=[TaskSpec(id="b", name="b", dependencies=["a"])])
            return None
    a = TaskSpec(id="a", name="a")
    store = LiveGraphExecutor(planner=OneShotPlanner()).run("run3", [a])
    assert "b" in {s.id for s in store.all_specs()}

def test_unknown_dependency_raises():
    orphan = TaskSpec(id="b", name="b", dependencies=["does_not_exist"])
    with pytest.raises(ValueError):
        LiveGraphExecutor().run("run4", [orphan])

def test_concurrent_ready_nodes_actually_overlap(monkeypatch):
    # assert two independent nodes' execution windows overlap in wall-clock
    # time, proving ThreadPoolExecutor concurrency is real, not accidental
    # sequential execution that happens to pass other tests.
    ...
```

## Tests that should be added for stability

In priority order:

1. **The five tests sketched above don't exist yet anywhere in this repo** — they're the minimum bar for "this engine's core contract is proven," and should be written before anything else touches this file again.
2. **A test for the "stuck" break path** (`if not ready: break` while `pending_or_running()` is still `True`) — construct a graph where this can actually happen (e.g. a node depending on another node that's `FAILED` but somehow wasn't cancelled — which shouldn't be reachable through normal `record_outcome`, but a test should confirm the engine degrades safely rather than infinite-looping if it ever is reached) and assert the run terminates with the expected partial `GraphStore` rather than hanging.
3. **A concurrency test that actually proves nodes run in parallel**, not just that the final result is correct — e.g. two ready nodes whose `tool_executor` each sleep 0.2s; assert total wall-clock time for the tick is closer to 0.2s than 0.4s. Correctness tests alone wouldn't catch a regression to accidental serial execution.
4. **A test that a `GraphPatch` with both new nodes AND a cancellation in the same patch applies in the documented order** (add-then-cancel) — currently implicit in the code's structure but not tested, so a future refactor could silently reorder it.
5. **A test for checkpoint file correctness**: run a small graph with `checkpoint_dir` set, read the JSON back, and assert its `nodes` list matches `store.all_specs()` — this is the file's "every run logged to disk" guarantee (relied on by `PLAN.md` §7's grading bar) and currently has zero test coverage.
6. **A test for `_safe_json`'s fallback path** — pass a non-JSON-serializable object (e.g. a raw `set()` or a custom class instance) as a node's `result` and confirm the checkpoint write doesn't crash and stores `str(value)` instead.
7. **A test that a planner raising an exception inside `on_outcome` doesn't silently swallow the rest of the run** — currently, `LiveGraphExecutor.run()` has no try/except around `self.planner.on_outcome(...)`, so a buggy planner would crash the whole run with an unrelated-looking traceback. Decide whether that's the desired behavior (probably yes — a broken planner shouldn't silently produce a half-finished graph) and add a test that pins it down explicitly rather than leaving it as an accident of the current code shape.
