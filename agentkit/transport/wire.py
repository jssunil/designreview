"""
The byte-level HTTP seam every platform call goes through.

`Wire` is a one-method protocol so the offline simulated seat (agentkit/sim)
and tests can stand in for the network without monkeypatching urllib.
`UrllibWire` is the real one: stdlib only, TLS verified by default, a
timeout on every request.
"""

from __future__ import annotations

import http.client
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

RETRYABLE_GATEWAY_STATUSES = frozenset({502, 503, 504})


class WireUnreachable(Exception):
    """No HTTP response at all: DNS, refused connection, TLS failure, timeout."""


@dataclass
class WireReply:
    status: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)


class Wire(Protocol):
    def send(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
             *, safe_to_repeat: bool) -> WireReply: ...


def _looks_chunked(head: bytes) -> bool:
    """True if `head` starts with a valid chunk-size line (hex digits,
    optional ;extensions, CRLF)."""
    line, sep, _ = head.partition(b"\r\n")
    if not sep:
        return False
    size = line.split(b";", 1)[0].strip()
    try:
        int(size, 16)
        return True
    except ValueError:
        return False


def read_body(resp) -> bytes:
    """Read an http.client response body, tolerating two AgentSwitch framing
    faults seen live on 2026-09-28:

    1. `Transfer-Encoding: chunked` declared, but the body is plain JSON with
       no chunk framing (every /api/mcp method). The server also sends
       `Connection: close`, so reading to EOF returns the whole body.
    2. A body shorter than declared -> keep what arrived.
    """
    if getattr(resp, "chunked", False) and hasattr(resp, "fp") and hasattr(resp.fp, "peek"):
        if not _looks_chunked(resp.fp.peek(64)[:64]):
            resp.chunked = False
            resp.length = None
    try:
        return resp.read()
    except http.client.IncompleteRead as short:
        return short.partial


def _tls_context() -> ssl.SSLContext:
    # Opt-out exists only for broken corporate proxies; the platform's
    # certificates verify fine (checked 2026-09-28).
    if os.getenv("AS_INSECURE_TLS") == "1":
        return ssl._create_unverified_context()  # noqa: S323 -- explicit opt-in
    return ssl.create_default_context()


class UrllibWire:
    """Real network. `safe_to_repeat` (a read) allows retrying connection
    failures and 502/503/504; a write gets exactly one attempt, because a
    gateway error or timeout may arrive after the server applied it."""

    def __init__(self, timeout: float = 60.0, read_attempts: int = 3, backoff_s: float = 1.0):
        self.timeout = timeout
        self.read_attempts = max(1, read_attempts)
        self.backoff_s = backoff_s
        self._ctx = _tls_context()

    def send(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes],
             *, safe_to_repeat: bool) -> WireReply:
        attempts = self.read_attempts if safe_to_repeat else 1
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    return WireReply(resp.status, read_body(resp), dict(resp.headers.items()))
            except urllib.error.HTTPError as e:
                reply = WireReply(e.code, e.read() or b"", dict(e.headers.items()) if e.headers else {})
                if e.code in RETRYABLE_GATEWAY_STATUSES and attempt < attempts - 1:
                    time.sleep(self.backoff_s * (attempt + 1))
                    continue
                return reply
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as e:
                last_error = e
                if attempt < attempts - 1:
                    time.sleep(self.backoff_s * (attempt + 1))
                    continue
        raise WireUnreachable(f"{method} {url}: {last_error}") from last_error
