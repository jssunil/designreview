"""
LLM Gateway Client for Capstone Agent (Team 21).
Pure Python implementation (using httpx / standard library) with multi-provider fallback:
Gemini -> Groq -> Cerebras -> Anthropic -> Nvidia -> OpenRouter -> Ollama.
No LangChain, No LangGraph, No CrewAI.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# Ensure local .env is loaded
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass


class LLMGateway:
    """Lightweight, resilient multi-provider LLM caller with automated fallback."""

    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.groq_key = os.getenv("GROQ_API_KEY")
        self.cerebras_key = os.getenv("CEREBRAS_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.nvidia_key = os.getenv("NVIDIA_API_KEY")
        self.openrouter_key = os.getenv("OPEN_ROUTER_API_KEY")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

    def call(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Call LLM with automated multi-tier fallback."""
        errors = []

        # 1. Try Gemini
        if self.gemini_key:
            try:
                return self._call_gemini(prompt, system_prompt, temperature, max_tokens, json_mode)
            except Exception as e:
                errors.append(f"Gemini error: {e}")

        # 2. Try Groq
        if self.groq_key:
            try:
                return self._call_groq(prompt, system_prompt, temperature, max_tokens, json_mode)
            except Exception as e:
                errors.append(f"Groq error: {e}")

        # 3. Try Cerebras
        if self.cerebras_key:
            try:
                return self._call_cerebras(prompt, system_prompt, temperature, max_tokens, json_mode)
            except Exception as e:
                errors.append(f"Cerebras error: {e}")

        # 4. Try Anthropic
        if self.anthropic_key:
            try:
                return self._call_anthropic(prompt, system_prompt, temperature, max_tokens)
            except Exception as e:
                errors.append(f"Anthropic error: {e}")

        # 5. Try OpenRouter
        if self.openrouter_key:
            try:
                return self._call_openrouter(prompt, system_prompt, temperature, max_tokens, json_mode)
            except Exception as e:
                errors.append(f"OpenRouter error: {e}")

        # 6. Try Ollama local
        try:
            return self._call_ollama(prompt, system_prompt, temperature, max_tokens, json_mode)
        except Exception as e:
            errors.append(f"Ollama error: {e}")

        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")

    def _call_gemini(self, prompt: str, system_prompt: Optional[str], temp: float, max_tok: int, json_mode: bool) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
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

    def _call_groq(self, prompt: str, system_prompt: Optional[str], temp: float, max_tok: int, json_mode: bool) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {
            "model": "llama-3.3-70b-versatile",
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

    def _call_cerebras(self, prompt: str, system_prompt: Optional[str], temp: float, max_tok: int, json_mode: bool) -> str:
        url = "https://api.cerebras.ai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {
            "model": "llama3.1-70b",
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

    def _call_anthropic(self, prompt: str, system_prompt: Optional[str], temp: float, max_tok: int) -> str:
        url = "https://api.anthropic.com/v1/messages"
        body: Dict[str, Any] = {
            "model": "claude-3-5-sonnet-20241022",
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

    def _call_openrouter(self, prompt: str, system_prompt: Optional[str], temp: float, max_tok: int, json_mode: bool) -> str:
        url = "https://openrouter.ai/api/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": "meta-llama/llama-3.3-70b-instruct",
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {self.openrouter_key}"}, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text}")
            return resp.json()["choices"][0]["message"]["content"]

    def _call_ollama(self, prompt: str, system_prompt: Optional[str], temp: float, max_tok: int, json_mode: bool) -> str:
        url = f"{self.ollama_url}/api/chat"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": os.getenv("OLLAMA_MODEL", "gemma:latest"),
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
