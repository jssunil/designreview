"""
Team 21 benchmark -- run the task set and grade it against ground truth.

    python capstone_evals.py              # every task in packs/designreview/tasks, then grade
    python capstone_evals.py --skip-llm   # template answers, no LLM cost
    python capstone_evals.py --only t01_battery_tray_rev_diff

This is a thin wrapper over the harness (agentkit/harness): each task runs
in its own subprocess as a dry run, the platform state the verifiers need is
captured with the harness's own login before and after, and grading reads
only those saved files (packs/designreview/checks.py). The first draft's
answer-grepping axes are retired: they matched substrings of the agent's
prose ("2.0" in "12.0", "blocked" in "nothing is blocked") and are replaced
by checks that compare the structured finding and the answer with the
platform.
"""

from __future__ import annotations

import sys

from agentkit.harness.batch import main as batch_main


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--grade" not in argv:
        argv.append("--grade")
    return batch_main(argv)


if __name__ == "__main__":
    sys.exit(main())
