"""
Offline tests for the LLM provider rows in config/routing.toml: endpoints configured by URL, second rows on
the native Anthropic / Gemini APIs, per-row request settings, the tier order, the record of which model
answered, and how .env values reach a batch's child processes (agentkit/llm/gateway.py,
agentkit/llm/models_check.py, agentkit/answer/narrator.py, agentkit/config.py).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/explainers/agentkit_llm.md and config.md; docs/CHANGES.md entries of 2026-10-14;
tests/MANUAL_TEST_LOG.md rows A-LIVE-05 to A-LIVE-10. All of these features exist today.

Use cases to cover:
  Rows configured by URL (ProviderSpec.url / url_env, chat_url):
  [x] chat_url("https://api.deepseek.com") and chat_url("https://api.moonshot.ai/v1/") both end in
      "/chat/completions"; a full ".../chat/completions" URL is kept as it is
  [x] a row with url = "https://api.deepseek.com" posts to that endpoint with "Authorization: Bearer <key>"
  [x] url_env set in env overrides the row's url
  [x] a keyless row (LM Studio) sends no Authorization header
  [x] a row whose key variable is not set is not in available_providers(); the next row answers

  Second rows on a native API (ProviderSpec.api = "anthropic" | "gemini"):
  [ ] TODO: a row named "anthropic_haiku" with api = "anthropic" posts to api.anthropic.com, and the reply's
      provider is "anthropic_haiku" (not "anthropic")
  [ ] TODO: a row with api = "gemini" posts to .../models/<model>:generateContent with the x-goog-api-key header
  [ ] TODO: the two Anthropic rows cool down separately: a 529 on the Haiku row does not stop the Sonnet row
  [ ] TODO: api = "cohere" (not a known API) -> load_routing raises a config error that names "api"

  Request settings for each row:
  [ ] TODO: temperature = 1 on a row replaces the caller's 0.1 in the request body; a row without it keeps 0.1
  [ ] TODO: max_tokens_field = "max_completion_tokens" sends that key and no "max_tokens"
  [ ] TODO: reasoning_effort = "low" is sent as is; a row without it sends no reasoning_effort
  [ ] TODO: a Claude 5.x model (claude-haiku-5-5, claude-sonnet-5-5) gets no "temperature" in the body

  The shipped tier order (config/routing.toml):
  [x] fast starts openai_luna, deepseek_flash, moonshot, gemini_lite, anthropic_haiku
  [x] quality and default start openai_sol, deepseek, moonshot, gemini, anthropic
  [x] lmstudio, then ollama, are the last two rows of each tier

  Which model answered:
  [x] compose_answer with a fake gateway whose call_detailed returns
      LLMReply("...", "deepseek_flash", "deepseek-flash", "end_turn", 10, 3) -> the result has
      provider "deepseek_flash" and model "deepseek-flash", and so does its first attempt
  [ ] TODO: models_check.check(gateway) on a faked model list without the configured model -> status "RETIRED"

  .env values in child processes (agentkit.config, AGENTKIT_DOTENV_KEYS):
  [ ] TODO: after load_env(<tmp .env with OPENAI_API_KEY>), from_process("OPENAI_API_KEY") is None
  [ ] TODO: simulate a child: keep os.environ, clear config._FROM_DOTENV, call load_env() again ->
      from_process("OPENAI_API_KEY") is still None, and candidate_order("default") does not start with "openai"
  [ ] TODO: a different value in the process (a runner-injected key) -> from_process gives it, and "openai" is first

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: models_check.check on a row with url = "https://api.deepseek.com/chat/completions" asks
      "https://api.deepseek.com/models" (it drops "/chat/completions"; base_url() itself keeps it)
  [ ] TODO: a keyless row is in available_providers() with no key variable set
  [ ] TODO: url_env names a variable that is not set -> the row's url is used
Hint: build a routing file in tmp_path (JSON is accepted) and use
LLMGateway(<file>, transport=httpx.MockTransport(handler), sleep=lambda s: None, env={...}); read the
request bodies with json.loads(request.content). For the .env cases, use monkeypatch to set
agentkit.config._FROM_DOTENV to {} and to delete the variables, and remove AGENTKIT_DOTENV_KEYS after the test.
"""

import json

import httpx
import pytest

import agentkit.config as config
from agentkit.answer import compose_answer
from agentkit.answer.claim_audit import ClaimRules
from agentkit.config import from_process, load_env
from agentkit.llm.gateway import ROUTING_PATH, LLMGateway, LLMReply, chat_url, load_routing
from agentkit.llm.models_check import check

DEEPSEEK = {
    "name": "deepseek",
    "model": "deepseek-v4-pro",
    "key_env": "DEEPSEEK_API_KEY",
    "url": "https://api.deepseek.com",
    "url_env": "DEEPSEEK_API_URL",
}
LMSTUDIO = {
    "name": "lmstudio",
    "model": "google/gemma-4-e4b",
    "keyless": True,
    "url": "http://127.0.0.1:1235/v1",
}


def _ok(text="hi"):
    return lambda req: httpx.Response(200, json={
        "model": json.loads(req.content)["model"],
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 2},
    })


def _gateway(tmp_path, providers, tiers, env):
    path = tmp_path / "routing.json"
    path.write_text(json.dumps({"providers": providers, "tiers": tiers}), encoding="utf-8")
    seen = []

    def handler(req):
        seen.append(req)
        return _ok("answer")(req)

    return LLMGateway(path, transport=httpx.MockTransport(handler), sleep=lambda s: None, env=env), seen


def test_url_row_posts_to_chat_completions_with_a_bearer_key_and_url_env_wins(tmp_path):
    assert chat_url("https://api.moonshot.ai/v1/") == "https://api.moonshot.ai/v1/chat/completions"
    assert chat_url("https://api.deepseek.com/chat/completions") == "https://api.deepseek.com/chat/completions"

    gw, seen = _gateway(tmp_path, [DEEPSEEK], {"default": ["deepseek"]}, {"DEEPSEEK_API_KEY": "ds-key"})
    gw.call("q")
    assert str(seen[0].url) == "https://api.deepseek.com/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer ds-key"

    gw, seen = _gateway(
        tmp_path,
        [DEEPSEEK],
        {"default": ["deepseek"]},
        {"DEEPSEEK_API_KEY": "ds-key", "DEEPSEEK_API_URL": "https://proxy.example/v1"},
    )
    gw.call("q")
    assert str(seen[0].url) == "https://proxy.example/v1/chat/completions"


def test_row_without_its_key_is_skipped_and_a_keyless_row_sends_no_authorization(tmp_path):
    gw, seen = _gateway(tmp_path, [DEEPSEEK, LMSTUDIO], {"default": ["deepseek", "lmstudio"]}, {})
    assert gw.available_providers() == ["lmstudio"]
    assert gw.call_detailed("q").provider == "lmstudio"
    assert "authorization" not in seen[0].headers


def test_shipped_tier_order():
    tiers = load_routing(ROUTING_PATH).tiers
    assert tiers["fast"][:5] == ["openai_luna", "deepseek_flash", "moonshot", "gemini_lite", "anthropic_haiku"]
    for name in ("quality", "default"):
        assert tiers[name][:5] == ["openai_sol", "deepseek", "moonshot", "gemini", "anthropic"]
    for name in ("fast", "quality", "default"):
        assert tiers[name][-2:] == ["lmstudio", "ollama"]


def test_the_answer_records_which_provider_and_model_wrote_it():
    class FakeGateway:
        def call_detailed(self, prompt, **kw):
            return LLMReply("Plain answer.", "deepseek_flash", "deepseek-flash", "end_turn", 10, 3)

    out = compose_answer("q", {}, gateway=FakeGateway(), rules=ClaimRules(), template=lambda f: "tpl", tiers=("fast",))
    assert (out["source"], out["provider"], out["model"]) == ("llm", "deepseek_flash", "deepseek-flash")
    assert (out["attempts"][0]["provider"], out["attempts"][0]["model"]) == ("deepseek_flash", "deepseek-flash")

