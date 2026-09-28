"""
compose_answer(): turn a finding (computed in code) into readable prose.

The finding is complete before this runs; the LLM only words it. Flow:

  1. a deterministic template rendering of the finding (always built)
  2. one LLM draft, told up front which facts it must state
  3. audit_narrative() against the finding
  4. if the audit fails: one rewrite with the specific problems listed
  5. if it still fails, the LLM is unreachable, or the answer was cut off:
     the template is the answer

The result always has an answer, and `source` says which ("llm" |
"template"), with every attempt and its audit kept for the run record.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from agentkit.answer.claim_audit import ClaimRules, audit_narrative, correction_note

MAX_ATTEMPTS = 2  # first draft + one rewrite


def _ask(gateway: Any, prompt: str, system_prompt: str, tier: str) -> tuple[str, Optional[str]]:
    """(text, stop_reason). stop_reason is None when the gateway can't say."""
    detailed = getattr(gateway, "call_detailed", None)
    if callable(detailed):
        reply = detailed(prompt, system_prompt=system_prompt, temperature=0.1, tier=tier)
        return (getattr(reply, "text", "") or "").strip(), getattr(reply, "stop_reason", None)
    return (gateway.call(prompt, system_prompt=system_prompt, temperature=0.1, tier=tier) or "").strip(), None


def compose_answer(query: str, finding: Dict[str, Any], *, gateway: Any, rules: ClaimRules,
                   template: Callable[[Dict[str, Any]], str], system_prompt: str = "",
                   instructions: str = "", tier: str = "quality",
                   max_attempts: int = MAX_ATTEMPTS) -> Dict[str, Any]:
    plain = template(finding)
    attempts: List[Dict[str, Any]] = []

    def fallback(reason: str) -> Dict[str, Any]:
        return {"text": plain, "source": "template", "fallback_reason": reason,
                "attempts": attempts, "audit": audit_narrative(plain, finding, rules), "template": plain}

    if gateway is None:
        return fallback("no LLM gateway configured")

    required = [f.label for f in rules.required(finding)]
    must = ("\n\nYour answer must state: " + "; ".join(required) + ".") if required else ""
    base = (f"QUESTION:\n{query}\n\nFINDING (JSON computed from live platform data -- the only "
            f"facts you may use):\n{json.dumps(finding, indent=1, default=str)}\n\n{instructions}{must}")
    prompt = base
    for _ in range(max_attempts):
        try:
            text, stop = _ask(gateway, prompt, system_prompt, tier)
        except Exception as e:  # every provider down, etc.
            return fallback(f"llm unavailable: {type(e).__name__}: {e}")
        if not text or stop in ("max_tokens", "length"):
            return fallback(f"llm answer unusable (stop_reason={stop!r}, {len(text)} chars)")
        report = audit_narrative(text, finding, rules)
        attempts.append({"text": text, "audit": report})
        if report["ok"]:
            return {"text": text, "source": "llm", "fallback_reason": None, "attempts": attempts,
                    "audit": report, "template": plain}
        prompt = (f"{base}\n\nYOUR PREVIOUS ANSWER:\n{text}\n\nPROBLEMS:\n{correction_note(report)}")
    return fallback(f"llm answer failed the claim audit {max_attempts} times")
