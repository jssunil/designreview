# `agentkit/harness/` — tasks, ground truth, grading, self-test

*Reading order: **7th**. Files: `tasks.py`, `ground_truth.py`, `run_one.py`, `batch.py`, `checks.py`,
`grade.py`, `mutants.py`. Domain checks live in `packs/designreview/checks.py`.*

## 10,000-ft view

```
task.toml ──► ground truth BEFORE (harness's own login)
          ──► agent run in a subprocess (dry run) ──► runs/<run_id>/{taskrun.json, tool_journal.jsonl, graph.json, llm_ledger.json}
          ──► ground truth AFTER
          ──► grade (files only) ──► score.json per run, report.md per batch
          ──► mutants (prove every check can fail) ──► calibration.json
```

## Why it's written this way

- **Verifiers read the database, not the agent's prose** — the brief's grading bar. The harness captures what
  the platform says with its **own** login: REST for records, a separate MCP session for the POST-only
  release-readiness endpoint. A failed read is recorded with its error kind (a `missing` record is itself
  ground truth for a refusal task), and flaky reads are retried.
- **Everything on disk before scoring.** Checks are pure functions over the run folder: no network, no LLM.
  A verifier bug is fixed by re-grading saved runs, never by re-running the agent.
- **Five statuses.** `pass`, `fail`, `drift` (the claim matched the platform before the run but not after —
  the data moved under the run), `skip`, `error` (ground truth unavailable or the check crashed). `error`
  never counts as a pass. Task status: any fail/error → fail; only drift → drift; else pass.
- **Built-in checks for every task:** `run_completed`, `tool_calls_policy` (from the journal: a dry run may
  only read), `answer_present`.
- **Subprocess per task** so an agent crash can't take the batch down; the running-record is the evidence.
- **Unique batch ids.** The batch folder is claimed with an exclusive `mkdir`; a same-second collision gets
  `-2`, `-3`… (two batches once overwrote each other before this).
- **Calibration.** `mutants.py` applies each mutant (3 built-in + the pack's) to copies of passing runs and
  requires its target check to fail. A mutant that slips through (MISSED) or a check nothing can make fail
  (UNEXERCISED) fails calibration. Failing runs are skipped; run folders are never modified.

## Task files

TOML, validated by the `TaskDef` pydantic model: `id, pack, plan, prompt, params, tenants, verifiers,
description`. Unknown keys, unknown verifiers, missing plans and duplicate ids are errors at load time.

## CLIs

| Command | What |
|---|---|
| `python -m agentkit.harness.batch [--only ids] [--skip-llm] [--grade] [--sim fixtures-dir-or-file --fault spec]` | run tasks (each on its own tenant) |
| `python -m agentkit.harness.grade <batch-dir or run-dirs>` | grade saved runs (exit 1 on any fail) |
| `python -m agentkit.harness.mutants [batch-dir]` | calibrate (exit 1 on MISSED/UNEXERCISED) |
| `python -m agentkit.harness.run_one --task t.toml --run-dir d --run-id id` | one task, one folder |

## Key types

`TaskDef`, `VerifierSpec`, `GroundTruthReader`, `RunBundle` (task, taskrun, graph, journal, before, after;
`truth(phase, key)`, `obs_error()`, `tools_called()`), `CheckOutcome`, `CheckDef`, `MutantDef`, `SimSession`.

## How to test

`tests/test_harness.py`, `tests/test_verifiers.py`, `tests/test_calibration.py`; offline end-to-end with
`SimSession` (see `agentkit_sim.md`).
