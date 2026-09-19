"""
Ground-truth-separated evaluation axes.

Each axis independently re-fetches the relevant platform state and checks the
agent's claim against it -- it does NOT grep the agent's own prose output in
isolation. That was the first draft's approach (`"bend radius" in
analysis.lower()`), and PLAN.md Section 7's grading bar explicitly rules it
out: "verifiers that read the database directly... not ones that trust the
agent's own prose output." An agent that fabricates a confident-sounding
answer must fail an axis that checks it against reality, even if the prose
reads well.

Provenance: axis shape (deterministic, ground-truth-separate-from-claim
scorers over a saved run) is a trimmed port of
D:\\sjk\\eagv3\\S18Code\\evals\\axes.py's pattern, adapted from "recompute via
pytest" to "recompute via the live AgentSwitch API/MCP" as this domain's
ground truth. See core/judge.py for the one axis that is inherently
qualitative rather than ground-truth-checkable, and rescore.py for
re-scoring a saved run without re-running the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.harness import TaskRun

# An axis receives the run being scored plus a `ground_truth_context` --
# typically an AgentSwitchClient for a live scoring pass, or a plain dict of
# pre-fetched records when rescoring a saved run without new network calls
# (see rescore.py). Returns whether the axis passed.
AxisFn = Callable[[TaskRun, Any], bool]


@dataclass
class EvaluationResult:
    task_id: str
    prompt: str
    passed: bool
    score: float
    axes: Dict[str, bool]
    latency_sec: float
    feedback_notes: str
    run_path: Optional[str] = None


def evaluate_run(
    run: TaskRun,
    axes: Dict[str, AxisFn],
    ground_truth_context: Any = None,
    passing_threshold: float = 0.80,
    run_path: Optional[Path] = None,
) -> EvaluationResult:
    """Score a TaskRun against a set of named axes. Each axis is given the
    run AND `ground_truth_context` so it can check the agent's claim against
    reality rather than against itself. An axis that raises (e.g. the ground
    truth it needs is unavailable) counts as failed, not as an error that
    aborts scoring -- one broken axis shouldn't hide the others' results."""
    axes_scores: Dict[str, bool] = {}
    for name, fn in axes.items():
        try:
            axes_scores[name] = bool(fn(run, ground_truth_context))
        except Exception:
            axes_scores[name] = False

    passed_count = sum(axes_scores.values())
    score = round(passed_count / len(axes), 2) if axes else 0.0
    passed = score >= passing_threshold
    failed_axes = [name for name, ok in axes_scores.items() if not ok]

    return EvaluationResult(
        task_id=run.task_id,
        prompt=run.prompt,
        passed=passed,
        score=score,
        axes=axes_scores,
        latency_sec=run.seconds,
        feedback_notes=(
            "All rubric axes passed."
            if not failed_axes
            else f"Failed axes: {', '.join(failed_axes)}."
        ),
        run_path=str(run_path) if run_path else None,
    )
