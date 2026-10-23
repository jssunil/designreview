"""
Offline tests for agentkit/llm (gateway + cost ledger).    Markers: none
(Add a separate test marked `gateway_live` for one real call if you want -- it costs a fraction of a cent.)

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] A provider with no API key is skipped; tier order from config/routing.toml is respected
  [x] 429 / 503 / 529 are retried with backoff 0.5, 1, 2, 4, 4 ... s (capped) then the next provider is tried
  [x] 400 / 404 go straight to the next provider (no retry)
  [x] 401 / 402 / 403 cool the provider down so the next call skips it
  [ ] TODO: stop_reason is normalised: STOP/stop/end_turn -> end_turn; MAX_TOKENS/length -> max_tokens; SAFETY -> refusal
  [ ] TODO: A truncated (max_tokens) reply is returned but NOT cached; a refusal moves to the next provider
  [ ] TODO: The cache key includes json_mode, max_tokens and tier (changing any one is a cache miss)
  [x] The Gemini key goes in the x-goog-api-key header, never the URL; keys never appear in error text
  [ ] TODO: Anthropic: no `temperature` for claude-opus-5 / sonnet-5 / haiku-5; text taken only from "text" blocks
  [ ] TODO: The ledger records provider-reported input/output tokens and prices input and output separately
  [ ] TODO: A routing file with rpm = 0, a tier naming an unknown provider, or a misspelt key is rejected
  [ ] TODO: strip_reasoning: "<think>x</think>Answer" -> "Answer"; an unclosed <think> leaves ""; a reply that is only
      reasoning moves to the next provider; the cached text has no <think>; reply.reasoning_chars is set
For rows configured by URL or by `api`, the per-row request settings and the tier order, see
test_llm_providers.py.
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: the same prompt twice: the second answer comes from the cache and the handler sees one request
  [x] all providers fail: the error names each provider tried and its status, never a key
  [ ] TODO: a 200 reply with no text (empty choices / no candidates) moves to the next provider
Hint: LLMGateway(<routing.json in tmp_path>, transport=httpx.MockTransport(handler), sleep=list.append,
env={"GEMINI_API_KEY": "k"}) -- the handler returns httpx.Response(200, json={...}). No network.
"""

import json

import httpx
import pytest

from agentkit.llm.economics import CostLedger
from agentkit.llm.gateway import LLMGateway

KEYS = {"GEMINI_API_KEY": "gem-SECRET-1", "ANTHROPIC_API_KEY": "ant-SECRET-2"}


def _make_gateway(tmp_path, handlers, env=KEYS):
    """Gemini then Anthropic in the default tier; Anthropic first in quality. Returns (gateway, hosts, sleeps)."""
    cfg = {
        "providers": [
            {"name": "gemini", "model": "gemini-2.5-flash", "key_env": "GEMINI_API_KEY", "rpm": 60},
            {"name": "anthropic", "model": "claude-opus-5", "key_env": "ANTHROPIC_API_KEY", "rpm": 60},
        ],
        "tiers": {"default": ["gemini", "anthropic"], "quality": ["anthropic", "gemini"]},
    }
    path = tmp_path / "routing.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    hosts, sleeps = [], []

    def handler(request):
        hosts.append(request.url.host)
        for host, reply in handlers.items():
            if host in request.url.host:
                return reply(request)
        return httpx.Response(599)

    gateway = LLMGateway(path, transport=httpx.MockTransport(handler), sleep=sleeps.append, env=dict(env))
    return gateway, hosts, sleeps


def _gemini(text="from gemini"):
    return lambda req: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    })


def _anthropic(text="from claude"):
    return lambda req: httpx.Response(200, json={
        "model": "claude-opus-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 20, "output_tokens": 7},
    })


def _status(code, body="error"):
    return lambda req: httpx.Response(code, text=body)


def test_keyless_provider_is_skipped_and_tier_order_is_respected(tmp_path):
    g, hosts, _ = _make_gateway(
        tmp_path,
        {"generativelanguage": _gemini(), "api.anthropic.com": _anthropic()},
        env={"ANTHROPIC_API_KEY": "k"},
    )
    assert g.call_detailed("q").provider == "anthropic"       # gemini has no key: never asked
    assert hosts == ["api.anthropic.com"]

    g, _, _ = _make_gateway(tmp_path, {"generativelanguage": _gemini(), "api.anthropic.com": _anthropic()})
    assert g.call_detailed("q").provider == "gemini"          # default tier: gemini first
    assert g.call_detailed("q", tier="quality").provider == "anthropic"


def test_503_is_retried_with_backoff_but_400_goes_straight_to_the_next_provider(tmp_path):
    g, hosts, sleeps = _make_gateway(tmp_path, {"generativelanguage": _status(503), "api.anthropic.com": _anthropic()})
    assert g.call_detailed("q").provider == "anthropic"
    assert hosts.count("generativelanguage.googleapis.com") == 3 and sleeps == [0.5, 1.0]

    g, hosts, sleeps = _make_gateway(tmp_path, {"generativelanguage": _status(400), "api.anthropic.com": _anthropic()})
    assert g.call_detailed("q").provider == "anthropic"
    assert hosts.count("generativelanguage.googleapis.com") == 1 and sleeps == []


@pytest.mark.parametrize("code", [401, 402, 403])
def test_auth_or_quota_failure_cools_the_provider_for_later_calls(tmp_path, code):
    g, hosts, _ = _make_gateway(tmp_path, {"generativelanguage": _status(code), "api.anthropic.com": _anthropic()})
    g.call_detailed("first question")
    g.call_detailed("second question")
    assert hosts.count("generativelanguage.googleapis.com") == 1


def test_gemini_key_goes_in_a_header_and_keys_never_reach_error_text(tmp_path):
    seen = []
    g2, _, _ = _make_gateway(tmp_path, {"generativelanguage": lambda req: (seen.append(req), _gemini()(req))[1]})
    g2.call("q")
    assert "gem-SECRET-1" not in str(seen[0].url) and seen[0].headers["x-goog-api-key"] == "gem-SECRET-1"

    g, _, _ = _make_gateway(tmp_path, {
        "generativelanguage": _status(400, "bad key gem-SECRET-1"),
        "api.anthropic.com": _status(400, "ant-SECRET-2 rejected"),
    })
    with pytest.raises(RuntimeError) as err:
        g.call("q")
    assert "SECRET" not in str(err.value)
    assert "gemini" in str(err.value) and "anthropic" in str(err.value)

