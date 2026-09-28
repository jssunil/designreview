"""
Re-grade saved harness runs without re-running the agent (no network, no LLM).

    python rescore.py                           # the newest batch under runs/batches/
    python rescore.py runs/batches/<batch_id>   # a specific batch
    python rescore.py runs/<run_id> ...         # specific run folders

A thin wrapper over `python -m agentkit.harness.grade`. Grading reads only
what the run saved (taskrun.json, tool_journal.jsonl, ground_truth_before/
after.json), so fixing a check and re-grading never costs an agent run.
The first draft's flat proofs/runs/*.json files carry no ground truth and
can't be graded by these checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentkit.harness.batch import RUNS_DIR
from agentkit.harness.grade import main as grade_main


def main(argv=None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if not paths:
        batches = sorted((RUNS_DIR / "batches").glob("*")) if (RUNS_DIR / "batches").exists() else []
        if not batches:
            print("No batches under runs/batches -- run `python capstone_evals.py` first.")
            return 1
        paths = [str(batches[-1])]
    return grade_main([str(Path(p)) for p in paths])


if __name__ == "__main__":
    sys.exit(main())
