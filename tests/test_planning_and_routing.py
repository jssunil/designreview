"""
Offline tests for choosing what to do: from the question to a plan and a part, the guard on any
planner's patches, fallback rules when a node fails, and checking tool arguments before a call
(agentkit/graph/*, agentkit/transport/*, packs/designreview/plans/*).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/RESEARCH_GAP_ANALYSIS.md, gaps G1, I6, I7, I9.
Items marked (not built yet) test a feature that does not exist today: write the test, and mark it
@pytest.mark.xfail(strict=True, reason="G#/I#") (or skip) until the feature lands.

Use cases to cover:
  Works today (the guard every planner must pass):
  [ ] TODO: a GraphPatch naming an action the pack did not register -> ValueError, graph unchanged
  [ ] TODO: a patch that would create a cycle, or pass the node budget -> ValueError, graph unchanged
  [ ] TODO: the rev_diff_dfm rule adds failed_checklists only when the gate reports blockers

  Question -> plan and part (G1, I6a, built 2026-10-14: agentkit.agents.router.RuleRouter,
  packs/designreview/router.py and routes.toml; the LLM-planner line is still not built):
  [x] "Run the platform's AI design review on the Battery Tray" -> plan tool_refusal
  [x] "What changed between rev B and rev C on the Battery Tray?" -> plan rev_diff_dfm
  [ ] TODO: "battery tray" resolves to the Battery Tray file id on the Suryodaya fixture
  [ ] TODO: a name that matches two files -> decline (or ask), never a guess
  [x] "the propeller" on the Suryodaya fixture -> declined, and the reason names the four candidate files
  [x] a question with a UUID in it uses that id and reads no file list
  [ ] TODO: department words that many file names share ("Quality", "Despatch") are not evidence of a match
  [ ] TODO: a task file with only a prompt (no plan, no file_id) runs and grades like t01
  [ ] TODO: (not built yet) an LLM planner's patch is checked exactly like a rule's: a raw tool name is refused

  Fallback rules (I7, built 2026-10-15: WhenModel verdict = "failed"; rev_diff_dfm rule
  failed_checklists_when_gate_unreadable):
  [ ] TODO: fail_always on the release gate -> the plan's fallback rule adds failed_checklists (added_by "planner",
      requires "settled"), and the node resolves
  [ ] TODO: the run still grades "fail" on release_gate_matches_db and on the golden release_gate.ready: nothing
      takes the place of the gate's verdict

  Argument checks (I9, built 2026-10-14: transport.journal.argument_problems, JournaledSeatClient):
  [x] a call missing a required argument (per the tool's inputSchema) is refused before it is sent,
      and the journal records no send
  [x] a call with all required arguments goes through unchanged

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: a question that matches no route -> route_declined with a reason, never a default plan
  [ ] TODO: Route.matches is case-insensitive: "WHAT CHANGED between rev B and rev C" matches like the lower-case text
  [ ] TODO: RouteDecision.declined is True only for the decline plan
Hint: GraphStore + LiveGraphExecutor run a plan with any object that has invoke_tool(name, args) and
offers(name); a SimPlatform on packs/designreview/fixtures/suryodaya.json is one. ToolCatalog gives each
tool's inputSchema and required arguments.
"""

import json
from pathlib import Path

import pytest

from agentkit.graph import GraphPatch, GraphStore, LiveGraphExecutor, RulePlanner, RunEnv, load_plan
from agentkit.registry import load_pack
from agentkit.sim import SimPlatform
from agentkit.transport import BAD_ARGS, SeatCallFailure
from agentkit.transport.catalog import ToolCatalog
from agentkit.transport.journal import JournaledSeatClient

ROOT = Path(__file__).resolve().parents[1]
SURYODAYA = ROOT / "packs" / "designreview" / "fixtures" / "suryodaya.json"
ROUTER = load_pack("packs.designreview").extras["router"]
BATTERY_TRAY = "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"


@pytest.mark.parametrize("question, plan", [
    ("Run the platform's AI design review on the Battery Tray", "tool_refusal"),
    ("What changed between rev B and rev C on the Battery Tray?", "rev_diff_dfm"),
])
def test_question_routes_to_its_plan_and_the_battery_tray(question, plan):
    decision = ROUTER.route(question, SimPlatform(SURYODAYA))
    assert decision.plan == plan
    assert decision.params["file_id"] == BATTERY_TRAY


def test_a_name_that_matches_several_files_is_declined_with_the_candidates():
    decision = ROUTER.route("Is the propeller ready for release?", SimPlatform(SURYODAYA))
    assert decision.declined
    reason = decision.params["reason"]
    assert "ambiguous" in reason
    for number in ("DF-2026-00012", "DF-2026-00013", "DF-2026-00014", "DF-2026-00015"):
        assert number in reason


def test_a_uuid_in_the_question_is_used_and_no_file_list_is_read():
    platform = SimPlatform(SURYODAYA)
    uuid = "00000000-0000-0000-0000-000000000000"
    decision = ROUTER.route(f"What changed between rev B and rev C of design file {uuid}?", platform)
    assert decision.params["file_id"] == uuid
    assert platform.calls == []


def test_a_call_missing_a_required_argument_is_refused_before_it_is_sent(tmp_path):
    platform = SimPlatform(SURYODAYA)
    client = JournaledSeatClient(platform, tmp_path / "journal.jsonl")
    with pytest.raises(SeatCallFailure) as err:
        client.invoke_tool("DesignFile.get", {"file_id": BATTERY_TRAY})   # the schema wants "id"
    assert err.value.kind == BAD_ARGS
    assert platform.calls == []                                            # never reached the platform
    entry = json.loads((tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert entry["sent"] is False

    record = client.invoke_tool("DesignFile.get", {"id": BATTERY_TRAY})   # all required args: unchanged
    assert platform.calls[-1][1:] == ("DesignFile.get", {"id": BATTERY_TRAY})
    assert record

