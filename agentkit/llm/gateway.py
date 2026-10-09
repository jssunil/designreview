"""
Multi-provider LLM gateway: rate-aware routing across Gemini, Groq, Cerebras,
Anthropic, NVIDIA, OpenRouter and Ollama, with classified retries, a response
cache, and a cost/token ledger fed by the providers' own usage numbers.

What a caller can rely on:
- `call(prompt, ...) -> str` (first-draft API) and
  `call_detailed(prompt, ...) -> LLMReply` (text, provider, model, real
  input/output tokens, normalised stop_reason, latency).
- stop_reason is normalised to: end_turn | max_tokens | refusal | other.
  A truncated (max_tokens) or refused reply is returned but never cached.
- Errors are classified. Retryable (429, 408, 409, 5xx, 529 overloaded,
  network/timeout, empty reply) -> capped exponential backoff on the same
  provider, then a cooldown and the next provider. Not retryable (400, 401,
  403, 404, ...) -> straight to the next provider; 401/402/403 also cool the
  provider down for 5 minutes (a bad key or empty quota doesn't fix itself).
- API keys go in headers only -- never in URLs -- and every error message is
  scrubbed of key values, so a key can't leak into logs or run records.
- TLS is always verified, against the OS trust store (works behind a
  TLS-inspecting proxy). One httpx.Client per gateway (connection reuse);
  pass `transport=` (e.g. httpx.MockTransport) to test without a network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentkit.config import CONFIG_DIR, ConfigError, load_env, read_config, validation_message
from agentkit.llm.economics import CostLedger

logger = logging.getLogger("agentkit.llm.gateway")

ROUTING_PATH = CONFIG_DIR / "routing.toml"
DEFAULT_TIER_ORDER = ["gemini", "groq", "cerebras", "anthropic", "nvidia", "openrouter", "ollama"]
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
AUTH_STATUS = frozenset({401, 402, 403})  # bad key / no quota: won't fix itself this run
MAX_BACKOFF_S = 4.0
FAILURE_COOLDOWN_S = 20.0
AUTH_COOLDOWN_S = 300.0
ANTHROPIC_VERSION = "2023-06-01"
# Current Claude models that reject temperature/top_p/top_k with a 400.
_NO_SAMPLING_MODELS = re.compile(r"^claude-(opus-5|opus-4-[78]|sonnet-5|fable|mythos)", re.I)
# Models with thinking on by default: thinking spends max_tokens, so keep headroom.
_THINKING_DEFAULT_MODELS = re.compile(r"^claude-(opus-5|fable|mythos)", re.I)
MIN_TOKENS_THINKING = 16000


class ProviderSpec(BaseModel):
    """One provider row of config/routing.toml."""

    model_config = ConfigDict(extra="forbid")
    name: str
    model: str = ""
    model_env: Optional[str] = None
    key_env: Optional[str] = None
    keyless: bool = False
    rpm: int = Field(60, gt=0)
    timeout: float = Field(60.0, gt=0)

    @property
    def is_keyless(self) -> bool:
        # keyless = true, or (JSON test configs) an explicit "key_env": null
        return self.keyless or ("key_env" in self.model_fields_set and self.key_env is None)


class RoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: List[ProviderSpec]
    tiers: Dict[str, List[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _tiers_name_known_providers(self) -> "RoutingConfig":
        names = [p.name for p in self.providers]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate providers {dupes}")
        for tier, order in self.tiers.items():
            unknown = [p for p in order if p not in names]
            if unknown:
                raise ValueError(f"tier {tier!r} names unknown providers {unknown}")
        return self


def load_routing(path: Path) -> RoutingConfig:
    try:
        return RoutingConfig.model_validate(read_config(path))
    except ValidationError as e:
        raise ConfigError(validation_message(e, str(path))) from None


class ProviderFault(Exception):
    def __init__(self, provider: str, message: str, *, status: Optional[int] = None, retryable: bool = True):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status
        self.retryable = retryable


@dataclass
class LLMReply:
    text: str
    provider: str
    model: str
    stop_reason: str = "end_turn"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_s: float = 0.0
    cached: bool = False

    @property
    def complete(self) -> bool:
        return self.stop_reason == "end_turn" and bool(self.text.strip())


@dataclass
class RateState:
    """Recent calls + cooldown for one provider. Thread-safe."""

    rpm_limit: int = 60

    def __post_init__(self) -> None:
        self._calls: List[float] = []
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def available(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if now < self._cooldown_until:
                return False
            self._calls = [t for t in self._calls if now - t < 60]
            return len(self._calls) < self.rpm_limit

    def record_call(self, now: Optional[float] = None) -> None:
        with self._lock:
            self._calls.append(time.time() if now is None else now)

    def cool_down(self, seconds: float, now: Optional[float] = None) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, (time.time() if now is None else now) + seconds)


class ResponseCache:
    """Exact-match LRU cache over every parameter that changes the answer.
    Only complete (end_turn) replies are stored."""

    def __init__(self, maxsize: int = 256):
        self.maxsize = maxsize
        self._store: "OrderedDict[str, LLMReply]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(**params: Any) -> str:
        return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[LLMReply]:
        with self._lock:
            hit = self._store.get(key)
            if hit is not None:
                self._store.move_to_end(key)
            return hit

    def put(self, key: str, reply: LLMReply) -> None:
        with self._lock:
            self._store[key] = reply
            self._store.move_to_end(key)
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)


def _norm_stop(raw: Any) -> str:
    """Map each provider's finish reason onto end_turn | max_tokens | refusal | other."""
    r = str(raw or "").lower()
    if r in ("", "none", "end_turn", "stop", "stop_sequence", "eos"):
        return "end_turn"
    if r in ("max_tokens", "length", "max_output_tokens"):
        return "max_tokens"
    if r in ("refusal", "safety", "content_filter", "recitation", "blocklist", "prohibited_content", "spii"):
        return "refusal"
    return "other"  # tool_use, pause_turn, provider-specific values


class LLMGateway:
    """Resilient multi-provider LLM transport."""

    def __init__(self, routing_config_path: Optional[Path] = None, *,
                 transport: Optional[httpx.BaseTransport] = None, max_attempts: int = 3,
                 sleep: Callable[[float], None] = time.sleep, env: Optional[Dict[str, str]] = None):
        load_env()
        self._env = env if env is not None else os.environ
        self._config = load_routing(Path(routing_config_path) if routing_config_path else ROUTING_PATH)
        self._providers: Dict[str, ProviderSpec] = {p.name: p for p in self._config.providers}
        self._rate: Dict[str, RateState] = {n: RateState(rpm_limit=p.rpm) for n, p in self._providers.items()}
        # Verify TLS against the OS trust store, not httpx's bundled certifi list:
        # on machines behind a TLS-inspecting proxy only the OS store has the
        # issuing CA (seen on this workstation, 2026-09-28).
        self._client = (httpx.Client(transport=transport, timeout=60.0) if transport
                        else httpx.Client(verify=ssl.create_default_context(), timeout=60.0))
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self.cache = ResponseCache()
        self.ledger = CostLedger()
        self._adapters: Dict[str, Callable[..., LLMReply]] = {
            "gemini": self._call_gemini,
            "anthropic": self._call_anthropic,
            "ollama": self._call_ollama,
            "groq": self._openai_compat("https://api.groq.com/openai/v1/chat/completions", "groq"),
            "cerebras": self._openai_compat("https://api.cerebras.ai/v1/chat/completions", "cerebras"),
            "nvidia": self._openai_compat("https://integrate.api.nvidia.com/v1/chat/completions", "nvidia"),
            "openrouter": self._openai_compat("https://openrouter.ai/api/v1/chat/completions", "openrouter"),
        }
        if self._env.get("OPENAI_API_KEY"):
            base = self._env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
            self._adapters["openai"] = self._openai_compat(url, "openai")
            self._providers["openai"] = ProviderSpec(name="openai", model=self._env.get("OPENAI_MODEL", ""),
                                                     model_env="OPENAI_MODEL", key_env="OPENAI_API_KEY", rpm=60, timeout=120)
            self._rate["openai"] = RateState(rpm_limit=60)

    # ---- config helpers ---------------------------------------------------

    def _key(self, provider: str) -> Optional[str]:
        """The API key, "" for a keyless (local) provider, None when unset."""
        spec = self._providers.get(provider)
        if spec is not None and spec.is_keyless:
            return ""
        env_name = (spec.key_env if spec else None) or f"{provider.upper()}_API_KEY"
        return self._env.get(env_name) or None

    def model_for(self, provider: str) -> str:
        spec = self._providers.get(provider)
        if spec is None:
            return ""
        return (self._env.get(spec.model_env) if spec.model_env else None) or spec.model

    def _timeout(self, provider: str) -> float:
        spec = self._providers.get(provider)
        return spec.timeout if spec else 60.0

    def candidate_order(self, tier: str) -> List[str]:
        tiers = self._config.tiers
        order = list(tiers.get(tier) or tiers.get("default") or DEFAULT_TIER_ORDER)
        if "openai" in self._adapters and "openai" not in order:
            order.insert(0, "openai")
        return order

    def available_providers(self, tier: str = "default") -> List[str]:
        return [p for p in self.candidate_order(tier) if p in self._adapters and self._key(p) is not None]

    def _scrub(self, text: str) -> str:
        for name in self._providers:
            k = self._key(name)
            if k:
                text = text.replace(k, "***")
        return text

    # ---- public API ---------------------------------------------------------

    def call(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.1,
             max_tokens: int = 4096, json_mode: bool = False, tier: str = "default") -> str:
        """First-draft API: returns only the text."""
        return self.call_detailed(prompt, system_prompt=system_prompt, temperature=temperature,
                                  max_tokens=max_tokens, json_mode=json_mode, tier=tier).text

    def call_detailed(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.1,
                      max_tokens: int = 4096, json_mode: bool = False, tier: str = "default") -> LLMReply:
        ckey = ResponseCache.key(prompt=prompt, system=system_prompt, temperature=temperature,
                                 max_tokens=max_tokens, json_mode=json_mode, tier=tier)
        hit = self.cache.get(ckey)
        if hit is not None:
            logger.info("cache_hit tier=%s provider=%s", tier, hit.provider)
            return LLMReply(**{**hit.__dict__, "cached": True})

        errors: List[str] = []
        for provider in self.candidate_order(tier):
            adapter = self._adapters.get(provider)
            key = self._key(provider)
            if adapter is None or key is None:
                continue
            state = self._rate.setdefault(provider, RateState())
            if not state.available():
                errors.append(f"{provider}: skipped (cooling down / rate-limited)")
                continue
            model = self.model_for(provider)
            for attempt in range(self.max_attempts):
                state.record_call()
                t0 = time.time()
                try:
                    reply = adapter(key, model, prompt, system_prompt, temperature, max_tokens, json_mode)
                except ProviderFault as fault:
                    msg = self._scrub(str(fault))
                    if fault.retryable and attempt < self.max_attempts - 1:
                        self._sleep(min(0.5 * 2 ** attempt, MAX_BACKOFF_S))
                        continue
                    errors.append(msg)
                    if fault.status in AUTH_STATUS:
                        state.cool_down(AUTH_COOLDOWN_S)
                    elif fault.retryable:
                        state.cool_down(FAILURE_COOLDOWN_S)
                    break
                reply.latency_s = round(time.time() - t0, 3)
                self.ledger.record(provider, model, prompt, reply.text, reply.latency_s,
                                   input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                                   stop_reason=reply.stop_reason)
                if reply.stop_reason == "refusal" or not reply.text.strip():
                    errors.append(f"{provider}: {reply.stop_reason} with {len(reply.text)} chars")
                    break  # try the next provider
                if reply.complete:
                    self.cache.put(ckey, reply)
                return reply
        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors) or 'no provider has a key configured'}")

    # ---- HTTP -----------------------------------------------------------------

    def _post(self, provider: str, url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self._client.post(url, headers=headers, json=body, timeout=self._timeout(provider))
        except httpx.TimeoutException as e:
            raise ProviderFault(provider, f"timeout ({type(e).__name__})", retryable=True) from None
        except httpx.TransportError as e:
            raise ProviderFault(provider, f"network error ({type(e).__name__})", retryable=True) from None
        if resp.status_code != 200:
            raise ProviderFault(provider, f"HTTP {resp.status_code}: {self._scrub(resp.text[:300])}",
                                status=resp.status_code, retryable=resp.status_code in RETRYABLE_STATUS)
        try:
            return resp.json()
        except ValueError:
            raise ProviderFault(provider, "non-JSON response body", status=200, retryable=True) from None

    # ---- adapters -------------------------------------------------------------

    def _call_gemini(self, key, model, prompt, system_prompt, temp, max_tok, json_mode) -> LLMReply:
        model = model or "gemini-2.5-flash"
        body: Dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": temp, "maxOutputTokens": max_tok}}
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = self._post("gemini", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          {"x-goog-api-key": key, "Content-Type": "application/json"}, body)
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            return LLMReply("", "gemini", model, "refusal" if block else "other")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        out = usage.get("candidatesTokenCount")
        if out is not None and usage.get("thoughtsTokenCount"):
            out += usage["thoughtsTokenCount"]
        return LLMReply(text, "gemini", model, _norm_stop(cands[0].get("finishReason")),
                        usage.get("promptTokenCount"), out)

    def _call_anthropic(self, key, model, prompt, system_prompt, temp, max_tok, json_mode) -> LLMReply:
        model = model or "claude-opus-5"
        body: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}],
                                "max_tokens": max(max_tok, MIN_TOKENS_THINKING) if _THINKING_DEFAULT_MODELS.match(model)
                                else max_tok}
        if system_prompt:
            body["system"] = system_prompt
        if not _NO_SAMPLING_MODELS.match(model):
            body["temperature"] = temp
        if json_mode:
            body["system"] = (body.get("system", "") + "\n\nRespond with a single JSON object and nothing else.").strip()
        data = self._post("anthropic", "https://api.anthropic.com/v1/messages",
                          {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                           "content-type": "application/json"}, body)
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        usage = data.get("usage") or {}
        tin = usage.get("input_tokens")
        if tin is not None:
            tin += (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
        return LLMReply(text, "anthropic", data.get("model", model), _norm_stop(data.get("stop_reason")),
                        tin, usage.get("output_tokens"))

    def _openai_compat(self, url: str, provider_name: Optional[str] = None) -> Callable[..., LLMReply]:
        host = httpx.URL(url).host
        known = {"api.groq.com": "groq", "api.cerebras.ai": "cerebras", "integrate.api.nvidia.com": "nvidia",
                 "openrouter.ai": "openrouter"}
        provider = provider_name or known.get(host, "openai")

        def call(key, model, prompt, system_prompt, temp, max_tok, json_mode) -> LLMReply:
            messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + \
                       [{"role": "user", "content": prompt}]
            body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temp, "max_tokens": max_tok}
            if json_mode and provider in ("groq", "cerebras"):
                body["response_format"] = {"type": "json_object"}
            data = self._post(provider, url, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, body)
            choices = data.get("choices") or []
            if not choices:
                return LLMReply("", provider, model, "other")
            text = (choices[0].get("message") or {}).get("content") or ""
            usage = data.get("usage") or {}
            return LLMReply(text, provider, data.get("model", model), _norm_stop(choices[0].get("finish_reason")),
                            usage.get("prompt_tokens"), usage.get("completion_tokens"))

        return call

    def _call_ollama(self, key, model, prompt, system_prompt, temp, max_tok, json_mode) -> LLMReply:
        base = self._env.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + \
                   [{"role": "user", "content": prompt}]
        body: Dict[str, Any] = {"model": model or "gemma:latest", "messages": messages, "stream": False,
                                "options": {"temperature": temp, "num_predict": max_tok}}
        if json_mode:
            body["format"] = "json"
        data = self._post("ollama", f"{base}/api/chat", {"Content-Type": "application/json"}, body)
        return LLMReply((data.get("message") or {}).get("content") or "", "ollama", model,
                        _norm_stop(data.get("done_reason")), data.get("prompt_eval_count"), data.get("eval_count"))

    def close(self) -> None:
        self._client.close()


if __name__ == "__main__":
    import sys

    gw = LLMGateway()
    tier = sys.argv[1] if len(sys.argv) > 1 else "default"
    print(f"Providers with keys (tier {tier}): {gw.available_providers(tier)}")
    reply = gw.call_detailed("Respond with exactly: GATEWAY_OK", tier=tier, max_tokens=64)
    print(f"{reply.provider}/{reply.model}: {reply.text.strip()!r} stop={reply.stop_reason} "
          f"in={reply.input_tokens} out={reply.output_tokens}")
    print("Ledger:", gw.ledger.summary())
