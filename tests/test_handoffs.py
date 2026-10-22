"""
Offline tests for the dry-run hand-off: the engine's write preview,
packs/designreview/handoffs.py, the schedule, and the verifiers
handoffs_match_platform / schedule_matches_db / answer_states_handoffs.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  Engine (domain-free)
  [x] A write action with a preview, in a dry run, ends HANDED_OFF with
      {"would_file": <preview>, "filed": False} and the action itself is never called
  [x] An empty preview ends RESOLVED ("nothing to file"); no preview -> DECLINED; a crashing preview -> FAILED
  [ ] TODO: With dry_run=False the action runs and the preview doesn't
  [ ] TODO: record_steps keeps a HANDED_OFF node's data (would_file) in the step
  Topics and matching
  [ ] TODO: handoffs.toml validates; an unknown key or empty keyword list is a ConfigError
  [x] Whole-word matching: "cracking risk" -> manufacturing, "PPAP level 3" -> quality,
      "pressure" does NOT match "press", "weldment" does NOT match "weld"
  [x] Open serious items = gate blockers + open high/critical feedback; resolved or low items are left out;
      a blocker that is also a feedback row is counted once
  [x] One hand-off per owner, citing every matching item; design-owned items (GD&T, wall thickness) give none
  [ ] TODO: The escalation a hand-off would file is AgentEscalation.create with reason_code needs_another_app
  [ ] TODO: past_due comes from the date: due before today and not completed/skipped (status "in_progress" can be late)
  Finding, claims, template
  [ ] TODO: A plan without a revisions node gives a finding with no revision_delta section
  [ ] TODO: The template answer for a hand-off finding (and for a no-hand-off finding) passes its own claim audit
  [ ] TODO: The audit flags "I have escalated this to manufacturing" but not "would be escalated" or
      "Nothing has been escalated"; flags "on track" while a milestone is past due
  [ ] TODO: (2026-10-15) "No hand-offs have been filed." and "No escalation was raised." are denials: neither the
      claim audit (claims.handoff_contradictions) nor the grader (answer_states_handoffs) flags them;
      "I filed the escalation; no further action is needed." is still a filing claim for both
  Verifiers (build RunBundle by hand)
  [ ] TODO: handoffs_match_platform: pass on a correct finding; fail on an invented owner, a missing or closed
      citation, filed=True in a dry run, or an escalation tool in the journal; drift when an item closed mid-run
  [ ] TODO: schedule_matches_db: fail when a late milestone is hidden or a date moved; drift when it moved mid-run
  [ ] TODO: answer_states_handoffs: fail when an owner isn't named, "proposed" only appears in the ask text,
      the answer claims it filed, a late milestone isn't named, or it says "on track"
  Write mode (use a fake platform that implements AgentSession.create, escalations.raise/update,
  escalations.assignees, AgentEscalation.list/get -- never the live one)
  [ ] TODO: A write run files one escalation per owner, assigned to the configured name, and records ESC numbers
  [ ] TODO: The batch captures the "after" ground truth BEFORE clean-up withdraws them; cleanup.json lists them
  [ ] TODO: A second run doesn't file again (already_open); a tenant with no assignee, or a name that isn't
      assignable, declines and files nothing; a refused raise closes its session and the node is FAILED
  [ ] TODO: cleanup_escalations without a run folder withdraws only open "T21-DR " escalations; dry_run changes nothing
  [ ] TODO: handoffs_match_platform fails a write run that declined although the person IS assignable
  End to end (captured fixtures)
  [ ] TODO: t08 passes offline: file_handoffs is HANDED_OFF, added by the planner, and no escalation tool was called
  [ ] TODO: t09 passes offline with no hand-off and three past-due milestones; file_handoffs never added
  [ ] TODO: t08 with fail_always:DesignMilestone.list -> fail; with drop_tool:DesignFeedback.list -> fail
      (the quality item is unseen); with an edit_text rename of the late milestone -> drift
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: the same open item cited by two feedback rows for one owner gives one citation, not two
  [ ] TODO: a milestone due TODAY (frozen clock) is not past due; due yesterday is
  [ ] TODO: the hand-off finding is the same for two runs on the same data (owner order is stable)
Hint: RunBundle(run_dir=Path("."), task={}, graph=None, journal=[], taskrun={"finding": ..., "claimed_answer": ...,
"dry_run": True}, before=..., after=...) with observations under "release_gate:<file_id>", "feedback:<file_id>",
"milestones:<project_id>".
"""

import pytest

from agentkit.graph import DECLINED, FAILED, HANDED_OFF, RESOLVED, LiveGraphExecutor, RunEnv, TaskSpec
from agentkit.harness.checks import RunBundle
from agentkit.registry import Registry
from packs.designreview import checks
from packs.designreview.handoffs import load_handoff_table, open_serious_items, propose_handoffs

TABLE = load_handoff_table()


def _run_write(preview, dry_run=True):
    reg, calls = Registry(), []

    @reg.action("file_it", writes=True, preview=preview)
    def file_it(env, args, results):
        calls.append(args)
        return {"filed": True}

    store = LiveGraphExecutor(RunEnv(client=None, dry_run=dry_run), reg).run(
        "r", [TaskSpec(id="w", action="file_it", args={"x": 1})]
    )
    return store.results()["w"], calls


def test_dry_run_write_with_a_preview_is_handed_off_and_never_called():
    would = [{"tool": "Thing.create", "args": {"x": 1}}]
    result, calls = _run_write(lambda env, args, results: would)
    assert result.verdict == HANDED_OFF
    assert result.data == {"would_file": would, "filed": False}
    assert calls == []


def _boom(env, args, results):
    raise RuntimeError("preview crashed")


@pytest.mark.parametrize("preview, verdict", [
    (lambda env, args, results: [], RESOLVED),   # nothing to file
    (None, DECLINED),                            # no preview at all
    (_boom, FAILED),                              # a crashing preview
])
def test_empty_missing_and_crashing_previews(preview, verdict):
    result, calls = _run_write(preview)
    assert result.verdict == verdict
    assert calls == []


def _owners(title):
    return [t.owner for t in TABLE.topics if t.matches(title)]


def test_keywords_match_whole_words_only():
    assert _owners("Cracking risk at the flange bend") == ["manufacturing"]
    assert _owners("PPAP level 3 submission missing") == ["quality"]
    assert _owners("Hydraulic pressure test overdue") == []   # "pressure" is not "press"
    assert _owners("Weldment drawing update") == []           # "weldment" is not "weld"


def test_open_serious_items_join_blockers_and_open_high_feedback_once():
    gate = {"blockers": [{"id": "b1", "title": "Cracking risk at bend", "priority": "critical", "status": "open"}]}
    feedback = {"data": [
        {"id": "b1", "title": "Cracking risk at bend", "priority": "critical", "status": "open"},  # also a blocker
        {"id": "f2", "title": "PPAP level 3 missing", "priority": "high", "status": "open"},
        {"id": "f3", "title": "Tooling quote", "priority": "high", "status": "resolved"},           # closed
        {"id": "f4", "title": "Label font", "priority": "low", "status": "open"},                   # not serious
    ]}
    items = open_serious_items(gate, feedback)
    assert [(i["id"], i["source"]) for i in items] == [("b1", "release_blocker"), ("f2", "feedback")]
    by_owner = {h["owner"]: [c["id"] for c in h["cites"]] for h in propose_handoffs(items, TABLE)}
    assert by_owner == {"manufacturing": ["b1"], "quality": ["f2"]}

