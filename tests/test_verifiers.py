"""
Offline tests for the design-review verifiers in packs/designreview/checks.py.    Markers: none

Author(s): Sunil Jakkaraju    Written by hand: [x] yes

Goal: prove the verifiers REJECT wrong answers, not just accept right ones.
Use cases to cover:
  [x] revision_delta_matches_db: rev B->v2, rev C->v3 passes; mapping by position (v1->v2) fails
      -> test_revision_delta_matches_db()
  [x] revision_delta_matches_db: versions that changed during the run give "drift", not "fail"
      -> test_revision_delta_drift()
  [x] answer_states_change: 2.0 / 3.0 / 4.2 mm all stated passes; "12.0 mm to 13.0 mm" fails
      -> test_answer_states_change()
  [x] answer_states_change: an invented quantity (not in any commit, blocker or feedback) fails
      -> test_answer_states_change()
  [x] release_gate_matches_db: "not ready for release" is read as NOT ready (negation handled)
      -> test_release_gate_matches_db()
  [x] release_gate_matches_db: "Nothing is blocked. It is ready for release." is read as READY
      -> test_release_gate_matches_db()
  [x] no_geometry_claim: "the geometric diff confirms ..." fails; "no geometry comparison was run" passes
      -> test_no_geometry_claim()
  [x] record_absent / tool_absent: a refusal passes; carrying on after the refusal fails
      -> test_record_absent_and_tool_absent()
  [x] Ground truth that couldn't be read gives "error" -- never "pass"
      -> test_ground_truth_unread_gives_error()
  [x] select_versions: {from_rev: "B", to_rev: "C"} maps by commit note ("Revision B" too);
      {latest: true} picks the two newest versions; a missing rev gives (None, None, [])
      -> test_select_versions()
  [x] answer_reflects_change_notes: an answer carrying the note's words passes; a generic answer
      ("Rev C changed the part") fails; a note with no content words is "skip"; a note rewritten
      during the run is "drift"
      -> test_answer_reflects_change_notes()
Hint: build a RunBundle by hand (agentkit.harness.checks.RunBundle) with `after` / `before` as
{"observations": {"versions:<file_id>": {"value": [...]}}} -- no network, no files needed.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agentkit.harness.checks import CheckOutcome, RunBundle
from packs.designreview import checks


def make_bundle(
    finding: Optional[Dict[str, Any]] = None,
    answer: str = "",
    before_versions: Optional[List[Dict[str, Any]]] = None,
    after_versions: Optional[List[Dict[str, Any]]] = None,
    file_id: str = "f1",
    before_observations: Optional[Dict[str, Any]] = None,
    after_observations: Optional[Dict[str, Any]] = None,
    journal: Optional[List[Dict[str, Any]]] = None,
    dry_run: bool = True,
) -> RunBundle:
    """Helper to assemble an in-memory RunBundle for verifier checks without disk IO or network."""
    before_obs = dict(before_observations or {})
    after_obs = dict(after_observations or {})
    if before_versions is not None:
        before_obs[f"versions:{file_id}"] = {"value": before_versions}
    if after_versions is not None:
        after_obs[f"versions:{file_id}"] = {"value": after_versions}
    return RunBundle(
        run_dir=Path("."),
        task={},
        graph=None,
        journal=journal or [],
        taskrun={"finding": finding or {}, "claimed_answer": answer, "dry_run": dry_run},
        before={"observations": before_obs},
        after={"observations": after_obs},
    )


def statuses(outcomes: List[CheckOutcome]) -> Dict[str, str]:
    """Map outcome names to their evaluation status ('pass', 'fail', 'drift', 'skip', 'error')."""
    return {o.name: o.status for o in outcomes}


# Write your tests below.

def test_select_versions():
    """
    Test select_versions() resolution logic:
    - {from_rev: "B", to_rev: "C"} maps by commit message note ("Revision B" / "Rev C").
    - {latest: true} selects the two newest versions by version_number.
    - Missing requested revisions return (None, None, []).
    """
    versions = [
        {"version_number": 1, "commit_message": "Initial release against drawing BEV-BT-2400 rev A."},
        {"version_number": 2, "commit_message": "Revision B — cell layout updated, 2.0 mm corner bend radius."},
        {"version_number": 3, "commit_message": "Rev C — corner bend radius opened from 2.0 mm to 3.0 mm."},
    ]
    # 1. Map by revision label in commit notes ("Revision B" -> v2, "Rev C" -> v3)
    f, t, notes = checks.select_versions(versions, {"from_rev": "B", "to_rev": "C"})
    assert f == 2
    assert t == 3
    assert notes == ["Rev C — corner bend radius opened from 2.0 mm to 3.0 mm."]

    # 2. {latest: true} selects the two newest versions
    f_lat, t_lat, notes_lat = checks.select_versions(versions, {"latest": True})
    assert f_lat == 2
    assert t_lat == 3
    assert notes_lat == ["Rev C — corner bend radius opened from 2.0 mm to 3.0 mm."]

    # 3. Missing revisions safely return (None, None, [])
    assert checks.select_versions(versions, {"from_rev": "B", "to_rev": "D"}) == (None, None, [])
    assert checks.select_versions(versions, {"from_rev": "X", "to_rev": "C"}) == (None, None, [])


def test_revision_delta_matches_db():
    """
    Test revision_delta_matches_db (check_revision_delta):
    - Correct mapping by commit note (rev B -> v2, rev C -> v3) passes.
    - Position-based mapping (v1 -> v2) fails when labels do not match.
    """
    versions = [
        {"version_number": 1, "commit_message": "Initial release rev A."},
        {"version_number": 2, "commit_message": "Rev B — layout updated."},
        {"version_number": 3, "commit_message": "Rev C — bend radius opened."},
    ]
    p = {"file_id": "f1", "from_rev": "B", "to_rev": "C"}

    # 1. Correct note-based mapping (rev B->v2, rev C->v3) passes
    finding_pass = {
        "revision_delta": {
            "from_version": 2,
            "to_version": 3,
            "change_notes": ["Rev C — bend radius opened."],
        }
    }
    bundle_pass = make_bundle(finding=finding_pass, before_versions=versions, after_versions=versions, file_id="f1")
    res_pass = checks.check_revision_delta(bundle_pass, p)
    assert statuses(res_pass) == {"mapping": "pass", "change_notes": "pass"}

    # 2. Position-based mapping (v1->v2) fails
    finding_fail = {
        "revision_delta": {
            "from_version": 1,
            "to_version": 2,
            "change_notes": ["Rev C — bend radius opened."],
        }
    }
    bundle_fail = make_bundle(finding=finding_fail, before_versions=versions, after_versions=versions, file_id="f1")
    res_fail = checks.check_revision_delta(bundle_fail, p)
    assert statuses(res_fail)["mapping"] == "fail"


def test_revision_delta_drift():
    """
    Test revision_delta_matches_db drift detection:
    - If version data changed on the platform during the agent run,
      the verifier should return 'drift' rather than 'fail'.
    """
    # Ground truth before the run (rev B was v1, rev C was v2)
    before_versions = [
        {"version_number": 1, "commit_message": "Rev B — layout updated."},
        {"version_number": 2, "commit_message": "Rev C — bend radius opened."},
    ]
    # Ground truth after the run (new version prepended, so rev B is now v2, rev C is v3)
    after_versions = [
        {"version_number": 1, "commit_message": "Rev A — initial release."},
        {"version_number": 2, "commit_message": "Rev B — layout updated."},
        {"version_number": 3, "commit_message": "Rev C — bend radius opened."},
    ]
    p = {"file_id": "f1", "from_rev": "B", "to_rev": "C"}

    # Agent's finding matched what existed in `before` (v1 -> v2)
    finding = {
        "revision_delta": {
            "from_version": 1,
            "to_version": 2,
            "change_notes": ["Rev C — bend radius opened."],
        }
    }
    bundle = make_bundle(finding=finding, before_versions=before_versions, after_versions=after_versions, file_id="f1")
    res = checks.check_revision_delta(bundle, p)

    # Disagreement with after_truth, but agreement with before_truth -> 'drift'
    assert statuses(res)["mapping"] == "drift"


def test_answer_states_change():
    """
    Test answer_states_change (check_answer_states_change):
    - All stated quantities (2.0 / 3.0 / 4.2 mm) from change notes passes.
    - Missing quantities ("12.0 mm to 13.0 mm") fails states_rev_quantities.
    - Invented quantities not in commits, blockers, or feedback fails no_invented_quantities.
    """
    versions = [
        {"version_number": 1, "commit_message": "Initial release rev A."},
        {"version_number": 2, "commit_message": "Rev B — 2.0 mm bend radius."},
        {"version_number": 3, "commit_message": "Rev C — opened from 2.0 mm to 3.0 mm, rib height 4.2 mm."},
    ]
    p = {"file_id": "f1", "from_rev": "B", "to_rev": "C"}

    # 1. Correct quantities present: 2.0, 3.0, and 4.2 mm all stated passes
    answer_pass = "The corner bend radius changed from 2.0 mm to 3.0 mm with rib height 4.2 mm."
    bundle_pass = make_bundle(answer=answer_pass, after_versions=versions, file_id="f1")
    res_pass = checks.check_answer_states_change(bundle_pass, p)
    assert statuses(res_pass)["states_rev_quantities"] == "pass"
    assert statuses(res_pass)["no_invented_quantities"] == "pass"

    # 2. Missing ground-truth quantities (inventing 12.0 / 13.0 mm instead) fails
    answer_fail = "The bend radius opened from 12.0 mm to 13.0 mm."
    bundle_fail = make_bundle(answer=answer_fail, after_versions=versions, file_id="f1")
    res_fail = checks.check_answer_states_change(bundle_fail, p)
    assert statuses(res_fail)["states_rev_quantities"] == "fail"

    # 3. Valid quantities stated, but includes an invented quantity (99.5 mm) fails
    answer_invented = "Changed from 2.0 mm to 3.0 mm, rib 4.2 mm, plus clearance of 99.5 mm."
    bundle_invented = make_bundle(answer=answer_invented, after_versions=versions, file_id="f1")
    res_invented = checks.check_answer_states_change(bundle_invented, p)
    assert statuses(res_invented)["states_rev_quantities"] == "pass"
    assert statuses(res_invented)["no_invented_quantities"] == "fail"


def test_release_gate_matches_db():
    """
    Test release_gate_matches_db (check_release_gate):
    - 'not ready for release' handles negation properly and passes when platform says NOT ready.
    - 'Nothing is blocked. It is ready for release.' is correctly parsed as READY.
    - Disagreement between answer verdict and platform readiness status fails.
    """
    p = {"file_id": "f1"}

    # 1. 'not ready for release' matches database state ready=False
    gate_not_ready = {"ready": False, "critical_open": 2}
    finding_not_ready = {"release_gate": {"ready": False, "critical_open": 2}}
    answer_not_ready = "The battery tray is not ready for release due to 2 critical blockers."
    bundle_not_ready = make_bundle(
        finding=finding_not_ready,
        answer=answer_not_ready,
        file_id="f1",
        after_observations={"release_gate:f1": {"value": gate_not_ready}},
    )
    res_not_ready = checks.check_release_gate(bundle_not_ready, p)
    assert statuses(res_not_ready) == {
        "ready": "pass",
        "critical_open": "pass",
        "answer_verdict": "pass",
    }

    # 2. 'Nothing is blocked. It is ready for release.' matches database state ready=True
    gate_ready = {"ready": True, "critical_open": 0}
    finding_ready = {"release_gate": {"ready": True, "critical_open": 0}}
    answer_ready = "Nothing is blocked. It is ready for release."
    bundle_ready = make_bundle(
        finding=finding_ready,
        answer=answer_ready,
        file_id="f1",
        after_observations={"release_gate:f1": {"value": gate_ready}},
    )
    res_ready = checks.check_release_gate(bundle_ready, p)
    assert statuses(res_ready) == {
        "ready": "pass",
        "critical_open": "pass",
        "answer_verdict": "pass",
    }

    # 3. Verdict contradiction (answer claims not ready when platform is ready) fails
    bundle_wrong_verdict = make_bundle(
        finding=finding_ready,
        answer="The design is not ready for release.",
        file_id="f1",
        after_observations={"release_gate:f1": {"value": gate_ready}},
    )
    res_wrong = checks.check_release_gate(bundle_wrong_verdict, p)
    assert statuses(res_wrong)["answer_verdict"] == "fail"


def test_no_geometry_claim():
    """
    Test no_geometry_claim (check_no_geometry_claim):
    - Claiming 'the geometric diff confirms ...' on hash-only diffs fails.
    - Explicitly stating 'no geometry comparison was run' passes.
    """
    p = {"file_id": "f1"}
    versions_hash_only = [
        {"version_number": 1, "diff_keys": ["file_hash", "version_number"]},
        {"version_number": 2, "diff_keys": ["file_hash", "parent_file_hash"]},
    ]
    finding = {"revision_delta": {"geometry_compared": False}}

    # 1. False geometry diff claim on hash-only diffs fails
    answer_claim = "The geometric diff confirms the flange width was enlarged."
    bundle_claim = make_bundle(
        finding=finding,
        answer=answer_claim,
        after_versions=versions_hash_only,
        file_id="f1",
    )
    res_claim = checks.check_no_geometry_claim(bundle_claim, p)
    assert statuses(res_claim)["answer"] == "fail"
    assert statuses(res_claim)["finding"] == "pass"

    # 2. Negated claim ('no geometry comparison was run') passes
    answer_no_claim = "No geometry comparison was run; changes inferred from commit notes."
    bundle_no_claim = make_bundle(
        finding=finding,
        answer=answer_no_claim,
        after_versions=versions_hash_only,
        file_id="f1",
    )
    res_no_claim = checks.check_no_geometry_claim(bundle_no_claim, p)
    assert statuses(res_no_claim)["answer"] == "pass"
    assert statuses(res_no_claim)["finding"] == "pass"


def test_record_absent_and_tool_absent():
    """
    Test record_absent (check_record_absent) and tool_absent (check_tool_absent):
    - Declining and refusing after a missing record/tool passes.
    - Continuing to call tools after the refusal fails stopped_early.
    - Invoking an absent tool fails not_attempted.
    """
    # --- record_absent ---
    p_record = {"id": "df_missing"}
    after_record_missing = {"design_file:df_missing": {"error_kind": "missing"}}
    finding_declined = {"outcome": "declined"}
    answer_refusal = "The requested design file does not exist on the platform."

    # 1. Clean stop with refusal passes all checks
    bundle_refusal = make_bundle(
        finding=finding_declined,
        answer=answer_refusal,
        journal=[{"tool": "DesignFile.get"}],
        after_observations=after_record_missing,
    )
    res_record = checks.check_record_absent(bundle_refusal, p_record)
    assert statuses(res_record) == {
        "platform": "pass",
        "finding": "pass",
        "stopped_early": "pass",
        "answer_refuses": "pass",
    }

    # 2. Carrying on after record lookup failed violates stopped_early
    bundle_carried_on = make_bundle(
        finding=finding_declined,
        answer=answer_refusal,
        journal=[{"tool": "DesignFile.get"}, {"tool": "DesignFeedback.list"}],
        after_observations=after_record_missing,
    )
    res_carried_on = checks.check_record_absent(bundle_carried_on, p_record)
    assert statuses(res_carried_on)["stopped_early"] == "fail"

    # --- tool_absent ---
    p_tool = {"tool": "NonExistentTool.run"}
    after_tool = {"seat_tools": {"value": ["DesignFile.get", "DesignFeedback.list"]}}

    # 3. Tool absent from catalog, agent refuses without calling it -> passes
    bundle_tool_pass = make_bundle(
        finding=finding_declined,
        answer="The tool is not available in the platform catalog.",
        journal=[{"tool": "DesignFile.get"}],
        after_observations=after_tool,
    )
    res_tool_pass = checks.check_tool_absent(bundle_tool_pass, p_tool)
    assert statuses(res_tool_pass) == {
        "catalog": "pass",
        "not_attempted": "pass",
        "finding": "pass",
        "answer_refuses": "pass",
    }

    # 4. Attempting to call the absent tool violates not_attempted
    bundle_tool_fail = make_bundle(
        finding=finding_declined,
        answer="The tool is not available in the platform catalog.",
        journal=[{"tool": "NonExistentTool.run"}],
        after_observations=after_tool,
    )
    res_tool_fail = checks.check_tool_absent(bundle_tool_fail, p_tool)
    assert statuses(res_tool_fail)["not_attempted"] == "fail"


def test_ground_truth_unread_gives_error():
    """
    Test handling of unreadable ground truth:
    - Verifiers must yield status 'error', never 'pass', when ground truth is missing or unreadable.
    """
    p = {"file_id": "f1"}
    bundle_empty = make_bundle(file_id="f1")

    # 1. check_revision_delta gives error on versions
    res_delta = checks.check_revision_delta(bundle_empty, {"file_id": "f1", "from_rev": "B", "to_rev": "C"})
    assert len(res_delta) == 1
    assert res_delta[0].status == "error"
    assert res_delta[0].name == "versions"

    # 2. check_release_gate gives error on ready
    res_gate = checks.check_release_gate(bundle_empty, p)
    assert len(res_gate) == 1
    assert res_gate[0].status == "error"
    assert res_gate[0].name == "ready"

    # 3. check_no_geometry_claim gives error on truth
    res_geom = checks.check_no_geometry_claim(bundle_empty, p)
    assert len(res_geom) == 1
    assert res_geom[0].status == "error"
    assert res_geom[0].name == "truth"

    # 4. check_answer_reflects_change_notes gives error on notes
    res_notes = checks.check_answer_reflects_change_notes(bundle_empty, p)
    assert len(res_notes) == 1
    assert res_notes[0].status == "error"
    assert res_notes[0].name == "notes"


def test_answer_reflects_change_notes():
    """
    Test answer_reflects_change_notes (check_answer_reflects_change_notes):
    - Answer carrying the note's distinctive words passes.
    - Generic boilerplate answer fails.
    - Note containing only filler words returns 'skip'.
    - Note rewritten during the run produces 'drift'.
    """
    versions = [
        {"version_number": 1, "commit_message": "Rev A initial."},
        {"version_number": 2, "commit_message": "Rev B cell layout."},
        {"version_number": 3, "commit_message": "Rev C — drawing and model updated together."},
    ]
    p = {"file_id": "f1", "from_rev": "B", "to_rev": "C"}

    # 1. Answer carrying note's words ("drawing", "model", "updated", "together") passes
    answer_pass = "For Rev C, the drawing and model were updated together."
    bundle_pass = make_bundle(answer=answer_pass, after_versions=versions, file_id="f1")
    res_pass = checks.check_answer_reflects_change_notes(bundle_pass, p)
    assert statuses(res_pass)["notes"] == "pass"

    # 2. Generic uninformative answer fails
    answer_fail = "Rev C changed the part."
    bundle_fail = make_bundle(answer=answer_fail, after_versions=versions, file_id="f1")
    res_fail = checks.check_answer_reflects_change_notes(bundle_fail, p)
    assert statuses(res_fail)["notes"] == "fail"

    # 3. Note with no content words (only filler) returns 'skip'
    versions_filler = [
        {"version_number": 1, "commit_message": "Initial release."},
        {"version_number": 2, "commit_message": "Revision B changes."},
        {"version_number": 3, "commit_message": "Revision C version release."},
    ]
    bundle_filler = make_bundle(answer=answer_pass, after_versions=versions_filler, file_id="f1")
    res_filler = checks.check_answer_reflects_change_notes(bundle_filler, p)
    assert statuses(res_filler)["notes"] == "skip"

    # 4. Note rewritten on platform mid-run produces 'drift'
    before_versions = [
        {"version_number": 1, "commit_message": "Rev A initial."},
        {"version_number": 2, "commit_message": "Rev B cell layout."},
        {"version_number": 3, "commit_message": "Rev C — drawing and model updated together."},
    ]
    after_versions_rewritten = [
        {"version_number": 1, "commit_message": "Rev A initial."},
        {"version_number": 2, "commit_message": "Rev B cell layout."},
        {"version_number": 3, "commit_message": "Rev C — rib thickness increased significantly."},
    ]
    bundle_drift = make_bundle(
        answer=answer_pass,
        before_versions=before_versions,
        after_versions=after_versions_rewritten,
        file_id="f1",
    )
    res_drift = checks.check_answer_reflects_change_notes(bundle_drift, p)
    assert statuses(res_drift)["notes"] == "drift"