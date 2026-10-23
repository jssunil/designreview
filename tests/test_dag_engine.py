"""
Offline tests for agentkit/graph (LiveGraphExecutor, GraphStore, GraphPatch, RulePlanner).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] Nodes with no dependencies all run; results() is keyed by node id
  [ ] TODO: A node with requires="resolved" runs only after every dependency RESOLVED
  [x] A DECLINED or FAILED dependency leaves its dependants BYPASSED (with the reason), never resolved
  [x] requires="settled" still runs when one dependency failed (partial join)
  [x] A platform "not found" (SeatCallFailure kind MISSING) ends the node DECLINED, not FAILED
  [ ] TODO: A FLAKY error is retried up to the action's `retries`; a write action is never retried
  [x] A write action in a dry run is never called: DECLINED without a preview; HANDED_OFF with
      data {"would_file": <preview>, "filed": False} with one; RESOLVED when the preview is empty
  [x] A preview on a read action is rejected at registration
  [x] A patch naming an action that is not registered is rejected (raw tool names can't be planned)
  [x] A dependency cycle is rejected and the graph is left exactly as it was
  [x] The node budget (max_nodes) is enforced
  [x] The rev_diff_dfm rule adds failed_checklists when release_gate has blockers/reason codes, and the
      fallback rule adds it when release_gate FAILED; a clear gate adds nothing
  [ ] TODO: That rule fires at most once
  [ ] TODO: A checkpoint is written and a run can resume from it (mid-flight nodes run again)
Since 2026-10-13 (agentkit/graph/policies.py, channels.py, checkpoint.py):
  [ ] TODO: RetryPolicy: waits are min(max_interval, initial * backoff**(n-1)); max_attempts includes the first try;
      retries=2 still waits backoff, 2*backoff; a write action gets one attempt whatever its policy
  [ ] TODO: superstep mode: siblings in one step never see each other's results; a Topic channel written by three
      siblings that sleep random times always ends in node-id order
  [ ] TODO: two siblings writing one LastValue channel in the same superstep raise ChannelConflict
  [ ] TODO: Barrier(["a", "b"]) has no value until both names arrive
  [ ] TODO: a long chain in superstep mode with max_steps too small raises StepLimitExceeded
  [ ] TODO: an action calling interrupt() pauses its node (no verdict), dependants wait, the rest finish;
      store.resume(node, answer) + continue_from() and interrupt() returns the answer
  [ ] TODO: approve_writes=True: a real write pauses with its preview; anything but {"approved": True} -> DECLINED
  [ ] TODO: with a MemorySaver: one "input" checkpoint, then one "loop" checkpoint per outcome, each pointing at its parent
  [ ] TODO: fork({node: new_result}): downstream plan nodes go back to pending, planner-added nodes disappear,
      the original store is unchanged
Since 2026-10-14 / 2026-10-15:
  [ ] TODO: @registry.action(name, timeout_s=0.1) on an action that sleeps 1 s -> the node ends FAILED with
      error_kind "timeout"; a sibling branch still finishes
  [ ] TODO: a rule with verdict = "failed" and no data condition is valid; the same rule with verdict "resolved"
      and no condition is rejected; verdict = "exploded" is rejected
  [ ] TODO: a fallback node with requires = "settled" runs after its dependency FAILED (with "resolved" it would
      be bypassed)
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: the same plan run twice with the same fake client gives the same verdict and data for every node
  [ ] TODO: an action that raises a plain Exception ends its node FAILED with the error text; independent
      siblings still finish and results() has every node
  [ ] TODO: a node that is BYPASSED is never passed to its action (the fake action records no call)
Hint: build a Registry with small fake actions, and use RunEnv(client=<fake with invoke_tool>),
so no MCP is needed. Plans: agentkit.graph.load_plan("packs/designreview/plans/rev_diff_dfm.toml").
"""

from pathlib import Path
import pytest

from agentkit.graph import (
    BYPASSED,
    DECLINED,
    FAILED,
    HANDED_OFF,
    RESOLVED,
    Barrier,
    ChannelConflict,
    GraphPatch,
    GraphStore,
    LastValue,
    LiveGraphExecutor,
    MemorySaver,
    NodeResult,
    RetryPolicy,
    RulePlanner,
    RunEnv,
    StepLimitExceeded,
    TaskSpec,
    Topic,
    declined,
    interrupt,
    load_plan,
)
from agentkit.registry import Registry, load_pack
from agentkit.transport import FLAKY, MISSING, SeatCallFailure

# Write your tests below.

# ==============================================================================
# Use cases to cover (Original)
# ==============================================================================


def test_nodes_with_no_dependencies_all_run():
    """Use case: Nodes with no dependencies all run; results() is keyed by node id."""
    reg = Registry()

    @reg.action("action_a")
    def action_a(env, args, results):
        return {"node": "a"}

    @reg.action("action_b")
    def action_b(env, args, results):
        return {"node": "b"}

    @reg.action("action_c")
    def action_c(env, args, results):
        return {"node": "c"}

    nodes = [
        TaskSpec(id="a", action="action_a"),
        TaskSpec(id="b", action="action_b"),
        TaskSpec(id="c", action="action_c"),
    ]
    env = RunEnv(client=None)
    store = LiveGraphExecutor(env, reg).run("test-no-deps", nodes)
    res = store.results()

    assert set(res.keys()) == {"a", "b", "c"}
    for node_id in ("a", "b", "c"):
        assert res[node_id].verdict == RESOLVED
    assert res["a"].data == {"node": "a"}
    assert res["b"].data == {"node": "b"}
    assert res["c"].data == {"node": "c"}


def test_requires_resolved_runs_only_after_dependencies_resolved():
    """Use case: A node with requires="resolved" runs only after every dependency RESOLVED."""
    pytest.skip("Not yet implemented")


@pytest.mark.parametrize("fail_mode", ["declined", "failed"])
def test_declined_or_failed_leaves_dependants_bypassed(fail_mode):
    """Use case: A DECLINED or FAILED dependency leaves its dependants BYPASSED (with the reason), never resolved."""
    reg = Registry()

    @reg.action("root_action")
    def root_action(env, args, results):
        if fail_mode == "declined":
            return declined("root declined for test")
        raise RuntimeError("root failed for test")

    @reg.action("leaf_action")
    def leaf_action(env, args, results):
        return {"node": "leaf"}

    nodes = [
        TaskSpec(id="root", action="root_action"),
        TaskSpec(id="leaf", action="leaf_action", after=["root"]),
    ]
    env = RunEnv(client=None)
    store = LiveGraphExecutor(env, reg).run(f"test-bypass-{fail_mode}", nodes)
    res = store.results()

    expected_root_verdict = DECLINED if fail_mode == "declined" else FAILED
    assert res["root"].verdict == expected_root_verdict
    assert res["leaf"].verdict == BYPASSED
    assert "root" in res["leaf"].reason
    
    


def test_requires_settled_runs_when_dependency_failed():
    """Use case: requires="settled" still runs when one dependency failed (partial join)."""
    reg = Registry()

    @reg.action("root1_action")
    def root1_action(env, args, results):
        raise RuntimeError("root1 failed for test")

    @reg.action("root2_action")
    def root2_action(env, args, results):
        return {"node": "root2"}

    @reg.action("leaf_action")
    def leaf_action(env, args, results):
        return {"node": "leaf"}

    nodes = [
        TaskSpec(id="root1", action="root1_action"),
        TaskSpec(id="root2", action="root2_action"),
        TaskSpec(id="leaf", action="leaf_action", after=["root1", "root2"], requires="settled"),
    ]
    env = RunEnv(client=None)
    store = LiveGraphExecutor(env, reg).run("test-settled", nodes)
    res = store.results()

    assert res["root1"].verdict == FAILED
    assert res["root2"].verdict == RESOLVED
    assert res["leaf"].verdict == RESOLVED   # settled runs even if one dependency failed
    assert res["leaf"].data == {"node": "leaf"}


def test_platform_not_found_ends_node_declined():
    """Use case: A platform "not found" (SeatCallFailure kind MISSING) ends the node DECLINED, not FAILED."""
    reg = Registry()

    @reg.action("root_action")
    def root_action(env, args, results):
        raise SeatCallFailure("platform not found", kind=MISSING)

    @reg.action("leaf_action")
    def leaf_action(env, args, results):
        return {"node": "leaf"}

    nodes = [
        TaskSpec(id="root", action="root_action"),
        TaskSpec(id="leaf", action="leaf_action", after=["root"]),
    ]
    env = RunEnv(client=None)
    store = LiveGraphExecutor(env, reg).run("test-missing-platform", nodes)
    res = store.results()

    assert res["root"].verdict == DECLINED
    assert "platform not found" in res["root"].reason
    assert res["leaf"].verdict == BYPASSED
    assert "root" in res["leaf"].reason


def test_flaky_error_retried_write_never_retried():
    """Use case: A FLAKY error is retried up to the action's `retries`; a write action is never retried."""
    pytest.skip("Not yet implemented")


def test_write_action_in_dry_run_modes():
    """Use case: A write action in a dry run is never called: DECLINED without a preview;
    HANDED_OFF with data {"would_file": <preview>, "filed": False} with one;
    RESOLVED when the preview is empty."""
    calls = []
    reg = Registry()

    @reg.action("w_no_preview", writes=True)
    def w_no_preview(env, args, results):
        calls.append("w_no_preview")
        return {"written": True}

    @reg.action(
        "w_preview",
        writes=True,
        preview=lambda env, args, results: [{"tool": "Ticket.create", "title": "x"}],
    )
    def w_preview(env, args, results):
        calls.append("w_preview")
        return {"written": True}

    @reg.action(
        "w_empty_preview",
        writes=True,
        preview=lambda env, args, results: [],
    )
    def w_empty_preview(env, args, results):
        calls.append("w_empty_preview")
        return {"written": True}

    nodes = [
        TaskSpec(id="node_no_preview", action="w_no_preview"),
        TaskSpec(id="node_preview", action="w_preview"),
        TaskSpec(id="node_empty_preview", action="w_empty_preview"),
    ]
    env = RunEnv(client=None, dry_run=True)
    store = LiveGraphExecutor(env, reg).run("test-dry-run", nodes)
    res = store.results()

    assert calls == []
    assert res["node_no_preview"].verdict == DECLINED
    assert "dry run" in res["node_no_preview"].reason

    assert res["node_preview"].verdict == HANDED_OFF
    assert res["node_preview"].data == {
        "would_file": [{"tool": "Ticket.create", "title": "x"}],
        "filed": False,
    }

    assert res["node_empty_preview"].verdict == RESOLVED
    assert res["node_empty_preview"].data == {"would_file": [], "filed": False}

    # Contrast: with dry_run=False, the write action executes for real
    reg_live = Registry()
    live_calls = []

    @reg_live.action(
        "w_preview",
        writes=True,
        preview=lambda env, args, results: [{"tool": "Ticket.create", "title": "x"}],
    )
    def w_preview_live(env, args, results):
        live_calls.append("w_preview_live")
        return {"written": True}

    env_live = RunEnv(client=None, dry_run=False)
    store_live = LiveGraphExecutor(env_live, reg_live).run(
        "test-live-run", [TaskSpec(id="node_live", action="w_preview")]
    )
    res_live = store_live.results()

    assert live_calls == ["w_preview_live"]
    assert res_live["node_live"].verdict == RESOLVED
    assert res_live["node_live"].data == {"written": True}


def test_preview_on_read_action_rejected():
    """Use case: A preview on a read action is rejected at registration."""
    reg = Registry()

    def dummy_preview(env, args):
        return {"preview": "data"}

    with pytest.raises(ValueError, match="only for write actions"):
        @reg.action("read_x", preview=dummy_preview)
        def read_x(env, args, results):
            return {"read": True}

    assert "read_x" not in reg.actions

    # Contrast: registering a write action with preview succeeds
    @reg.action("write_x", writes=True, preview=dummy_preview)
    def write_x(env, args, results):
        return {"written": True}

    assert "write_x" in reg.actions


def test_patch_naming_unregistered_action_rejected():
    """Use case: A patch naming an action that is not registered is rejected (raw tool names can't be planned)."""
    reg = Registry()
    store = GraphStore("t", reg)

    with pytest.raises(ValueError, match="is not a registered action"):
        store.apply(GraphPatch(add=[TaskSpec(id="node1", action="unregistered_tool")]))

    assert "node1" not in store


def test_dependency_cycle_rejected_graph_preserved():
    """Use case: A dependency cycle is rejected and the graph is left exactly as it was."""
    reg = Registry()

    @reg.action("act_a")
    def act_a(env, args, results):
        return {}

    @reg.action("act_b")
    def act_b(env, args, results):
        return {}

    @reg.action("act_c")
    def act_c(env, args, results):
        return {}

    store = GraphStore("t", reg)
    store.apply(GraphPatch(add=[
        TaskSpec(id="a", action="act_a"),
        TaskSpec(id="b", action="act_b", after=["a"]),
    ]))

    saved_specs = list(store.specs.keys())
    saved_after = {node_id: list(spec.after) for node_id, spec in store.specs.items()}

    with pytest.raises(ValueError, match="patch introduces a dependency cycle"):
        store.apply(GraphPatch(
            add=[TaskSpec(id="c", action="act_c")],
            link=[("b", "a")],
        ))

    assert list(store.specs.keys()) == saved_specs
    assert {node_id: list(spec.after) for node_id, spec in store.specs.items()} == saved_after
    assert "c" not in store


def test_node_budget_max_nodes_enforced():
    """Use case: The node budget (max_nodes) is enforced."""
    reg = Registry()

    @reg.action("act")
    def act(env, args, results):
        return {}

    store = GraphStore("t", reg, max_nodes=2)
    store.apply(GraphPatch(add=[
        TaskSpec(id="n1", action="act"),
        TaskSpec(id="n2", action="act"),
    ]))
    assert len(store.specs) == 2

    with pytest.raises(ValueError, match="would exceed the 2-node budget"):
        store.apply(GraphPatch(add=[TaskSpec(id="n3", action="act")]))

    assert "n3" not in store


def test_rev_diff_dfm_rule_adds_failed_checklists():
    """Use case: The rev_diff_dfm rule adds failed_checklists when release_gate has blockers/reason codes,
    and the fallback rule adds it when release_gate FAILED; a clear gate adds nothing."""
    plan_path = Path(__file__).resolve().parents[1] / "packs" / "designreview" / "plans" / "rev_diff_dfm.toml"
    plan = load_plan(plan_path)

    # 1. release_gate has blockers -> rule adds failed_checklists
    planner = RulePlanner(plan.rules, {"file_id": "file-123"})
    spec = TaskSpec(id="release_gate", action="read_release_gate")
    spec.result = NodeResult(RESOLVED, data={"result": {"blockers": ["wall thickness too thin"], "reason_codes": []}})
    patch = planner.on_outcome(None, spec)
    assert patch is not None
    assert len(patch.add) == 1
    assert patch.add[0].id == "failed_checklists"
    assert patch.add[0].action == "list_failed_checklists"
    assert patch.add[0].args == {"file_id": "file-123"}
    assert patch.add[0].after == ["release_gate"]

    # 2. release_gate has reason codes -> rule adds failed_checklists
    planner = RulePlanner(plan.rules, {"file_id": "file-123"})
    spec = TaskSpec(id="release_gate", action="read_release_gate")
    spec.result = NodeResult(RESOLVED, data={"result": {"blockers": [], "reason_codes": ["RC-01"]}})
    patch = planner.on_outcome(None, spec)
    assert patch is not None
    assert patch.add[0].id == "failed_checklists"

    # 3. release_gate FAILED -> fallback rule adds failed_checklists with requires="settled"
    planner = RulePlanner(plan.rules, {"file_id": "file-123"})
    spec = TaskSpec(id="release_gate", action="read_release_gate")
    spec.result = NodeResult(FAILED, reason="Gate service unavailable")
    patch = planner.on_outcome(None, spec)
    assert patch is not None
    assert len(patch.add) == 1
    assert patch.add[0].id == "failed_checklists"
    assert patch.add[0].requires == "settled"
    assert patch.add[0].after == ["release_gate"]

    # 4. Clear gate -> adds nothing
    planner = RulePlanner(plan.rules, {"file_id": "file-123"})
    spec = TaskSpec(id="release_gate", action="read_release_gate")
    spec.result = NodeResult(RESOLVED, data={"result": {"blockers": [], "reason_codes": []}})
    patch = planner.on_outcome(None, spec)
    assert patch is None


def test_rule_fires_at_most_once():
    """Use case: That rule fires at most once."""
    pytest.skip("Not yet implemented")


def test_checkpoint_written_and_resume_midflight():
    """Use case: A checkpoint is written and a run can resume from it (mid-flight nodes run again)."""
    pytest.skip("Not yet implemented")


# ==============================================================================
# Since 2026-10-13 (policies, channels, checkpoint)
# ==============================================================================


def test_retry_policy_exponential_backoff_and_write_single_attempt():
    """Use case: RetryPolicy: waits are min(max_interval, initial * backoff**(n-1));
    max_attempts includes the first try; retries=2 still waits backoff, 2*backoff;
    a write action gets one attempt whatever its policy."""
    pytest.skip("Not yet implemented")


def test_superstep_mode_isolation_and_topic_channel_ordering():
    """Use case: superstep mode: siblings in one step never see each other's results;
    a Topic channel written by three siblings that sleep random times always ends in node-id order."""
    pytest.skip("Not yet implemented")


def test_last_value_channel_conflict_same_superstep():
    """Use case: two siblings writing one LastValue channel in the same superstep raise ChannelConflict."""
    pytest.skip("Not yet implemented")


def test_barrier_channel_waits_for_all_names():
    """Use case: Barrier(["a", "b"]) has no value until both names arrive."""
    pytest.skip("Not yet implemented")


def test_superstep_step_limit_exceeded():
    """Use case: a long chain in superstep mode with max_steps too small raises StepLimitExceeded."""
    pytest.skip("Not yet implemented")


def test_action_interrupt_pauses_and_resumes():
    """Use case: an action calling interrupt() pauses its node (no verdict), dependants wait, the rest finish;
    store.resume(node, answer) + continue_from() and interrupt() returns the answer."""
    pytest.skip("Not yet implemented")


def test_approve_writes_pauses_with_preview():
    """Use case: approve_writes=True: a real write pauses with its preview; anything but {"approved": True} -> DECLINED."""
    pytest.skip("Not yet implemented")


def test_memory_saver_checkpoint_chain():
    """Use case: with a MemorySaver: one "input" checkpoint, then one "loop" checkpoint per outcome,
    each pointing at its parent."""
    pytest.skip("Not yet implemented")


def test_store_fork_resets_downstream_and_preserves_original():
    """Use case: fork({node: new_result}): downstream plan nodes go back to pending,
    planner-added nodes disappear, the original store is unchanged."""
    pytest.skip("Not yet implemented")


# ==============================================================================
# Since 2026-10-14 / 2026-10-15 (timeouts, rule validation, settled requires)
# ==============================================================================


def test_action_timeout_ends_node_failed():
    """Use case: @registry.action(name, timeout_s=0.1) on an action that sleeps 1 s ->
    the node ends FAILED with error_kind "timeout"; a sibling branch still finishes."""
    pytest.skip("Not yet implemented")


def test_rule_verdict_validation():
    """Use case: a rule with verdict = "failed" and no data condition is valid;
    the same rule with verdict "resolved" and no condition is rejected;
    verdict = "exploded" is rejected."""
    pytest.skip("Not yet implemented")


def test_fallback_node_with_requires_settled_runs_after_failed_dependency():
    """Use case: a fallback node with requires = "settled" runs after its dependency FAILED
    (with "resolved" it would be bypassed)."""
    pytest.skip("Not yet implemented")


# ==============================================================================
# More use cases (added 2026-10-16)
# ==============================================================================


def test_plan_run_twice_with_fake_client_is_deterministic():
    """Use case: the same plan run twice with the same fake client gives the same verdict and data for every node."""
    pytest.skip("Not yet implemented")


def test_action_plain_exception_ends_node_failed_sibling_finishes():
    """Use case: an action that raises a plain Exception ends its node FAILED with the error text;
    independent siblings still finish and results() has every node."""
    pytest.skip("Not yet implemented")


def test_bypassed_node_action_is_never_called():
    """Use case: a node that is BYPASSED is never passed to its action (the fake action records no call)."""
    pytest.skip("Not yet implemented")

