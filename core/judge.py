"""
Single-pass LLM-as-judge for the one eval axis that can't be checked
mechanically against ground truth: whether the agent's written DFM reasoning
is specific and evidence-grounded, or generic boilerplate.

Provenance: a simplified single-judge version of the rubric idea in
D:\\sjk\\eagv3\\S17Code\\s17code\\evals\\judge.py -- no multi-model judge
panel, no disagreement tracking; one call, one short rubric, one score. Every
other eval axis in core/eval_framework.py is deterministic and ground-truth
based; this is the deliberate exception for the one criterion ("does this
read like it was written for this exact part") that has no ground-truth
record to check against.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from core.llm_gateway import LLMGateway

RUBRIC = """Score the following engineering analysis on a 0-5 scale for each
criterion below, then respond with ONLY a JSON object of the exact shape
{{"specific": 0-5, "consistent": 0-5, "non_generic": 0-5, "rationale": "..."}}
-- no prose before or after the JSON.

Criteria:
- specific: cites concrete numbers/entities from the evidence (not vague claims)
- consistent: the conclusion follows from the cited evidence, no contradictions
- non_generic: reads like it was written for this exact part, not a template

ANALYSIS TO SCORE:
---
{analysis}
---
"""


@dataclass
class JudgeScore:
    specific: int
    consistent: int
    non_generic: int
    rationale: str

    @property
    def average(self) -> float:
        """Normalized 0-1 average across the three 0-5 criteria."""
        return round((self.specific + self.consistent + self.non_generic) / 15.0, 2)


def judge_analysis(analysis: str, gateway: Optional[LLMGateway] = None) -> JudgeScore:
    """Runs the rubric once via the LLM gateway's "fast" tier (this is a
    cheap sanity check, not the main synthesis call) and parses the JSON
    verdict out of the response."""
    gateway = gateway or LLMGateway()
    raw = gateway.call(RUBRIC.format(analysis=analysis), temperature=0.0, json_mode=True, tier="fast")
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    data = json.loads(match.group(0) if match else raw)
    return JudgeScore(
        specific=int(data.get("specific", 0)),
        consistent=int(data.get("consistent", 0)),
        non_generic=int(data.get("non_generic", 0)),
        rationale=str(data.get("rationale", "")),
    )
