"""
Generic Multi-Provider LLM Gateway.
Rate-aware routing, one retry-with-backoff per provider, response caching, and
a running cost/token ledger -- across Gemini, Groq, Cerebras, Anthropic,
OpenRouter, and Ollama. No dependencies on external agent frameworks.

Provenance: the routing/rate-limit design (RateState, tier-ordered candidate
selection) is a trimmed port of D:\\sjk\\eagv3\\glc_v5\\glc\\routing\\core.py's
Router/RateState, adapted from a hosted FastAPI gateway service into a plain
importable client -- this project calls providers directly from agent code
rather than running a sidecar service. The response cache is a simplified,
exact-match version of glc_v5's embedding-based semantic cache
(glc/cache/semantic.py); it skips the embeddings dependency since exact-match
is enough to avoid re-paying for repeated evidence-gathering calls during
harness/eval iteration. Cost tracking is core/economics.py, trimmed from
glc_v5's economics/{pricing,meter}.py.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
import yaml

if __name__ == "__main__":
    # Running this file directly (`python core/llm_gateway.py`) only puts
    # core/ on sys.path, not the project root -- add it so `core.economics`
    # resolves the same way it does when imported from a root-level script.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.economics import CostLedger

try:
    from dotenv import load_dotenv
    # Search for .env in current, parent or project root
    env_path = Path.cwd() / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

logger = logging.getLogger("core.llm_gateway")

ROUTING_CONFIG_PATH = Path(__file__).parent / "routing.yaml"
DEFAULT_TIER_ORDER = ["gemini", "groq", "cerebras", "anthropic", "openrouter", "ollama"]


@dataclass
class RateState:
    """Tracks recent call outcomes for one provider and governs cooldown/
    backoff -- a rate-limited or recently-failed provider is skipped instead
    of being retried straight into another 429/5xx."""

    rpm_limit: int = 60
    _call_times: List[float] = field(default_factory=list)
    _cooldown_until: float = 0.0

    def available(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        if now < self._cooldown_until:
            return False
        self._call_times = [t for t in self._call_times if now - t < 60]
        return len(self._call_times) < self.rpm_limit

    def record_call(self, now: Optional[float] = None) -> None:
        self._call_times.append(now if now is not None else time.time())

    def record_failure(self, cooldown_sec: float = 20.0, now: Optional[float] = None) -> None:
        self._cooldown_until = (now if now is not None else time.time()) + cooldown_sec


class ResponseCache:
    """Exact-prompt-hash cache: matches only identical
    (system_prompt, prompt, temperature) triples. No embeddings, no semantic
    matching -- just enough to avoid re-paying for repeated calls when the
    harness/eval loop re-runs the same evidence-gathering prompt."""

    def __init__(self, maxsize: int = 256):
        self._store: Dict[str, str] = {}
        self._order: List[str] = []
        self.maxsize = maxsize

    @staticmethod
    def _key(system_prompt: Optional[str], prompt: str, temperature: float) -> str:
        raw = f"{system_prompt or ''}|{prompt}|{temperature}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, system_prompt: Optional[str], prompt: str, temperature: float) -> Optional[str]:
        return self._store.get(self._key(system_prompt, prompt, temperature))

    def set(self, system_prompt: Optional[str], prompt: str, temperature: float, value: str) -> None:
        key = self._key(system_prompt, prompt, temperature)
        if key not in self._store and len(self._order) >= self.maxsize:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)
        self._store[key] = value
        if key not in self._order:
            self._order.append(key)


def _load_routing_config(path: Path) -> Dict[str, Any]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class LLMGateway:
    """Resilient multi-provider LLM transport with rate-aware routing,
    per-provider retry, response caching, and a running cost/token ledger."""

    def __init__(self, routing_config_path: Optional[Path] = None):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.groq_key = os.getenv("GROQ_API_KEY")
        self.cerebras_key = os.getenv("CEREBRAS_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.nvidia_key = os.getenv("NVIDIA_API_KEY")
        self.openrouter_key = os.getenv("OPEN_ROUTER_API_KEY")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

        cfg_path = routing_config_path or ROUTING_CONFIG_PATH
        self._config = _load_routing_config(cfg_path)
        self._provider_configs: Dict[str, Dict[str, Any]] = {
            p["name"]: p for p in self._config.get("providers", [])
        }
        self._rate_states: Dict[str, RateState] = {
            name: RateState(rpm_limit=cfg.get("rpm", 60)) for name, cfg in self._provider_configs.items()
        }
        self.cache = ResponseCache()
        self.ledger = CostLedger()

        self._adapters: Dict[str, Callable[..., str]] = {
            "gemini": self._call_gemini,
            "groq": self._call_groq,
            "cerebras": self._call_cerebras,
            "anthropic": self._call_anthropic,
            "openrouter": self._call_openrouter,
            "ollama": self._call_ollama,
        }
        self._key_present: Dict[str, Callable[[], bool]] = {
            "gemini": lambda: bool(self.gemini_key),
            "groq": lambda: bool(self.groq_key),
            "cerebras": lambda: bool(self.cerebras_key),
            "anthropic": lambda: bool(self.anthropic_key),
            "openrouter": lambda: bool(self.openrouter_key),
            "ollama": lambda: True,  # local, no key required
        }

    def _candidate_order(self, tier: str) -> List[str]:
        tiers = self._config.get("tiers", {})
        order = tiers.get(tier) or tiers.get("default")
        return order or DEFAULT_TIER_ORDER

    def call(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        json_mode: bool = False,
        tier: str = "default",
    ) -> str:
        """Call LLM with rate-aware routing, retry-with-backoff, caching, and
        cost tracking. `tier` selects a candidate order from routing.yaml
        (e.g. "fast" for the eval judge, "quality" for final synthesis)."""
        cached = self.cache.get(system_prompt, prompt, temperature)
        if cached is not None:
            logger.info("cache_hit tier=%s", tier)
            return cached

        errors: List[str] = []

        for provider in self._candidate_order(tier):
            if not self._key_present.get(provider, lambda: False)():
                continue
            state = self._rate_states.setdefault(provider, RateState())
            if not state.available():
                errors.append(f"{provider}: skipped (cooling down / rate-limited)")
                continue

            adapter = self._adapters[provider]
            model_name = self._provider_configs.get(provider, {}).get("model")

            for attempt in range(2):  # one retry-with-backoff per provider
                try:
                    state.record_call()
                    t0 = time.time()
                    text = adapter(prompt, system_prompt, temperature, max_tokens, json_mode, model_name)
                    latency = time.time() - t0
                    self.ledger.record(provider, model_name or provider, prompt, text, latency)
                    self.cache.set(system_prompt, prompt, temperature, text)
                    return text
                except Exception as e:
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                    state.record_failure()
                    errors.append(f"{provider} error: {e}")

        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")

    # ---- provider adapters (transport unchanged from the first draft; each
    # now accepts an optional model override sourced from routing.yaml) ----

    def _call_gemini(self, prompt, system_prompt, temp, max_tok, json_mode, model=None) -> str:
        model = model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_key}"
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"System Instructions:\n{system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temp,
                "maxOutputTokens": max_tok,
            }
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        with httpx.Client(timeout=30.0, verify=False) as client:
            resp = client.post(url, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text}")
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

    def _call_groq(self, prompt, system_prompt, temp, max_tok, json_mode, model=None) -> str:
        model = model or "llama-3.3-70b-versatile"
        url = "https://api.groq.com/openai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {self.groq_key}"}, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text}")
            return resp.json()["choices"][0]["message"]["content"]

    def _call_cerebras(self, prompt, system_prompt, temp, max_tok, json_mode, model=None) -> str:
        model = model or "llama3.1-70b"
        url = "https://api.cerebras.ai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {self.cerebras_key}"}, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"Cerebras HTTP {resp.status_code}: {resp.text}")
            return resp.json()["choices"][0]["message"]["content"]

    def _call_anthropic(self, prompt, system_prompt, temp, max_tok, json_mode=False, model=None) -> str:
        model = model or "claude-3-5-sonnet-20241022"
        url = "https://api.anthropic.com/v1/messages"
        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tok,
            "temperature": temp,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt

        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=40.0) as client:
            resp = client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"Anthropic HTTP {resp.status_code}: {resp.text}")
            return resp.json()["content"][0]["text"]

    def _call_openrouter(self, prompt, system_prompt, temp, max_tok, json_mode, model=None) -> str:
        model = model or "meta-llama/llama-3.3-70b-instruct"
        url = "https://openrouter.ai/api/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {self.openrouter_key}"}, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text}")
            return resp.json()["choices"][0]["message"]["content"]

    def _call_ollama(self, prompt, system_prompt, temp, max_tok, json_mode, model=None) -> str:
        model = model or os.getenv("OLLAMA_MODEL", "gemma:latest")
        url = f"{self.ollama_url}/api/chat"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temp, "num_predict": max_tok},
        }
        if json_mode:
            body["format"] = "json"

        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text}")
            return resp.json()["message"]["content"]


if __name__ == "__main__":
    gw = LLMGateway()
    print("Testing LLM Gateway...")
    res = gw.call("Respond with 'GATEWAY_OK' if you receive this message.")
    print("Gateway response:", res.strip())
    print("Cost ledger:", gw.ledger.summary())
