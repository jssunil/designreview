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
- Reasoning a model writes into its answer text (<think>...</think>, as some
  open reasoning models do) is removed before anything else sees the reply:
  the claim audit, the cache, the ledger. reply.reasoning_chars says how much
  was removed. A reply that is only reasoning counts as empty.
- Errors are classified. Retryable (429, 408, 409, 5xx, 529 overloaded,
  network/timeout, empty reply) -> capped exponential backoff on the same
  provider, then a cooldown and the next provider. Not retryable (400, 401,
  403, 404, ...) -> straight to the next provider; 401/402/403 also cool the
  provider down for 5 minutes (a bad key or empty quota doesn't fix itself).
  A 429 whose body says "budget_exhausted" is a spent token budget, not a rate
  limit (the platform's LLM endpoint answers it so): not retried, and the
  provider stays down for the rest of the run, so the answer falls back at once.
- A row can have more keys (`key_envs`). Each key has its own rate window and
  cool-down; calls start at the next key in turn (round-robin). A rate limit,
  a bad key or a spent budget cools that key only and moves to the next key;
  the row is skipped only when all its keys are cooling. A row can also set
  its own cool-downs (`cooldown_s`, `auth_cooldown_s`). Records name the key
  index, never the key.
- API keys go in headers only -- never in URLs -- and every error message is
  scrubbed of key values, so a key can't leak into logs or run records.
- Model-visible means logged: each attempt (answered, cache hit or failed)
  goes into ledger.calls with the exact system prompt, prompt and reply, keys
  scrubbed; the harness writes them to llm_calls.jsonl in the run folder.
- TLS is always verified, against the OS trust store (works behind a
  TLS-inspecting proxy). One httpx.Client per gateway (connection reuse);
  pass `transport=` (e.g. httpx.MockTransport) to test without a network.
"""

from __future__ import annotations

import functools
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
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import httpx

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentkit.config import CONFIG_DIR, ConfigError, from_process, load_env, read_config, validation_message
from agentkit.llm.economics import CostLedger, LLMCall
from agentkit.telemetry.spans import add_event, span
from agentkit.transport.wire import refuse_if_offline

logger = logging.getLogger("agentkit.llm.gateway")

ROUTING_PATH = CONFIG_DIR / "routing.toml"
DEFAULT_TIER_ORDER = ["gemini", "groq", "cerebras", "anthropic", "nvidia", "openrouter", "ollama"]
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
AUTH_STATUS = frozenset({401, 402, 403})  # bad key / no quota: won't fix itself this run
MAX_BACKOFF_S = 4.0
FAILURE_COOLDOWN_S = 20.0
AUTH_COOLDOWN_S = 300.0
BUDGET_COOLDOWN_S = 24 * 3600.0  # a spent token budget: the rest of the run
BUDGET_EXHAUSTED = "budget_exhausted"
ANTHROPIC_VERSION = "2023-06-01"
# Current Claude models that reject temperature/top_p/top_k with a 400.
_NO_SAMPLING_MODELS = re.compile(r"^claude-(opus-5|opus-4-[78]|sonnet-5|haiku-5|fable|mythos)", re.I)
# Models with thinking on by default: thinking spends max_tokens, so keep headroom.
_THINKING_DEFAULT_MODELS = re.compile(r"^claude-(opus-5|fable|mythos)", re.I)
MIN_TOKENS_THINKING = 16000
# About the provider's smallest cacheable prompt (~1024 tokens at ~4 characters per token).
ANTHROPIC_CACHE_MIN_CHARS = 4096


class ProviderSpec(BaseModel):
    """One provider row of config/routing.toml."""

    model_config = ConfigDict(extra="forbid")
    name: str
    model: str = ""
    model_env: Optional[str] = None
    key_env: Optional[str] = None
    # More keys for this row, used in turn after key_env; each has its own rate window and cool-down.
    key_envs: List[str] = Field(default_factory=list)
    keyless: bool = False
    rpm: int = Field(60, gt=0)  # for each key
    # This row's cool-downs in seconds (unset: FAILURE_COOLDOWN_S, AUTH_COOLDOWN_S).
    cooldown_s: Optional[float] = Field(None, gt=0)
    auth_cooldown_s: Optional[float] = Field(None, gt=0)
    timeout: float = Field(60.0, gt=0)
    # Any OpenAI-compatible endpoint: `url` (base, e.g. https://host/v1, or the full .../chat/completions);
    # `url_env` (if set in the environment) overrides it. Built-in providers ignore both.
    url: Optional[str] = None
    url_env: Optional[str] = None
    # Some models accept one temperature only (Moonshot's kimi-k2.6: 1): sent instead of the caller's.
    temperature: Optional[float] = Field(None, ge=0)
    # Reasoning models (OpenAI gpt-6-*): the output cap is "max_completion_tokens" and it includes the
    # reasoning tokens; `reasoning_effort` is sent as is (gpt-6-luna takes "none", gpt-6.1-sol "low" at least).
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    reasoning_effort: Optional[str] = None
    # A second row on a native API (another model, its own tier place, rate state and cool-down):
    # api = "anthropic" or "gemini" uses that provider's adapter under this row's name.
    api: Optional[Literal["anthropic", "gemini"]] = None

    @property
    def is_keyless(self) -> bool:
        # keyless = true, or (JSON test configs) an explicit "key_env": null
        return self.keyless or ("key_env" in self.model_fields_set and self.key_env is None)


def chat_url(base: str) -> str:
    """https://host/v1 -> https://host/v1/chat/completions (a full endpoint is kept as is)."""
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


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
    def __init__(self, provider: str, message: str, *, status: Optional[int] = None, retryable: bool = True,
                 budget_exhausted: bool = False):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status
        self.retryable = retryable
        self.budget_exhausted = budget_exhausted


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
    reasoning_chars: int = 0  # reasoning removed from the text (strip_reasoning)
    key_index: int = 0  # which of the row's keys answered (0 = key_env)

    @property
    def complete(self) -> bool:
        return self.stop_reason == "end_turn" and bool(self.text.strip())


@dataclass
class RateState:
    """Recent calls + cooldown for one key of one provider. Thread-safe."""

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


_REASONING_TAGS = r"(?:think|thinking|reasoning)"
_REASONING_BLOCK = re.compile(r"<(" + _REASONING_TAGS + r")>.*?</\1>\s*", re.S | re.I)
_REASONING_OPEN = re.compile(r"<" + _REASONING_TAGS + r">", re.I)
_REASONING_CLOSE = re.compile(r"</" + _REASONING_TAGS + r">", re.I)


def strip_reasoning(text: str) -> Tuple[str, int]:
    """(answer, chars removed). Removes <think>...</think> blocks (and
    <thinking>, <reasoning>); an unclosed opening tag means the reply was cut
    off mid-reasoning, so everything from it on goes; a closing tag with no
    opening one (some servers drop the opener) means everything before it was
    reasoning."""
    if not text or "<" not in text:
        return text, 0
    out = _REASONING_BLOCK.sub("", text)
    m = _REASONING_OPEN.search(out)
    if m:
        out = out[:m.start()]
    m = _REASONING_CLOSE.search(out)
    if m:
        out = out[m.end():]
    if out == text:
        return text, 0
    out = out.strip()
    return out, len(text) - len(out)


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
        self._rate: Dict[Tuple[str, int], RateState] = {}  # (provider, key index) -> state
        self._next_key: Dict[str, int] = {}  # round-robin position of each provider
        self._lock = threading.Lock()
        for p in self._config.providers:
            unset = [n for n in p.key_envs if not self._env.get(n)]
            if unset:  # names only, never values
                logger.warning("provider %s: key_envs not set, ignored: %s", p.name, ", ".join(unset))
        # Verify TLS against the OS trust store, not httpx's bundled certifi list:
        # on machines behind a TLS-inspecting proxy only the OS store has the
        # issuing CA (seen on this workstation, 2026-09-28).
        self._real_network = transport is None
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
        native = {"anthropic": self._call_anthropic, "gemini": self._call_gemini}
        for p in self._config.providers:
            base = self.base_url(p.name)
            if p.name in self._adapters:
                continue
            if p.api:
                self._adapters[p.name] = functools.partial(native[p.api], provider=p.name)
            elif base:
                self._adapters[p.name] = self._openai_compat(chat_url(base), p.name)
        # The official runner injects OPENAI_* into the process: that endpoint then goes first in every
        # tier. The same key copied into .env only makes "openai" available to tiers that name it.
        key = self._env.get("OPENAI_API_KEY")
        self._openai_first = bool(key) and not (self._env is os.environ and from_process("OPENAI_API_KEY") is None)
        if self._env.get("OPENAI_API_KEY"):
            base = self._env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions" if not base.endswith("/chat/completions") else base
            self._adapters["openai"] = self._openai_compat(url, "openai")
            self._providers["openai"] = ProviderSpec(name="openai", model=self._env.get("OPENAI_MODEL", ""),
                                                     model_env="OPENAI_MODEL", key_env="OPENAI_API_KEY", rpm=60, timeout=120)

    # ---- config helpers ---------------------------------------------------

    def _keys(self, provider: str) -> List[str]:
        """The row's API keys in order (key_env, then key_envs that are set); [""] for a keyless row."""
        spec = self._providers.get(provider)
        if spec is not None and spec.is_keyless:
            return [""]
        names = [(spec.key_env if spec else None) or f"{provider.upper()}_API_KEY"] + (spec.key_envs if spec else [])
        keys: List[str] = []
        for name in names:
            value = self._env.get(name)
            if value and value not in keys:
                keys.append(value)
        return keys

    def _key(self, provider: str) -> Optional[str]:
        """The first API key, "" for a keyless (local) provider, None when unset."""
        keys = self._keys(provider)
        return keys[0] if keys else None

    def _state(self, provider: str, index: int = 0) -> RateState:
        with self._lock:
            state = self._rate.get((provider, index))
            if state is None:
                spec = self._providers.get(provider)
                state = self._rate[(provider, index)] = RateState(rpm_limit=spec.rpm if spec else 60)
            return state

    def _key_order(self, provider: str, n: int) -> List[int]:
        """Round-robin: each call starts at the next key, so the load spreads over all keys."""
        with self._lock:
            start = self._next_key.get(provider, 0) % n
            self._next_key[provider] = start + 1
        return [(start + i) % n for i in range(n)]

    def _cooldown(self, provider: str, *, auth: bool = False) -> float:
        spec = self._providers.get(provider)
        own = (spec.auth_cooldown_s if auth else spec.cooldown_s) if spec else None
        return own or (AUTH_COOLDOWN_S if auth else FAILURE_COOLDOWN_S)

    def base_url(self, provider: str) -> str:
        """The configured OpenAI-compatible base URL of a routing.toml row ("" when it has none)."""
        spec = self._providers.get(provider)
        if spec is None:
            return ""
        return ((self._env.get(spec.url_env) if spec.url_env else None) or spec.url or "").rstrip("/")

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
        if "openai" in self._adapters and "openai" not in order and self._openai_first:
            order.insert(0, "openai")
        return order

    def available_providers(self, tier: str = "default") -> List[str]:
        return [p for p in self.candidate_order(tier) if p in self._adapters and self._key(p) is not None]

    def _scrub(self, text: str) -> str:
        for name in self._providers:
            for k in self._keys(name):
                if k:
                    text = text.replace(k, "***")
        return text

    def _log_call(self, tier: str, provider: str, model: str, system_prompt: Optional[str], prompt: str,
                  **fields: Any) -> None:
        """Record what the model saw and said for one attempt, with every key value scrubbed."""
        scrub = lambda v: self._scrub(v) if isinstance(v, str) else v  # noqa: E731
        self.ledger.log_call(LLMCall(tier=tier, provider=provider, model=model or "",
                                     system=scrub(system_prompt or ""), prompt=scrub(prompt),
                                     **{k: scrub(v) for k, v in fields.items()}))

    # ---- public API ---------------------------------------------------------

    def call(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.1,
             max_tokens: int = 4096, json_mode: bool = False, tier: str = "default") -> str:
        """First-draft API: returns only the text."""
        return self.call_detailed(prompt, system_prompt=system_prompt, temperature=temperature,
                                  max_tokens=max_tokens, json_mode=json_mode, tier=tier).text

    def call_detailed(self, prompt: str, system_prompt: Optional[str] = None, temperature: float = 0.1,
                      max_tokens: int = 4096, json_mode: bool = False, tier: str = "default") -> LLMReply:
        """One "llm" telemetry span per call: provider, model, real token counts,
        stop_reason, cache hit -- never the prompt or a key."""
        with span("llm", tier, tier=tier, json_mode=json_mode, max_tokens=max_tokens) as s:
            reply = self._call_detailed(prompt, system_prompt, temperature, max_tokens, json_mode, tier)
            s.set(provider=reply.provider, model=reply.model, input_tokens=reply.input_tokens,
                  output_tokens=reply.output_tokens, stop_reason=reply.stop_reason, cached=reply.cached,
                  latency_s=reply.latency_s, reasoning_chars=reply.reasoning_chars, ok=reply.complete,
                  key_index=reply.key_index)
            return reply

    def _call_detailed(self, prompt: str, system_prompt: Optional[str], temperature: float, max_tokens: int,
                       json_mode: bool, tier: str) -> LLMReply:
        if self._real_network:  # an injected transport (tests) is not the network
            refuse_if_offline("LLM call")
        ckey = ResponseCache.key(prompt=prompt, system=system_prompt, temperature=temperature,
                                 max_tokens=max_tokens, json_mode=json_mode, tier=tier)
        hit = self.cache.get(ckey)
        if hit is not None:
            logger.info("cache_hit tier=%s provider=%s", tier, hit.provider)
            self._log_call(tier, hit.provider, hit.model, system_prompt, prompt, reply=hit.text,
                           stop_reason=hit.stop_reason, cached=True)
            return LLMReply(**{**hit.__dict__, "cached": True})

        errors: List[str] = []
        for provider in self.candidate_order(tier):
            adapter = self._adapters.get(provider)
            keys = self._keys(provider)
            if adapter is None or not keys:
                continue
            model = self.model_for(provider)
            for index in self._key_order(provider, len(keys)):
                if not self._state(provider, index).available():
                    label = provider if len(keys) == 1 else f"{provider} key {index + 1}"
                    errors.append(f"{label}: skipped (cooling down / rate-limited)")
                    continue
                reply, next_key = self._try_key(adapter, provider, keys, index, model, prompt, system_prompt,
                                                temperature, max_tokens, json_mode, tier, ckey, errors)
                if reply is not None:
                    return reply
                if not next_key:
                    break  # a fault of the provider or of the request: the next provider
        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors) or 'no provider has a key configured'}")

    def _try_key(self, adapter: Callable[..., LLMReply], provider: str, keys: List[str], index: int, model: str,
                 prompt: str, system_prompt: Optional[str], temperature: float, max_tokens: int, json_mode: bool,
                 tier: str, ckey: str, errors: List[str]) -> Tuple[Optional[LLMReply], bool]:
        """Up to max_attempts calls with one key. (reply, _) on an answer; (None, True) when the fault
        belongs to this key (rate limit, bad key, spent budget), so the next key can still work;
        (None, False) when the next provider must be tried."""
        state = self._state(provider, index)
        spec = self._providers.get(provider)
        fixed = spec.temperature if spec else None
        for attempt in range(self.max_attempts):
            state.record_call()
            t0 = time.time()
            try:
                reply = adapter(keys[index], model, prompt, system_prompt,
                                temperature if fixed is None else fixed, max_tokens, json_mode)
            except ProviderFault as fault:
                msg = self._scrub(str(fault))
                self._log_call(tier, provider, model, system_prompt, prompt, error=msg,
                               latency_sec=round(time.time() - t0, 3), key_index=index)
                # A rate limit on one of several keys: use the next key now, not after a backoff.
                rotate = len(keys) > 1 and fault.status == 429
                if fault.retryable and not rotate and attempt < self.max_attempts - 1:
                    add_event("retry", provider=provider, attempt=attempt + 1, status=fault.status)
                    self._sleep(min(0.5 * 2 ** attempt, MAX_BACKOFF_S))
                    continue
                errors.append(msg)
                if fault.budget_exhausted:
                    add_event("budget_exhausted", provider=provider, key_index=index)
                    state.cool_down(BUDGET_COOLDOWN_S)
                    return None, True
                if fault.status in AUTH_STATUS:
                    state.cool_down(self._cooldown(provider, auth=True))
                    return None, True
                if fault.status == 429:
                    if len(keys) > 1:
                        add_event("next_key", provider=provider, key_index=index)
                    state.cool_down(self._cooldown(provider))
                    return None, True
                if fault.retryable:  # the server is down, not the key: cool every key of the row
                    for i in range(len(keys)):
                        self._state(provider, i).cool_down(self._cooldown(provider))
                return None, False
            reply.latency_s = round(time.time() - t0, 3)
            reply.text, reply.reasoning_chars = strip_reasoning(reply.text)
            reply.key_index = index
            entry = self.ledger.record(provider, model, prompt, reply.text, reply.latency_s,
                                       input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                                       stop_reason=reply.stop_reason, key_index=index)
            self._log_call(tier, provider, model, system_prompt, prompt, reply=reply.text,
                           stop_reason=reply.stop_reason, input_tokens=entry.input_tokens,
                           output_tokens=entry.output_tokens, cost_usd=entry.cost_usd,
                           latency_sec=reply.latency_s, key_index=index)
            if reply.stop_reason == "refusal" or not reply.text.strip():
                errors.append(f"{provider}: {reply.stop_reason} with {len(reply.text)} chars")
                return None, False  # try the next provider
            if reply.complete:
                self.cache.put(ckey, reply)
            return reply, False
        return None, False

    # ---- HTTP -----------------------------------------------------------------

    def _post(self, provider: str, url: str, headers: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = self._client.post(url, headers=headers, json=body, timeout=self._timeout(provider))
        except httpx.TimeoutException as e:
            raise ProviderFault(provider, f"timeout ({type(e).__name__})", retryable=True) from None
        except httpx.TransportError as e:
            raise ProviderFault(provider, f"network error ({type(e).__name__})", retryable=True) from None
        if resp.status_code != 200:
            spent = resp.status_code == 429 and BUDGET_EXHAUSTED in resp.text
            raise ProviderFault(provider, f"HTTP {resp.status_code}: {self._scrub(resp.text[:300])}",
                                status=resp.status_code, budget_exhausted=spent,
                                retryable=resp.status_code in RETRYABLE_STATUS and not spent)
        try:
            return resp.json()
        except ValueError:
            raise ProviderFault(provider, "non-JSON response body", status=200, retryable=True) from None

    # ---- adapters -------------------------------------------------------------

    def _call_gemini(self, key, model, prompt, system_prompt, temp, max_tok, json_mode,
                     provider: str = "gemini") -> LLMReply:
        model = model or "gemini-2.5-flash"
        body: Dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": temp, "maxOutputTokens": max_tok}}
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = self._post(provider, f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          {"x-goog-api-key": key, "Content-Type": "application/json"}, body)
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            return LLMReply("", provider, model, "refusal" if block else "other")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        out = usage.get("candidatesTokenCount")
        if out is not None and usage.get("thoughtsTokenCount"):
            out += usage["thoughtsTokenCount"]
        return LLMReply(text, provider, model, _norm_stop(cands[0].get("finishReason")),
                        usage.get("promptTokenCount"), out)

    def _call_anthropic(self, key, model, prompt, system_prompt, temp, max_tok, json_mode,
                        provider: str = "anthropic") -> LLMReply:
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
        if len(body.get("system") or "") >= ANTHROPIC_CACHE_MIN_CHARS:
            # A long, stable system prompt is marked cacheable: later calls with the same prefix
            # read it from the provider's cache (cheaper, faster). Shorter prompts can't be cached.
            body["system"] = [{"type": "text", "text": body["system"], "cache_control": {"type": "ephemeral"}}]
        data = self._post(provider, "https://api.anthropic.com/v1/messages",
                          {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                           "content-type": "application/json"}, body)
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        usage = data.get("usage") or {}
        tin = usage.get("input_tokens")
        if tin is not None:
            tin += (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
        return LLMReply(text, provider, data.get("model", model), _norm_stop(data.get("stop_reason")),
                        tin, usage.get("output_tokens"))

    def _openai_compat(self, url: str, provider_name: Optional[str] = None) -> Callable[..., LLMReply]:
        host = httpx.URL(url).host
        known = {"api.groq.com": "groq", "api.cerebras.ai": "cerebras", "integrate.api.nvidia.com": "nvidia",
                 "openrouter.ai": "openrouter"}
        provider = provider_name or known.get(host, "openai")

        def call(key, model, prompt, system_prompt, temp, max_tok, json_mode) -> LLMReply:
            messages = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + \
                       [{"role": "user", "content": prompt}]
            spec = self._providers.get(provider)
            body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temp,
                                    (spec.max_tokens_field if spec else "max_tokens"): max_tok}
            if spec is not None and spec.reasoning_effort:
                body["reasoning_effort"] = spec.reasoning_effort
            if json_mode and provider in ("groq", "cerebras"):
                body["response_format"] = {"type": "json_object"}
            headers = {"Content-Type": "application/json"}
            if key:  # a keyless local server (LM Studio) gets no Authorization header
                headers["Authorization"] = f"Bearer {key}"
            data = self._post(provider, url, headers, body)
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
