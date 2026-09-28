# `capstone_evals.py` and `rescore.py` — run and re-grade the benchmark

*Reading order: **11th**. Thin wrappers over `agentkit/harness`.*

```bash
python capstone_evals.py [--skip-llm] [--only t01_battery_tray_rev_diff] [--sim fixture.json]   # run + grade
python rescore.py [runs/batches/<id> | runs/<run_id> ...]                                       # re-grade (files only)
```

- `capstone_evals.py` = `python -m agentkit.harness.batch --grade`: every task in `packs/designreview/tasks/`
  runs in its own subprocess, ground truth is captured before and after with the harness's own login, and the
  batch is graded into `score.json` files and `report.md`.
- `rescore.py` = `python -m agentkit.harness.grade` on the newest batch (or the paths given). No network, no
  LLM: fixing a check and re-grading never costs an agent run.

## What replaced the first draft's axes

The first draft graded by searching the agent's prose for substrings (`"2.0" in answer`, "blocked" meaning
not ready). That passed "12.0 mm" as "2.0 mm", read "nothing is blocked" as not ready, and missed reworded
geometry claims. The verifiers in `packs/designreview/checks.py` compare the structured finding **and** the
answer with platform ground truth, handle negation per sentence, and report drift when the data moved during
the run. The first draft's flat `proofs/runs/*.json` files carry no ground truth and can't be graded by them.
