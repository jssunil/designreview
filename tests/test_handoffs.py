"""
Offline tests for the dry-run hand-off: the engine's write preview,
packs/designreview/handoffs.py, the schedule, and the verifiers
handoffs_match_platform / schedule_matches_db / answer_states_handoffs.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  Engine (domain-free)
  [ ] A write action with a preview, in a dry run, ends HANDED_OFF with
      {"would_file": <preview>, "filed": False} and the action itself is never called
  [ ] An empty preview ends RESOLVED ("nothing to file"); no preview -> DECLINED; a crashing preview -> FAILED
  [ ] With dry_run=False the action runs and the preview doesn't
  [ ] record_steps keeps a HANDED_OFF node's data (would_file) in the step
  Topics and matching
  [ ] handoffs.toml validates; an unknown key or empty keyword list is a ConfigError
  [ ] Whole-word matching: "cracking risk" -> manufacturing, "PPAP level 3" -> quality,
      "pressure" does NOT match "press", "weldment" does NOT match "weld"
  [ ] Open serious items = gate blockers + open high/critical feedback; resolved or low items are left out;
      a blocker that is also a feedback row is counted once
  [ ] One hand-off per owner, citing every matching item; design-owned items (GD&T, wall thickness) give none
  [ ] The escalation a hand-off would file is AgentEscalation.create with reason_code needs_another_app
  [ ] past_due comes from the date: due before today and not completed/skipped (status "in_progress" can be late)
  Finding, claims, template
  [ ] A plan without a revisions node gives a finding with no revision_delta section
  [ ] The template answer for a hand-off finding (and for a no-hand-off finding) passes its own claim audit
  [ ] The audit flags "I have escalated this to manufacturing" but not "would be escalated" or
      "Nothing has been escalated"; flags "on track" while a milestone is past due
  Verifiers (build RunBundle by hand)
  [ ] handoffs_match_platform: pass on a correct finding; fail on an invented owner, a missing or closed
      citation, filed=True in a dry run, or an escalation tool in the journal; drift when an item closed mid-run
  [ ] schedule_matches_db: fail when a late milestone is hidden or a date moved; drift when it moved mid-run
  [ ] answer_states_handoffs: fail when an owner isn't named, "proposed" only appears in the ask text,
      the answer claims it filed, a late milestone isn't named, or it says "on track"
  Write mode (use a fake platform that implements AgentSession.create, escalations.raise/update,
  escalations.assignees, AgentEscalation.list/get -- never the live one)
  [ ] A write run files one escalation per owner, assigned to the configured name, and records ESC numbers
  [ ] The batch captures the "after" ground truth BEFORE clean-up withdraws them; cleanup.json lists them
  [ ] A second run doesn't file again (already_open); a tenant with no assignee, or a name that isn't
      assignable, declines and files nothing; a refused raise closes its session and the node is FAILED
  [ ] cleanup_escalations without a run folder withdraws only open "T21-DR " escalations; dry_run changes nothing
  [ ] handoffs_match_platform fails a write run that declined although the person IS assignable
  End to end (captured fixtures)
  [ ] t08 passes offline: file_handoffs is HANDED_OFF, added by the planner, and no escalation tool was called
  [ ] t09 passes offline with no hand-off and three past-due milestones; file_handoffs never added
  [ ] t08 with fail_always:DesignMilestone.list -> fail; with drop_tool:DesignFeedback.list -> fail
      (the quality item is unseen); with an edit_text rename of the late milestone -> drift
Hint: RunBundle(run_dir=Path("."), task={}, graph=None, journal=[], taskrun={"finding": ..., "claimed_answer": ...,
"dry_run": True}, before=..., after=...) with observations under "release_gate:<file_id>", "feedback:<file_id>",
"milestones:<project_id>".
"""

import pytest

from agentkit.graph import HANDED_OFF, LiveGraphExecutor, RunEnv, TaskSpec
from agentkit.harness.checks import RunBundle
from agentkit.registry import Registry
from packs.designreview import checks
from packs.designreview.handoffs import load_handoff_table, open_serious_items, propose_handoffs

# Write your tests below.
