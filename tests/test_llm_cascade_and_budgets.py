"""
Offline tests for how LLM calls are spent: an audit-gated cascade (cheap model first), prompt-cache
friendly prompts, agreement across sampled drafts, finding-path citations, retired-model detection, and
per-node / per-run budgets (agentkit/llm/*, agentkit/answer/*, agentkit/graph/engine.py).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/RESEARCH_GAP_ANALYSIS.md, gaps I1, I2, I3, I4, I5, I8.
Most items test features that do not exist today: write the test, and mark it
@pytest.mark.xfail(strict=True, reason="I#") (or skip) until the feature lands.

Use cases to cover:
  Works today (the base the cascade builds on):
  [ ] TODO: compose_answer with a fake gateway whose first draft passes the audit -> exactly 1 LLM call,
      answer_source "llm"
  [x] first draft invents "2.5 mm", second is clean -> exactly 2 calls, the rewrite prompt names "3.0 mm"
  [x] the cost ledger adds up input and output tokens per provider and the summary cost matches
      the price table

  Audit-gated cascade (I1, built 2026-10-14: compose_answer(tiers=...)):
  [x] the cheap tier's draft passes the audit -> the quality tier is never called
  [ ] TODO: the cheap draft fails the audit -> the quality tier is called; the template only if both fail
  [ ] TODO: the ledger shows which tier answered, so cost per passing answer can be reported

  Prompt caching (I2, built 2026-10-14: stable system prompt first; gateway ANTHROPIC_CACHE_MIN_CHARS):
  [ ] TODO: two narrations with different findings send byte-identical prefixes (system prompt + instructions);
      the finding comes after the cache breakpoint
  [ ] TODO: an Anthropic request with a long enough system prompt carries a cache_control block;
      a short one does not

  Sampled agreement (I3, not built yet; eval-time only):
  [ ] TODO: 3 drafts, 2 state the same release verdict -> agreement = 2/3

  Finding-path citations (I4, built 2026-10-15: claim_audit.check_citations / strip_citations, "[@path]"):
  [ ] TODO: a sentence tagged [@revision_delta.change_notes[1]] whose number is in that value -> passes
  [ ] TODO: a tag to a path that is not in the finding, or whose value lacks the sentence's number -> audit fails,
      and correction_note says "Wrong citation"
  [ ] TODO: two tags in one sentence pool their values
  [ ] TODO: tags are removed from the text the user sees (compose_answer's text); the attempt keeps them
  [ ] TODO: a plain "[1]" in the prose is not a citation, and the "[1]" inside a path is not read as a quantity

  Model drift (I5, built 2026-10-14: agentkit.llm.models_check.check / model_ids):
  [ ] TODO: a provider model list (faked) that no longer has the configured model -> preflight reports it

  Reasoning models (2026-10-14; see also test_llm_providers.py):
  [ ] TODO: a quality-tier reply cut off at max_tokens is not used: the next step is the template, and the ledger
      still records the call

  Budgets (I8, built 2026-10-14: registry.action(timeout_s=), config.llm_budget_usd, compose_answer(max_cost_usd=)):
  [ ] TODO: an action that sleeps past its timeout_s ends FAILED with the reason; the other branch still
      finishes (requires="settled")
  [x] a run whose ledger passes max_cost_usd stops calling the LLM and answers with the template,
      recording why

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: price_for("openai", "gpt-4o-mini") uses the "provider:model" price; an unknown model uses the provider's
      price; an unknown provider uses (1.0, 1.0)
  [ ] TODO: estimate_tokens("") == 0 and estimate_tokens("one two three") == 3 (about 1.3 tokens per word, at least 1)
  [ ] TODO: max_cost_usd=0 with a fake gateway: no LLM call is made and the answer is the template
Hint: the narrator only needs an object with call(prompt, **kw) -> str (or call_detailed). For the
gateway itself, use LLMGateway(<routing file>, transport=httpx.MockTransport(handler), sleep=list.append,
env={...}) and inspect the request bodies the handler receives.
"""

from typing import Any, Dict, List

import pytest

from agentkit.answer import audit_narrative, compose_answer
from agentkit.graph import RESOLVED, NodeResult
from agentkit.llm.economics import CostLedger, price_for
from agentkit.llm.gateway import LLMGateway
from packs.designreview.claims import CLAIM_RULES
from packs.designreview.finding import build_finding
from packs.designreview.templates import plain_rendering


def _finding() -> Dict[str, Any]:
    notes = ["Rev A: prototype", "Rev B: bend radius 2.0 mm", "Rev C: bend radius 3.0 mm"]
    rows = [{"id": f"v{i}", "version_number": i, "commit_message": n} for i, n in enumerate(notes, 1)]
    results = {
        "design_file": NodeResult(RESOLVED, data={"id": "f1", "name": "Tray"}),
        "revisions": NodeResult(RESOLVED, data={"data": rows}),
    }
    f = build_finding(results, {"file_id": "f1", "prompt": "What changed between rev B and rev C?"})
    f["release_gate"] = {"outcome": "answered", "ready": False, "critical_open": 1, "blockers": []}
    return f


class _Scripted:
    """A fake gateway: scripted replies per tier; records (tier, prompt) of every call."""

    def __init__(self, by_tier: Dict[str, List[str]], spent: float = 0.0):
        self.by_tier = {k: list(v) for k, v in by_tier.items()}
        self.calls: List[tuple] = []
        self.ledger = type("Ledger", (), {"total_cost": lambda _self: spent})()

    def call(self, prompt, system_prompt="", tier="default", **kw):
        self.calls.append((tier, prompt))
        return self.by_tier[tier].pop(0)


def _compose(gw, **kw):
    return compose_answer("q", _finding(), gateway=gw, rules=CLAIM_RULES, template=plain_rendering, **kw)


def test_cheap_draft_that_passes_the_audit_ends_the_cascade():
    good = plain_rendering(_finding())            # the template text passes its own audit
    gw = _Scripted({"fast": [good], "quality": [good]})
    out = _compose(gw, tiers=("fast", "quality"))
    assert out["source"] == "llm" and out["tier"] == "fast"
    assert [tier for tier, _ in gw.calls] == ["fast"]   # the quality tier is never called


def test_an_invented_number_is_rewritten_with_the_audit_note():
    good = plain_rendering(_finding())
    gw = _Scripted({"quality": ["The bend radius is now 2.5 mm.", good]})
    out = _compose(gw)
    assert out["source"] == "llm" and len(gw.calls) == 2
    rewrite_prompt = gw.calls[1][1]
    assert "2.5 mm" in rewrite_prompt and "3.0 mm" in rewrite_prompt


def test_ledger_adds_tokens_and_prices_input_and_output_separately():
    ledger = CostLedger()
    ledger.record("openai", "gpt-4o-mini", input_tokens=1000, output_tokens=200)
    ledger.record("openai", "gpt-4o-mini", input_tokens=500, output_tokens=100)
    pin, pout = price_for("openai", "gpt-4o-mini")
    s = ledger.summary()
    assert (s["calls"], s["input_tokens"], s["output_tokens"]) == (2, 1500, 300)
    assert s["cost_usd_estimated"] == pytest.approx(1500 / 1e6 * pin + 300 / 1e6 * pout, abs=1e-6)


def test_spent_budget_answers_with_the_template_and_makes_no_call():
    gw = _Scripted({"fast": ["x"], "quality": ["x"]}, spent=0.30)
    out = _compose(gw, tiers=("fast", "quality"), max_cost_usd=0.25)
    assert out["source"] == "template" and gw.calls == []
    assert "budget" in out["fallback_reason"]

