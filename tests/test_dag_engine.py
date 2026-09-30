"""
Offline tests for agentkit/graph (LiveGraphExecutor, GraphStore, GraphPatch, RulePlanner).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] Nodes with no dependencies all run; results() is keyed by node id
  [ ] A node with requires="resolved" runs only after every dependency RESOLVED
  [ ] A DECLINED or FAILED dependency leaves its dependants BYPASSED (with the reason), never resolved
  [ ] requires="settled" still runs when one dependency failed (partial join)
  [ ] A platform "not found" (SeatCallFailure kind MISSING) ends the node DECLINED, not FAILED
  [ ] A FLAKY error is retried up to the action's `retries`; a write action is never retried
  [ ] A write action in a dry run is never called: DECLINED without a preview; HANDED_OFF with
      data {"would_file": <preview>, "filed": False} with one; RESOLVED when the preview is empty
  [ ] A preview on a read action is rejected at registration
  [ ] A patch naming an action that is not registered is rejected (raw tool names can't be planned)
  [ ] A dependency cycle is rejected and the graph is left exactly as it was
  [ ] The node budget (max_nodes) is enforced
  [ ] The rev_diff_dfm rule adds failed_checklists ONLY when release_gate has blockers/reason codes
  [ ] That rule fires at most once
  [ ] A checkpoint is written and a run can resume from it (mid-flight nodes run again)
Hint: build a Registry with small fake actions, and use RunEnv(client=<fake with invoke_tool>),
so no MCP is needed. Plans: agentkit.graph.load_plan("packs/designreview/plans/rev_diff_dfm.toml").
"""

import pytest

from agentkit.graph import GraphPatch, GraphStore, LiveGraphExecutor, RulePlanner, RunEnv, TaskSpec, load_plan
from agentkit.registry import Registry, load_pack

# Write your tests below.
