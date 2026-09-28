"""
Single-pass LLM-as-judge for the one eval axis that can't be checked
mechanically against ground truth: whether the agent's written DFM reasoning
is specific and evidence-grounded, or generic boilerplate.
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
