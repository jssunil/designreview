# Changes

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
