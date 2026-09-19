"""
Minimal cost/token ledger for the LLM gateway.

Provenance: trimmed from D:\\sjk\\eagv3\\glc_v5\\glc\\economics\\{pricing,meter}.py
-- kept the "record every call's estimated cost" idea, dropped budget-admission
control (pre-call 402s on breach) since a capstone script doesn't need to hard-
block on budget, only see where the money and tokens are going.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger("core.economics")

# Approximate USD per 1K tokens (blended input+output), illustrative only --
# not pulled from a live pricing API, just enough to rank providers by rough
# cost and spot an unexpectedly expensive run.
PRICE_PER_1K_TOKENS: Dict[str, float] = {
    "gemini": 0.0003,
    "groq": 0.0002,
    "cerebras": 0.0002,
    "anthropic": 0.003,
    "openrouter": 0.001,
    "ollama": 0.0,  # local, free
}


def estimate_tokens(text: str) -> int:
    """Rough estimate (~1.3 tokens/word). No tokenizer dependency on purpose --
    this is for cost visibility, not billing-accurate accounting."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3))


@dataclass
class LedgerEntry:
    provider: str
    model: str
    tokens_estimated: int
    cost_usd: float
    latency_sec: float
    ts: float = field(default_factory=time.time)


class CostLedger:
    """Running in-memory record of every LLM call this process made."""

    def __init__(self) -> None:
        self.entries: List[LedgerEntry] = []

    def record(self, provider: str, model: str, prompt: str, response: str, latency_sec: float) -> LedgerEntry:
        tokens = estimate_tokens(prompt) + estimate_tokens(response)
        price = PRICE_PER_1K_TOKENS.get(provider, 0.001)
        cost = round((tokens / 1000) * price, 6)
        entry = LedgerEntry(provider, model, tokens, cost, round(latency_sec, 3))
        self.entries.append(entry)
        logger.info(
            "llm_call provider=%s model=%s tokens~%d cost~$%.6f latency=%.2fs",
            provider, model, tokens, cost, latency_sec,
        )
        return entry

    def total_cost(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 6)

    def total_tokens(self) -> int:
        return sum(e.tokens_estimated for e in self.entries)

    def summary(self) -> Dict[str, float]:
        return {
            "calls": len(self.entries),
            "tokens_estimated": self.total_tokens(),
            "cost_usd_estimated": self.total_cost(),
        }
