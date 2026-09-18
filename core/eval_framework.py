"""
Generic Evaluation Harness & Multi-Axis Scoring Framework.
Inspired by EAGv3 S18/S17 benchmark patterns.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class EvaluationResult:
    task_id: str
    prompt: str
    passed: bool
    score: float  # 0.0 to 1.0
    axes: Dict[str, bool]
    latency_sec: float
    feedback_notes: str
    output: Optional[str] = None


class BaseEvaluator:
    """Base benchmark evaluation engine."""

    def evaluate_task(
        self,
        task_id: str,
        prompt: str,
        execution_fn: Callable[[str], str],
        axes_validators: Dict[str, Callable[[str], bool]],
        passing_threshold: float = 0.80
    ) -> EvaluationResult:
        """Run execution function and evaluate against rubric axes."""
        t0 = time.time()
        output = execution_fn(prompt)
        latency = round(time.time() - t0, 2)

        axes_scores: Dict[str, bool] = {}
        for axis_name, validator in axes_validators.items():
            try:
                axes_scores[axis_name] = bool(validator(output))
            except Exception:
                axes_scores[axis_name] = False

        passed_count = sum(1 for v in axes_scores.values() if v)
        score = round(passed_count / len(axes_validators), 2) if axes_validators else 0.0
        passed = score >= passing_threshold

        return EvaluationResult(
            task_id=task_id,
            prompt=prompt,
            passed=passed,
            score=score,
            axes=axes_scores,
            latency_sec=latency,
            feedback_notes="All benchmark axes passed successfully." if passed else "Failed one or more rubric axes.",
            output=output
        )
