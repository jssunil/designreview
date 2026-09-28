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
  [ ] start_review, begin_work, complete_review each move the status exactly one step
  [ ] Skipping a step (complete_review straight from draft) is rejected
  [ ] A completed review cannot begin_work again
  [ ] A due date earlier than the creation date is rejected   (filed 1b711d71; try API / edit variants)
  [ ] Binding a review to a version clears no_review_binding in release_readiness
 DesignFeedback flow
  [ ] start_working / submit_for_review / accept / reject / reopen follow the documented states
  [ ] Invalid transitions are rejected
  [ ] tags = null / list / "" is stored safely (UI crash fb28c793 came from tags)
  [ ] Feedback whose review does not cover its file is rejected   (candidate bug C-3)
 Shares / supplier requests
  [ ] A revoked share can no longer be used
  [ ] A supplier request moves draft -> sent -> received -> accepted / rejected
"""

import pytest

pytestmark = [pytest.mark.live, pytest.mark.writes]

# Write your tests below.
