# `agentkit/sim/` — the platform, offline

*Reading order: **8th**. Files: `fixture.py`, `sim_platform.py`, `capture.py`.*

## 10,000-ft view

Record every MCP and REST exchange the tasks need, once, against the live platform; replay them offline
for both the agent and the harness, with injectable faults.

## Why it's written this way

- **Capture is generic and complete.** `capture_fixture()` runs every task (template answers, no LLM) and its
  ground-truth probes through `RecordingSeat` / `RecordingRest` wrappers. Nothing is hand-written, so the
  fixture holds exactly the calls an offline run makes.
- **Replay is exact and loud.** A call whose `(tool, canonical args)` wasn't captured raises
  "not in fixture — re-capture it" (`unclassified`), never an empty result that could make a test pass.
  Results are fresh copies every time.
- **Faults test behaviour under trouble.**
  - `drop_tool:<tool>` — absent from the catalogue; calling it refuses (`not_in_seat`)
  - `fail_once:<tool>:<kind>` / `fail_always:<tool>:<kind>` — e.g. `flaky`, `missing`
  - `edit_text:<old>=><new>` — after the agent finishes, rewrite every recorded result (MCP and REST):
    "another team edited the record during the run" → the grader must say `drift`
  Call faults fire only while `armed`; the harness arms them around the agent's run so its own ground-truth
  reads stay undisturbed.
- **Validated fixtures.** `SeatFixture` is a pydantic model (format, tenant, captured_at, tools, calls).

## Per-tenant fixtures

A fixture holds one tenant's platform. `python -m agentkit.sim.capture --tenant <t>` runs only the tasks
whose `tenants` include `<t>` and writes `<pack>/fixtures/<t>.json`. `--sim` takes either one fixture
file (every task replays it) or the fixtures folder (each task replays `<its tenant>.json`); a missing
tenant fixture is an error that says how to capture it.

## Results on the captured fixtures

All seven tasks (five on Suryodaya, two on Keystone) pass offline in about a second. T01 under faults: `fail_once` flaky read → pass
(retried); `drop_tool:DesignFeedback.list` → pass (degrades without false claims); release readiness always
failing → fail (verdict unknown); rev C note edited during the run → drift.

## Data note

The fixtures (`packs/designreview/fixtures/suryodaya.json` ~430 KB, `keystone.json` ~115 KB) are snapshots of the course platform's
seed data, including person-like names in feedback text. Decide whether it belongs in git; offline tests skip
without them, and `python -m agentkit.sim.capture --tenant <tenant>` recreates them.

## How to test

`tests/test_sim_platform.py`.
