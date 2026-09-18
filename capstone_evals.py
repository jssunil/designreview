"""
Team 21 Capstone Benchmark Evaluation Suite.
Uses the generic BaseEvaluator to score the Team 21 Design Review Agent against Section 8 rubric.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from capstone_agent import Team21DesignReviewAgent
from core.eval_framework import BaseEvaluator, EvaluationResult


class Team21Evaluator:
    """Capstone benchmark suite for Team 21."""

    def __init__(self, tenant: str = "suryodaya"):
        self.agent = Team21DesignReviewAgent(tenant)
        self.evaluator = BaseEvaluator()

    def run_core_benchmark(self) -> EvaluationResult:
        prompt = "What changed between rev B and rev C, and are there manufacturability problems in this part?"

        # 5 Concrete Rubric Validators
        validators = {
            "detected_bend_radius_change": lambda out: (
                "bend radius" in out.lower() and ("2.0" in out or "2 mm" in out) and ("3.0" in out or "3 mm" in out)
            ),
            "detected_blank_growth": lambda out: (
                "4.2 mm" in out or "blank length" in out.lower()
            ),
            "detected_micro_cracking": lambda out: (
                any(k in out.lower() for k in ["micro-cracking", "cracking", "formability", "tear"])
            ),
            "detected_fixture_impact": lambda out: (
                any(k in out.lower() for k in ["weld fixture", "fixture", "tooling", "locator"])
            ),
            "governed_release_readiness": lambda out: (
                any(k in out.lower() for k in ["not yet ready", "pending", "release", "review"])
            ),
        }

        def execute_fn(p: str) -> str:
            res = self.agent.answer_capstone_benchmark(p)
            return res["analysis"]

        return self.evaluator.evaluate_task(
            task_id="T01_BATTERY_TRAY_REVC_DFM",
            prompt=prompt,
            execution_fn=execute_fn,
            axes_validators=validators,
            passing_threshold=0.80
        )


if __name__ == "__main__":
    suite = Team21Evaluator("suryodaya")
    print("=== EXECUTING TEAM 21 CAPSTONE BENCHMARK EVALUATION ===")
    result = suite.run_core_benchmark()
    print(json.dumps(asdict(result), indent=2))
