"""
Cost/token ledger for the LLM gateway.

Records every call with the provider-reported token usage when the provider
returns it (`usage_estimated=False`), falling back to a word-count estimate
only when it doesn't. Prices are approximate list prices per 1M tokens,
input/output, for cost *visibility* -- not billing-accurate accounting.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("agentkit.llm.economics")

# (input, output) USD per 1M tokens. Model-specific rows win over provider rows.
# Anthropic rows from the Claude model table (cached 2026-06-24); others approximate.
PRICE_PER_MTOK: Dict[str, Tuple[float, float]] = {
    "anthropic:claude-opus-5": (5.00, 25.00),
    "anthropic:claude-opus-5-5": (4.00, 20.00),
    "anthropic:claude-sonnet-5": (2.00, 10.00),
    "anthropic:claude-sonnet-4-6": (3.00, 15.00),
    "anthropic:claude-haiku-4-5": (1.00, 5.00),
    "anthropic": (3.00, 15.00),
    "gemini": (0.30, 2.50),
    "groq": (0.59, 0.79),
    "cerebras": (0.85, 1.20),
    "nvidia": (0.60, 0.60),
    "openrouter": (1.00, 1.00),
    "ollama": (0.0, 0.0),  # local
}


def estimate_tokens(text: str) -> int:
    """~1.3 tokens/word; used only when a provider reports no usage."""
    if not text:
        return 0
    return max(1, int(len(text.split()) * 1.3))


def price_for(provider: str, model: str) -> Tuple[float, float]:
    return PRICE_PER_MTOK.get(f"{provider}:{model}") or PRICE_PER_MTOK.get(provider) or (1.0, 1.0)


@dataclass
class LedgerEntry:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_sec: float
    usage_estimated: bool = False
    stop_reason: Optional[str] = None
    ts: float = field(default_factory=time.time)

    @property
    def tokens_estimated(self) -> int:  # first-draft field name, kept for callers
        return self.input_tokens + self.output_tokens


class CostLedger:
    """Running in-memory record of every LLM call this process made."""

    def __init__(self) -> None:
        self.entries: List[LedgerEntry] = []
        self._lock = threading.Lock()

    def record(self, provider: str, model: str, prompt: str = "", response: str = "",
               latency_sec: float = 0.0, *, input_tokens: Optional[int] = None,
               output_tokens: Optional[int] = None, stop_reason: Optional[str] = None) -> LedgerEntry:
        estimated = input_tokens is None or output_tokens is None
        tin = input_tokens if input_tokens is not None else estimate_tokens(prompt)
        tout = output_tokens if output_tokens is not None else estimate_tokens(response)
        pin, pout = price_for(provider, model)
        cost = round(tin / 1e6 * pin + tout / 1e6 * pout, 6)
        entry = LedgerEntry(provider, model, tin, tout, cost, round(latency_sec, 3), estimated, stop_reason)
        with self._lock:
            self.entries.append(entry)
        logger.info("llm_call provider=%s model=%s in=%d out=%d%s cost=$%.6f latency=%.2fs stop=%s",
                    provider, model, tin, tout, " (estimated)" if estimated else "", cost, latency_sec, stop_reason)
        return entry

    def total_cost(self) -> float:
        return round(sum(e.cost_usd for e in self.entries), 6)

    def total_tokens(self) -> int:
        return sum(e.input_tokens + e.output_tokens for e in self.entries)

    def summary(self) -> Dict[str, float]:
        return {
            "calls": len(self.entries),
            "input_tokens": sum(e.input_tokens for e in self.entries),
            "output_tokens": sum(e.output_tokens for e in self.entries),
            "tokens_estimated": self.total_tokens(),  # first-draft key, kept for callers
            "usage_estimated_calls": sum(1 for e in self.entries if e.usage_estimated),
            "cost_usd_estimated": self.total_cost(),
        }
