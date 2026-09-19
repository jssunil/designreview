"""
Reload saved TaskRun proofs and recompute their eval score without
re-running the (live, paid) agent -- only the cheap, deterministic
ground-truth axes are recomputed by default; the LLM-judge axis is skipped
unless --with-judge is passed, since it's the one axis that costs money and
can vary between calls.

Provenance: the raw-run-then-rescore split is ported from
D:\\sjk\\eagv3\\S18Code\\rescore.py -- built there after a scoring-code bug
shipped wrong and could only be fixed by burning more GPU-hours re-running
the agent; rescoring a saved run makes a scoring bug a free fix instead.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from as_client import AgentSwitchClient
from capstone_evals import AXES
from core.eval_framework import evaluate_run
from core.harness import TaskRun

PROOFS_DIR = Path(__file__).parent / "proofs" / "runs"
RESULTS_PATH = Path(__file__).parent / "proofs" / "results.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Rescore saved TaskRun proofs without re-running the agent.")
    parser.add_argument("--tenant", default="suryodaya")
    parser.add_argument(
        "--with-judge", action="store_true", help="Also run the LLM-judge axis (costs one LLM call per run)."
    )
    args = parser.parse_args()

    client = AgentSwitchClient(args.tenant)
    client.login()
    client.init_mcp()

    axes = dict(AXES)
    if not args.with_judge:
        axes.pop("judged_reasoning_quality", None)

    run_paths = sorted(PROOFS_DIR.glob("*.json"))
    if not run_paths:
        print(f"No saved runs found under {PROOFS_DIR}")
        return

    results = []
    for path in run_paths:
        run = TaskRun.load(path)
        result = evaluate_run(run, axes, ground_truth_context=client, run_path=path)
        results.append(asdict(result))
        print(f"{path.name}: score={result.score} passed={result.passed}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} rescored result(s) to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
