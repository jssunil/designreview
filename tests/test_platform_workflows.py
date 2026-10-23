"""
Live WRITE tests for DesignReview workflows.
Markers: live + writes (module-wide).

Author(s): ____________    Written by hand: [ ] yes

RULES (brief section 3): every write is attributed to team21 and the book is shared.
  * Create your own project / review / feedback inside the test (prefix names with "T21-TEST").
  * Never edit or transition records the team did not create.
  * Re-read a record before asserting on it; other agents change data underneath you.

Use cases to cover:
 DesignReview flow  (draft -> open -> in_progress -> completed)
  [x] start_review, begin_work, complete_review each move the status exactly one step
  [x] Skipping a step (complete_review straight from draft) is rejected
  [ ] TODO: A completed review cannot begin_work again
  [ ] TODO: A due date earlier than the creation date is rejected   (filed 1b711d71; try API / edit variants)
  [ ] TODO: Binding a review to a version clears no_review_binding in release_readiness
 DesignFeedback flow
  [x] start_working / submit_for_review / accept / reject / reopen follow the documented states
  [x] Invalid transitions are rejected
  [ ] TODO: tags = null / list / "" is stored safely (UI crash fb28c793 came from tags)
  [ ] TODO: Feedback whose review does not cover its file is rejected   (candidate bug C-3)
 Shares / supplier requests
  [ ] TODO: A revoked share can no longer be used
  [ ] TODO: A supplier request moves draft -> sent -> received -> accepted / rejected
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: a review created by the test is visible to a second read with the T21-TEST prefix and team21 as author
  [ ] TODO: the same transition twice (start_review on an open review) is rejected, and the status does not change
  [ ] TODO: clean-up: every record the test created ends in a closed or withdrawn state when the test finishes
"""

import datetime as dt

import pytest

try:
    from conftest import MCPError
except ImportError:
    from tests.conftest import MCPError

pytestmark = [pytest.mark.live, pytest.mark.writes]

RUN_TAG = f"T21-TEST {dt.datetime.now():%Y%m%d-%H%M%S}"


def record_id(record):
    return record.get("id") or record["data"]["id"]


def status_of(platform, entity, rid):
    return platform.call(f"{entity}.get", id=rid)["status"]   # re-read: never trust the write's response


@pytest.fixture(scope="module")
def company_id(suryodaya):
    return suryodaya.rest("GET", "/api/auth/me").json()["company_id"]


@pytest.fixture(scope="module")
def project_id(suryodaya, company_id):
    return record_id(suryodaya.call("DesignProject.create", name=f"{RUN_TAG} sandbox project",
                                    company_id=company_id,
                                    description="Created by Team 21 automated tests. Safe to ignore."))


@pytest.fixture
def new_review(suryodaya, project_id, company_id):
    return lambda: record_id(suryodaya.call("DesignReview.create", title=f"{RUN_TAG} review", project_id=project_id,
                                            company_id=company_id, review_type="dfm_review", priority="low"))


@pytest.fixture
def new_feedback(suryodaya, company_id, new_review, known):
    return lambda: record_id(suryodaya.call("DesignFeedback.create", title=f"{RUN_TAG} feedback",
                                            feedback_type="issue", priority="low", company_id=company_id,
                                            file_id=known["battery_tray"]["file_id"],
                                            review_id=new_review()))


def test_review_moves_exactly_one_step_per_transition(suryodaya, new_review):
    rid = new_review()
    assert status_of(suryodaya, "DesignReview", rid) == "draft"
    for tool, expected in [("DesignReview.start_review", "open"), ("DesignReview.begin_work", "in_progress"),
                           ("DesignReview.complete_review", "completed")]:
        suryodaya.call(tool, id=rid)
        assert status_of(suryodaya, "DesignReview", rid) == expected


def test_completing_a_draft_review_is_rejected_and_the_status_stays(suryodaya, new_review):
    rid = new_review()
    with pytest.raises(MCPError):
        suryodaya.call("DesignReview.complete_review", id=rid)
    assert status_of(suryodaya, "DesignReview", rid) == "draft"


def test_feedback_follows_the_documented_states_with_a_reopen(suryodaya, new_feedback):
    fid = new_feedback()
    assert status_of(suryodaya, "DesignFeedback", fid) == "open"
    for tool, expected in [("DesignFeedback.start_working", "in_progress"),
                           ("DesignFeedback.submit_for_review", "for_review"),
                           ("DesignFeedback.reopen", "in_progress"),
                           ("DesignFeedback.submit_for_review", "for_review"),
                           ("DesignFeedback.accept", "accepted")]:
        suryodaya.call(tool, id=fid)
        assert status_of(suryodaya, "DesignFeedback", fid) == expected


def test_accepting_open_feedback_is_an_invalid_transition(suryodaya, new_feedback):
    fid = new_feedback()
    with pytest.raises(MCPError):
        suryodaya.call("DesignFeedback.accept", id=fid)    # accept needs for_review
    assert status_of(suryodaya, "DesignFeedback", fid) == "open"

