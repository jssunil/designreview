# `capstone_evals.py` and `rescore.py` — run and grade the benchmark again

*Reading order: **14th**. These are thin wrappers on `agentkit/harness`. Written in ASD-STE100 (Simplified
Technical English) style, 2026-10-14.*

## Commands

```bash
python capstone_evals.py [--skip-llm] [--only t01_battery_tray_rev_diff] [--sim packs/designreview/fixtures]   # run + grade
python rescore.py [runs/batches/<id> | runs/<run_id> ...]                                       # grade again (files only)
```

## `capstone_evals.py`

This command is the same as `python -m agentkit.harness.batch --grade`.

- Each task in `packs/designreview/tasks/` runs in its own subprocess.
- The harness reads the ground truth before and after each run. It uses its own login.
- The grader writes a `score.json` file for each run and a `report.md` file for the batch.

## `rescore.py`

This command is the same as `python -m agentkit.harness.grade`. It grades the newest batch, or the paths that
you give. It uses no network and no LLM. Thus a change to a check costs no new agent run.

## Shared flags

- `--batch-id <id>` gives the batch a name.
- `--repeat N` runs each task N times. A task passes only if each of its runs passed (pass^k). The header of
  `report.md` ends with `**P of T tasks pass on every run.**`

## Two proofs

- `python -m agentkit.harness.grade --check runs/batches/<id>` grades the batch again and writes nothing. It
  shows that the scores do not change.
- `python -m agentkit.harness.replay --calibrate runs/batches/<id> --sim packs/designreview/fixtures` runs
  each passing run again from one node, with an incorrect result in that node. It shows that the target check
  fails. A control replay with no change must still pass. CI does this after the mutants. See
  `agentkit_harness.md`.

## What replaced the axes of the first draft

The first draft searched the text of the answer for substrings, for example `"2.0" in answer`. It also read
"blocked" as "not ready". This method had three errors:

- it accepted "12.0 mm" as "2.0 mm";
- it read "nothing is blocked" as "not ready";
- it did not find a geometry claim in different words.

The verifiers in `packs/designreview/checks.py` now compare the structured finding **and** the answer with
the ground truth of the platform. They read negation in each sentence. They report drift when the data
changed during the run.

The flat `proofs/runs/*.json` files of the first draft have no ground truth. The verifiers cannot grade them.
