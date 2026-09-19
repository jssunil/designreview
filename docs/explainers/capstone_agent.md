# `capstone_agent.py` — the Design Review agent

*Reading order: **8th**. This is where every `core/` module and `as_client.py` get wired together into the actual answer to "what changed between rev B and rev C, and are there manufacturability problems in this part?" Read all seven prior files first — this one won't make sense without them.*

## 10,000-ft view

Two classes. `DesignReviewPlanner` implements `core.dag_engine.Planner`'s `on_outcome` hook with exactly one domain-specific rule: if release-readiness comes back blocked, go fetch the specific failed checklist items. `Team21DesignReviewAgent` builds a 4-node evidence-gathering graph, runs it through `LiveGraphExecutor`, hands the results to `LLMGateway` for synthesis, and returns a `TaskRun`. This file is intentionally thin — almost everything it does is *configuration* of the generic engines from `core/`, not new logic.

## Why it's written this way (intent & trade-offs)

- **The planner is a separate class from the agent, implementing `Planner` structurally (no inheritance).** This mirrors `core/dag_engine.py`'s design choice directly — `DesignReviewPlanner` isn't a `core.dag_engine.Planner` subclass, it just has a matching `on_outcome` method. The trade-off: nothing stops someone from constructing a `DesignReviewPlanner` with a typo'd method name and only discovering the bug when `LiveGraphExecutor` tries to call it and silently does nothing (since `if self.planner:` is truthy for any non-`None` object, whether or not it actually has `on_outcome`).
- **`DesignReviewPlanner.bind(file_id)` exists because the planner is constructed *before* `file_id` is known at the call site inside `run()`.** Rather than passing `file_id` to `__init__`, there's a two-step `DesignReviewPlanner().bind(file_id)` — a small, slightly awkward builder pattern that exists only because the planner needs the same `file_id` the initial tasks were built with, and the cleanest way to guarantee they can't drift apart was to construct the planner and its initial tasks in the same method, then bind. A cleaner alternative (pass `file_id` straight into `DesignReviewPlanner.__init__`) was available and not taken — worth revisiting.
- **`_added_followup: bool` guards against adding the same follow-up node twice.** `on_outcome` fires once per *finished node*, and `get_readiness` only finishes once — so in the current graph shape this guard is defensive rather than strictly necessary. It matters if the graph shape ever changes such that `get_readiness`-equivalent evidence could be gathered by more than one node, or if a future planner rule re-checks the same condition on a later event.
- **Tool names/params were discovered live, not assumed from the repo's static `agentswitch_tools.json`.** This is documented directly in a comment above `initial_tasks`: an earlier draft of this file (written before a live `tools/list` call was made) used a generic `list`/`get`-with-`entity`-param shape that turned out to be a *different, non-existent* tool surface for this seat. The real MCP catalogue uses per-entity tool names (`DesignFile.get`, `DesignVersion.list`, ...) with flat keyword arguments. **This is worth calling out as the single most important lesson from building this file**: the repo's own static JSON fixtures were actively misleading, and the only way to get this right was to query the live platform's `tools/list` and `inputSchema` for each tool before writing the call.
- **The synthesis prompt explicitly instructs the LLM not to fabricate** ("If the evidence above does not actually support a claim... say so explicitly — do not fabricate a confident-sounding answer") and explicitly warns it not to claim a geometry-based diff was run, since `diff_from_parent_json` is null in this build. This is a direct response to `PLAN.md`'s documented finding that the platform's own `ai-review` endpoint used to silently fabricate a "completed" review with zero real findings — the prompt is written to make the *opposite* failure mode (honest refusal over confident fabrication) the path of least resistance for the model, and `capstone_evals.py::axis_did_not_fabricate_geometry_diff` checks for this specific failure mode mechanically.
- **`_mcp_tool_executor` raises on a JSON-RPC `"error"` key, not just an HTTP error.** This is the fix for the exact footgun documented in `as_client.py`'s explainer (JSON-RPC errors return HTTP 200). Without this check, a tool-argument-schema violation (like the real `DesignChecklistResult.list` schema mismatch hit and fixed during this session — see below) would have silently returned an error *object* as if it were a successful result, and the DAG engine would have recorded it as `SUCCEEDED` with a nonsense "result."
- **`answer_capstone_benchmark` is kept as a thin alias for `run`.** Two public entrypoints doing the same thing is redundant on its face; it exists purely for call-site readability (`agent.answer_capstone_benchmark()` reads better in a demo script than `agent.run()`), a minor and arguably unnecessary duplication.
- **A real bug was found and fixed live, in this exact file, while verifying it**: the follow-up checklist call originally used `{"status": "fail"}`, but the real `DesignChecklistResult.list` MCP tool's schema uses a field named `overall_result` (enum `pass`/`fail`/`needs_review`), not `status`, and has `additionalProperties: false` — so the wrong field name was outright rejected rather than silently ignored. This was caught by actually running the agent end-to-end against the live platform and reading the resulting `-32602 Invalid tool arguments` error, not by static review.

## What it does (walkthrough)

1. **Module constants**: `PROOFS_DIR`, `CHECKPOINT_DIR` (both under `proofs/`, kept separate — `runs/` holds `TaskRun` records, `checkpoints/` holds `GraphStore`'s lower-level DAG-state snapshots), `BATTERY_TRAY_FILE_ID` (hardcoded to the one file on the platform with a genuine, legible multi-revision history — see `SESSION_NOTES.md` §1.1), `DEFAULT_PROMPT` (the literal Section 8 benchmark question).
2. **`DesignReviewPlanner.on_outcome`**: checks if the just-finished node is `get_readiness`, succeeded, and hasn't already triggered a follow-up. If so, drills into the real response shape (`spec.result["result"]["blockers"]` or `["reason_codes"]`), and if either is non-empty, returns a `GraphPatch` adding a `get_blocking_checklists` node depending on `get_readiness`.
3. **`Team21DesignReviewAgent.__init__`**: constructs an `AgentSwitchClient`, logs in, runs the MCP handshake, and constructs an `LLMGateway` — all eagerly, at agent-construction time (meaning `Team21DesignReviewAgent(tenant)` itself makes network calls; there's no lazy/deferred login).
4. **`_mcp_tool_executor(tool_name, tool_args)`**: the bridge between `core/dag_engine.py`'s generic `tool_executor` callback and `as_client.py`'s `call_mcp`. Checks for a JSON-RPC error, then unwraps `structuredContent` if present (falling back to the raw `result` dict otherwise — relevant for tools that don't populate `structuredContent`).
5. **`run(task_id, prompt, file_id)`** — the main method:
   - Builds a `TaskRun` immediately (before any work happens), so even a total early failure would have *something* to save (though in practice, if `__init__`'s login fails, this method is never reached at all).
   - Builds the 4 `initial_tasks` (`get_file`, `get_versions`, `get_readiness`, `get_feedback`) — all independent of each other (no `dependencies` between them), so all four run concurrently in the DAG engine's first tick.
   - Constructs a `LiveGraphExecutor` bound to `_mcp_tool_executor` and a freshly-bound `DesignReviewPlanner`, runs it with a UUID-suffixed `run_id` (so repeated runs of the same `task_id` don't collide on the checkpoint filename — note this is *more* collision-resistant than `TaskRun.save`'s second-precision filename, an inconsistency between the two persistence mechanisms worth reconciling).
   - Converts every finished `TaskSpec` into a `Step` on the `TaskRun` (this is the moment the DAG engine's internal state becomes the harness's uniform record).
   - Builds `evidence = store.results()` and interpolates it into a long, structured synthesis prompt.
   - Calls the gateway on the `"quality"` tier; on success, records the answer and a successful `llm_call` step; on any exception, records the error on both the `TaskRun` itself (`run.error`, `run.ended = "error"`) and as a failed step.
   - Sets `run.seconds`, saves the run to disk, returns it.
6. **`answer_capstone_benchmark`**: one-line alias, see trade-offs above.
7. **`__main__` block**: constructs the agent, runs it, prints the answer and the cost ledger summary.

## Types & variables

| Name | Type | Purpose |
|---|---|---|
| `BATTERY_TRAY_FILE_ID` | `str` (UUID) | Hardcoded target — this agent currently answers the benchmark for exactly one file, not a general "any file" query. A real generalization would need this to come from the prompt/task definition instead. |
| `DesignReviewPlanner._added_followup` | `bool` | One-shot guard, see trade-offs. |
| `DesignReviewPlanner._file_id` | `Optional[str]` | Set via `.bind()`, not the constructor — see trade-offs. |
| `Team21DesignReviewAgent.tenant` | `str` | `"suryodaya"` or `"keystone"` — passed straight through to `AgentSwitchClient`. |
| `Team21DesignReviewAgent.mcp_client` | `AgentSwitchClient` | The one platform connection this agent uses for every tool call. |
| `Team21DesignReviewAgent.gateway` | `LLMGateway` | The one LLM connection this agent uses for its single synthesis call. |

## Function/method reference

| Function/Method | Inputs → Output | Notes |
|---|---|---|
| `DesignReviewPlanner.on_outcome(store, node_id, spec)` | → `Optional[GraphPatch]` | Only reacts to `node_id == "get_readiness"`; returns `None` for every other node, every time. |
| `DesignReviewPlanner.bind(file_id)` | `str` → `DesignReviewPlanner` (self) | Returns `self` so it can be chained inline: `DesignReviewPlanner().bind(file_id)`. |
| `Team21DesignReviewAgent._mcp_tool_executor(tool_name, tool_args)` | → `Any` | Raises `RuntimeError` on a JSON-RPC error; this is what `core/dag_engine.py`'s `_execute_node` catches and converts into a `FAILED` node state. |
| `Team21DesignReviewAgent.run(task_id, prompt, file_id)` | → `TaskRun` | The one method that does everything; conforms to `core.harness.Harness`. |
| `Team21DesignReviewAgent.answer_capstone_benchmark(query)` | `str` → `TaskRun` | Alias for `run(prompt=query)`. |

## How to test it

This file is the hardest in the project to unit-test in isolation, because `__init__` itself makes live network calls (login + MCP handshake) — you cannot construct a `Team21DesignReviewAgent` at all without a reachable AgentSwitch instance and valid credentials. That's a real testability gap, not just an inconvenience.

- **Fully unit-testable today, no changes needed**: `DesignReviewPlanner.on_outcome` — it only needs a fake `TaskSpec` with the right `state`/`result` shape, no real `GraphStore` or network required.
- **Needs a refactor to be unit-testable**: everything in `Team21DesignReviewAgent`. The constructor doing eager login/handshake means there's no way to construct an agent with a *fake* `mcp_client`/`gateway` without either (a) monkeypatching `AgentSwitchClient.login`/`init_mcp` to no-ops, which is brittle, or (b) restructuring `__init__` to accept an already-constructed client/gateway (dependency injection) so a test can pass in fakes directly. Option (b) is the better long-term fix.
- **Integration test, needs live platform + real API keys**: the actual `run()` method end-to-end — this is what was manually exercised during this session, but there is no automated version of that test today.

```python
def test_planner_adds_followup_when_blockers_present():
    planner = DesignReviewPlanner().bind("file-123")
    spec = TaskSpec(id="get_readiness", name="x", state=NodeState.SUCCEEDED,
                     result={"result": {"blockers": [{"id": "b1"}]}})
    patch = planner.on_outcome(store=None, node_id="get_readiness", spec=spec)
    assert patch is not None
    assert patch.add[0].tool_name == "DesignChecklistResult.list"
    assert patch.add[0].tool_args["file_id"] == "file-123"

def test_planner_does_nothing_when_no_blockers():
    planner = DesignReviewPlanner().bind("file-123")
    spec = TaskSpec(id="get_readiness", name="x", state=NodeState.SUCCEEDED,
                     result={"result": {"blockers": [], "reason_codes": []}})
    assert planner.on_outcome(store=None, node_id="get_readiness", spec=spec) is None

def test_planner_only_fires_once():
    planner = DesignReviewPlanner().bind("file-123")
    spec = TaskSpec(id="get_readiness", name="x", state=NodeState.SUCCEEDED,
                     result={"result": {"blockers": [{"id": "b1"}]}})
    first = planner.on_outcome(store=None, node_id="get_readiness", spec=spec)
    second = planner.on_outcome(store=None, node_id="get_readiness", spec=spec)
    assert first is not None and second is None

def test_planner_ignores_other_nodes():
    planner = DesignReviewPlanner().bind("file-123")
    spec = TaskSpec(id="get_versions", name="x", state=NodeState.SUCCEEDED, result={"data": []})
    assert planner.on_outcome(store=None, node_id="get_versions", spec=spec) is None
```

## Tests that should be added for stability

In priority order:

1. **The four `DesignReviewPlanner` tests above** — pure logic, zero network, currently zero coverage, and this is the one piece of genuinely novel domain logic in the whole file (everything else is wiring).
2. **A dependency-injection refactor of `Team21DesignReviewAgent.__init__`** (accept optional pre-built `mcp_client`/`gateway` instead of always constructing them) purely to make the rest of the class unit-testable — this is a prerequisite for the next several items, not optional polish.
3. **Once injectable**: a test that `_mcp_tool_executor` raises on a JSON-RPC error dict and returns `structuredContent` (or falls back to `result`) on success — using a fake `mcp_client.call_mcp` that returns canned responses, no real network.
4. **Once injectable**: a test that `run()` produces a `TaskRun` with exactly one `Step` per `initial_tasks` entry plus any planner-added follow-ups, using a fake `tool_executor` and a fake `gateway.call` — this is the test that would have caught the `overall_result` vs. `status` field-name bug *before* it required a live run to discover, if the fake tool executor had validated its arguments against a saved real schema fixture (see `as_client.py`'s explainer, tests-to-add item 5, for that fixture idea).
5. **A test for the LLM-call failure path** specifically — force `gateway.call` to raise, and assert `run.error`, `run.ended == "error"`, and the failed `llm_call` step are all set correctly. This path is exercised in the code but has never actually been hit and tested (every real run so far has succeeded).
6. **A schema-contract test**: given the real MCP `inputSchema` for `DesignFile.get`/`DesignVersion.list`/`DesignFeedback.list`/`endpoint.designreview.release_readiness`/`DesignChecklistResult.list` (captured once as fixtures, per the idea above), validate that every `tool_args` dict this file constructs — including the planner's follow-up — actually satisfies each tool's real schema. This is the single test that would have caught *both* real bugs found during this session (the wrong generic `list`/`get` tool names, and the `status`/`overall_result` field-name mismatch) without needing to hit the live platform at all.
7. **A test resolving the `_added_followup` vs. constructor-arg inconsistency** noted in trade-offs — once `file_id` is passed via `__init__` instead of `.bind()`, add a test that a `DesignReviewPlanner` constructed without calling `.bind()` fails loudly (e.g. a required constructor arg) rather than silently using `self._file_id = None` in a tool call.
