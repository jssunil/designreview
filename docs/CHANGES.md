# Changes

## 2026-09-30 — Write mode: hand-offs filed as escalations, verified, withdrawn (Part 3)

### Why

Part 2 proposed hand-offs but never delivered them. Part 3 files them on the platform so a person is actually
on the hook, proves from ground truth that exactly the right escalations exist, and then cleans up. The
platform is shared, so a test run must not leave tickets in someone's inbox.

### Decisions

| Question | Choice |
|---|---|
| Who escalations go to | By **name** per tenant in `handoffs.toml` (Suryodaya: Meera Kulkarni, the only assignable person), resolved to a party id through `escalations.assignees` on every run; nothing hard-coded |
| A tenant with no assignable person (Keystone) | **Decline**: prepared, not filed, with the reason; graded as correct |
| After grading | **Auto-withdraw** the run's escalations and close their sessions, after the "after" ground truth is captured; `--keep-writes` and a manual `agentkit.harness.cleanup` for the rest |

### Added / changed

| What | Where |
|---|---|
| `TaskDef.allow_writes`; `run_one --write`; `batch --write [--keep-writes]`. A run writes only with both the flag and the task opt-in, live only (refused with `--sim`) | `agentkit/harness/tasks.py`, `run_one.py`, `batch.py` |
| Write allowlist: `tool_calls_policy` fails a write run that calls a tool outside the pack's `extras["write_tools"]`; the `sneaky_write` mutant now applies to write runs too | `agentkit/harness/checks.py`, `grade.py`, `mutants.py` |
| Clean-up hook: the batch calls the pack's `extras["cleanup"]` after the "after" capture → `cleanup.json`; `python -m agentkit.harness.cleanup --tenant t [--dry-run]` or `--run-dir` | `agentkit/harness/batch.py`, `agentkit/harness/cleanup.py` |
| `raise_handoffs` files for real: AgentSession (titled with the subject) → `escalations.raise` to the resolved assignee. Skips a subject that's already open; declines with no assignee or when the name isn't assignable; on a refusal or error, closes the session and fails the node | `packs/designreview/handoffs.py`, `handoffs.toml` (`[assignees]`) |
| `cleanup_escalations`: withdraw (with `expect_status`) and close the session; only our `T21-DR ` subjects, and only open or acknowledged ones | `packs/designreview/handoffs.py` |
| Probes `escalations:<file_id>` (ours, any status) and `assignees` | `packs/designreview/probes.py` |
| `handoffs_match_platform` judges filing against those probes. A dry run must leave no new escalation. A write run needs an open `needs_another_app` escalation assigned to the configured, assignable person for every owner, and nothing stray | `packs/designreview/checks.py` |
| `answer_states_handoffs`: a write run's answer must name every filed ESC number; a filing claim with nothing new on the platform fails | `packs/designreview/checks.py` |
| Finding (`escalations`, `already_open`, `filing_errors`, `assignee`), claim rules (ESC numbers, assignee), template and narration for write runs | `finding.py`, `claims.py`, `templates.py`, `prompts/narrate.md` |
| Mutants `escalation_not_on_platform`, `filed_number_unstated` (write runs) | `packs/designreview/mutants.py` |
| t08 and t09 set `allow_writes = true` (still dry runs without `--write`) | `packs/designreview/tasks/` |
| Fixtures re-captured (the two new probes) | `packs/designreview/fixtures/` |
| Reference tests (16 new, against an in-memory writable platform); `tests/` templates updated | `tests_local/test_write_mode.py`, `tests/test_harness.py`, `tests/test_handoffs.py` |

### First live write (2026-09-30, batch `20260930T212448`, LLM answers)

| | Result |
|---|---|
| t08 (Suryodaya) | **pass**. Filed **ESC-2026-00059** (manufacturing: bend-radius cracking, weld access) and **ESC-2026-00060** (quality: PPAP level 3), both open, `needs_another_app`, assigned to Meera Kulkarni, due in 24 h. The answer names both numbers and the assignee |
| t09 (Keystone) | **pass**. Nothing filed (no hand-off needed) |
| Evidence → clean-up | `ground_truth_after.json` shows both open. Then both were **withdrawn** and both sessions **closed** (re-read from the platform); `cleanup --dry-run` finds nothing of ours still open |
| Journal | writes: 2× `AgentSession.create`, 2× `escalations.raise` (all allowlisted) |
| Calibration (write batch) | OK: both write-run mutants caught; `tool_calls_policy` exercised in write mode |
| `tests_local` offline | 494 passed |

Writing the tests exposed one verifier gap, now fixed. When the configured person isn't assignable on the
platform, declining is correct, but the verifier demanded an escalation anyway. It now reads who is assignable
as its own ground truth.

## 2026-09-30 — Dry-run hand-offs and the project schedule (Part 2)

### Why

The seat can see a problem that it can't fix. The Battery Tray's release is blocked by a critical
"2.0 mm inside bend radius — cracking risk" finding, and PPAP level 3 is required. Both need teams whose tools
this seat doesn't have (manufacturing, quality). The agent used to report the blocker and stop. Now it says who
needs to act, cites the exact items, and shows the escalation it *would* file. In a dry run it files nothing,
and every part of that is graded against the platform.

The platform also doesn't flag late work: the Battery Tray's "DFM review closed" milestone was due 2026-09-19
and still reads `in_progress`. The finding computes lateness from the date.

### Added

| What | Where |
|---|---|
| **Generic write preview.** A write action may register `preview`. In a dry run the engine calls it instead of the action, and the node ends `handed_off` with `{"would_file": …, "filed": false}` (`resolved` if the preview is empty; still `declined` without a preview). `record_steps` keeps a hand-off's data | `agentkit/registry.py`, `agentkit/graph/engine.py`, `agentkit/agents/planned.py` |
| Hand-off topics as data: owner (manufacturing / quality / purchasing), ask, whole-word keywords; validated by pydantic | `packs/designreview/handoffs.toml` |
| Actions `list_milestones`, `find_cross_seat_dependencies` (reads) and `raise_handoffs` (write, preview-only: the `AgentEscalation.create` call per hand-off) | `packs/designreview/handoffs.py` |
| Plan `release_handoff`: gate + feedback + milestones in parallel → cross-seat match (settled join) → a rule adds `raise_handoffs` only when something is proposed | `packs/designreview/plans/release_handoff.toml` |
| Finding sections `schedule` (past_due computed from the snapshot date) and `handoffs` (proposed, would_file, filed); sections whose nodes aren't planned are left out | `packs/designreview/finding.py` |
| Claim rules: name each owner, say the hand-offs are only proposed, say "no other team" when none is needed, name each past-due milestone; contradictions for "I escalated / has been filed / was notified" in a dry run and "on track" while late | `packs/designreview/claims.py` |
| Template and narration sections "Schedule" and "Who needs to act" | `templates.py`, `prompts/narrate.md` |
| Probe `milestones:<project_id>`; the release-gate probe now records blocker ids | `packs/designreview/probes.py` |
| Verifiers `handoffs_match_platform`, `schedule_matches_db`, `answer_states_handoffs` (own matchers; drift-aware) | `packs/designreview/checks.py` |
| 10 mutants: invented / dropped / closed-item / marked-filed hand-off, filing claimed, owner unnamed, "no hand-off" unstated, late milestone hidden, date shifted, "on track" claimed | `packs/designreview/mutants.py` |
| Task `t08_battery_tray_handoff` (Suryodaya): two hand-offs, one past-due milestone | `packs/designreview/tasks/` |
| Task `t09_keystone_no_handoff_late_schedule` (Keystone): the negative case — no hand-off is right, three milestones are late | `packs/designreview/tasks/` |
| Both fixtures re-captured (milestone reads) | `packs/designreview/fixtures/` |
| Reference tests (72 new) and the `tests/test_handoffs.py` to-do template; the `test_dag_engine` template covers the preview | `tests_local/test_handoffs.py`, `tests/` |

Nothing in `agentkit/` names a team, a tool or a tenant: the preview is a generic write-action hook, and the
owners, topics and escalation tool are pack data.

### Results

| Run | Result |
|---|---|
| Live batch, LLM answers, both tenants | **9/9 pass** |
| Calibration (live batch) | OK: all 29 mutants caught, no check unexercised |
| Offline batch, per-tenant fixtures | 9/9 pass |
| `tests_local` offline / `tests/` | 478 passed / 4 passed |

The claim audit caught a real invention on t08's first LLM draft (an unsupported "15 mm") and the rewrite fixed
it. Writing the tests also exposed two verifier weaknesses, both fixed:
- "Proposed" in the hand-off's ask text ("propose the process change") no longer counts as saying the hand-off
  wasn't filed.
- `answer_states_handoffs` now reports drift (not fail) when a milestone or item changed during the run.

### Not done here (Part 3)

Filing for real: `raise_handoffs` in write mode (behind `--write` and a task-level `allow_writes`), an
AgentSession for the escalation, `escalations.raise` with a named assignee (needs your choice of assignee),
de-duplication against open escalations, and a `--cleanup` withdraw. The first live write waits for your
go-ahead.

## 2026-09-30 — Run viewer (read-only); claim-audit timestamp fix

### Why

Every run already leaves a full audit trail on disk: answer, finding, graph, tool journal, ground truth
before and after, score and cost. But reading it meant opening nine JSON files per run. The viewer puts
that trail on one local page. It's useful for explaining a graded run and for finding out quickly why a
check failed or drifted.

### Added

| What | Where |
|---|---|
| Run viewer: standard-library HTTP server, JSON API (`/api/batches`, `/api/runs`, `/api/batches/<id>`, `/api/runs/<id>`), plain HTML/JS/CSS page with no framework and no build step | `agentkit/viewer/` (`python -m agentkit.viewer`) |
| Pages: batches; all runs; batch (task × tenant, failing checks, calibration per mutant, `report.md`); run tabs Answer · Finding · Checks · Evidence (graph timeline) · Tool calls · Ground truth (drift diff) · Cost | `agentkit/viewer/static/` |
| Safety: GET/HEAD only (405 otherwise), strict id pattern and folder confinement, static whitelist, text-only rendering, `\u`-escaped JSON, CSP and nosniff headers, binds to 127.0.0.1 | `agentkit/viewer/server.py` |
| Explainer; `tests/test_viewer.py` to-do template; README section | `docs/explainers/agentkit_viewer.md`, `tests/`, `README.md` |
| Reference tests (38 new): API over hand-built and offline-batch run folders, traversal cases, read-only HTTP, headers, hostile-text escaping | `tests_local/test_viewer.py` |

### Fixed

| What | Where |
|---|---|
| The claim audit treated every number in the finding as supported, **including the digits of timestamps and UUIDs**. With a snapshot taken at 12:05, an invented "12 mm" passed the audit. Dates, clock times and UUIDs are now removed before numbers are collected. A regression test pins the clock | `agentkit/answer/claim_audit.py` (`known_numbers`) |

### Results

| Run | Result |
|---|---|
| `tests_local` offline | 406 passed (includes the viewer tests; the previously time-dependent audit test is now stable) |
| `tests/` (your suite) | 4 passed |
| Offline batch, per-tenant fixtures | 7/7 pass |
| Viewer on the live `runs/` folder in Chrome | batches, batch, run tabs render; no console errors; POST → 405; `..%2F.env` → 404 |

## 2026-09-30 — Keystone tasks; generic version selection; per-tenant fixtures

### Why

A check of both AgentSwitch tenants found no new design-review data since 2026-09-28, but showed that
**Keystone** (seeded 2026-09-16) has 21 files with 2–6 revisions and real DFM standards, while every graded
task ran only on Suryodaya. Keystone's version notes also exposed two shapes the task set didn't cover:

- **Boilerplate notes with rev labels** — "Revision C — drawing and model updated together". The right answer
  reports exactly that and invents no specific changes.
- **Real engineering notes without rev labels** — "DFM fixes: increased draft angles, added rib
  reinforcement". The question can only be "what changed in the latest revision", so the verifiers must be
  able to compare the two newest versions rather than named revs.

### Added

| What | Where |
|---|---|
| Task `t06_keystone_boilerplate_notes` — Hydraulic Manifold Mount rev B→C (Keystone) | `packs/designreview/tasks/t06_keystone_boilerplate_notes.toml` |
| Task `t07_keystone_latest_dfm_fixes` — Auger Drive Housing Cover, latest revision (Keystone) | `packs/designreview/tasks/t07_keystone_latest_dfm_fixes.toml` |
| Verifier `answer_reflects_change_notes` — the answer must carry the distinctive words of the compared version's note (default at least 3; all of them when the note has fewer); `skip` when the note has none; `drift` when the note was rewritten during the run | `packs/designreview/checks.py` |
| `select_versions()` — one generic rule for which versions a check compares: `from_rev`/`to_rev` (rev letters or numbers from commit messages, including "Revision B") or `latest = true` (the two newest versions) | `packs/designreview/checks.py` |
| Mutant `change_notes_ignored` — strips the note's words from a passing answer; `answer_reflects_change_notes` must fail | `packs/designreview/mutants.py` |
| Keystone platform fixture (16 exchanges) | `packs/designreview/fixtures/keystone.json` |
| Reference tests for all of the above (15 new) | `tests_local/` |

### Changed

| What | Where |
|---|---|
| `revision_delta_matches_db` and `answer_states_change` use `select_versions()` instead of requiring rev letters | `packs/designreview/checks.py` |
| `answer_reflects_change_notes` added to the existing rev-diff tasks t01, t04, t05 | `packs/designreview/tasks/` |
| Fixture capture runs only the tasks whose `tenants` include the tenant being captured (it used to run every task against it) | `agentkit/sim/capture.py` |
| `--sim` accepts the fixtures **folder**: each task replays `<its tenant>.json`; a single file still works; a missing tenant fixture is an error that says how to capture it | `agentkit/harness/batch.py` (`SimSession`) |
| Suryodaya fixture re-captured (the seat gained two tools, `events.catalog` and `tools.preview`) | `packs/designreview/fixtures/suryodaya.json` |
| README, explainers (`packs_designreview`, `agentkit_sim`, `agentkit_harness`, `capstone_agent`, `capstone_evals`), `tests/README.md` and the `test_verifiers` / `test_sim_platform` to-do templates | `README.md`, `docs/`, `tests/` |

Nothing in `agentkit/` names Keystone or a design-review entity: the tenant is task data (`tenants = ["keystone"]`)
and the version rule is a verifier parameter.

### Results

| Run | Result |
|---|---|
| Live batch, LLM answers, both tenants | **7/7 pass** (t06: 16/16 checks; t07: 16/16 checks) |
| Offline batch, per-tenant fixtures | **7/7 pass** in about a second |
| Calibration (live and offline batches) | OK — every mutant caught, no check unexercised; `change_notes_ignored` caught on all 5 runs it applies to |
| `tests_local` offline | 365 passed |

What the LLM answered for t06: *"The commit messages for rev B and rev C both state only that 'drawing and
model updated together,' so the specific design intent of the rev C change cannot be determined from the
platform data."*

### Noticed, not changed

- Keystone `DF-2026-00026` has `status = approved`, yet release readiness reports "The file is not in the
  canonical ready state" — a possible platform inconsistency; confirm against the DesignFile state machine
  before reporting it.
- Suryodaya milestones carry `created_at` equal to their due dates (two in the future: 2026-10-03, 2026-10-17)
  — seed data with synthetic timestamps; a possible data-integrity report.
