# Manual Test Log — Team 21 (Design Review)

Checks done by hand in the browser (UI counts, sorting, thumbnails, crashes, date pickers).
Record every run, including passes. A failure that is a real platform bug becomes a bug report: put the returned ID in **Bug ID**.

- Suryodaya: https://agentswitch.theschoolofai.in/v/DesignReview:Home
- Keystone: https://class.agentswitch.theschoolofai.in/v/DesignReview:Home
- Views: see [`links.md`](../links.md) and [`research.md`](../research.md) §2

## How to fill in a row
| Field | Meaning |
| :-- | :-- |
| ID | `M-<area>-<nn>`, e.g. `M-REV-01` |
| Tester / Date | Who ran it, and when |
| Tenant | Suryodaya / Keystone / both |
| Record(s) | Page, file / review / feedback number or ID |
| Steps | Numbered, exact clicks and values |
| Expected | What should happen |
| Actual | What did happen (copy error text, attach a screenshot filename) |
| Result | PASS / FAIL / BLOCKED |
| Bug ID | Bug report ID if filed, or "known: <id>" for a duplicate |

## Areas to cover
- [ ] **CNT**: dashboard cards match their list pages (projects, files, reviews, feedback, shares)
- [ ] **SORT**: priority / due-date ordering on dashboard and lists
- [ ] **REV**: version history visible for the Battery Tray / Propeller, and matching the API
- [ ] **REVIEW**: review create / edit validation (due date, required fields), transitions in the UI
- [ ] **FB**: Feedback panel on files with unusual tags; feedback transitions in the UI
- [ ] **VIEW**: 3D viewer, thumbnails, glTF/STL previews
- [ ] **REL**: release readiness page vs the API result for the same file
- [ ] **SHARE**: create / revoke / expire share links
- [ ] **PERM**: things team21 must NOT be able to do (release / approve, other apps)

## Log

| ID | Tester | Date | Tenant | Record(s) | Steps | Expected | Actual | Result | Bug ID |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| | | | | | | | | | |
