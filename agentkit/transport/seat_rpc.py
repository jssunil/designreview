"""
JSON-RPC 2.0 client for a seat's MCP surface on AgentSwitch (POST /api/mcp).

What callers can rely on:
- Success returns the tool's payload (structuredContent, else the JSON in
  content[].text, else the raw result) -- never the envelope.
- Every failure is a SeatCallFailure whose `kind` says what to do next.
  Branch on `kind`, never on message text:

    MISSING         no such record            -> refuse, don't retry
    BAD_ARGS        schema violation          -> fix args; `field_errors` names the field
    BAD_TRANSITION  right tool, wrong state   -> report, don't retry
    NOT_IN_SEAT     tool absent from this seat's tools/list -> structural refusal
    DENIED          HTTP 403                  -> refuse
    TOKEN_EXPIRED   HTTP 401 after one re-login already failed
    FLAKY           network, timeout, 429, 5xx, non-JSON body -> a read may be retried
    UNCLASSIFIED    an error with no recognisable code, or result.isError

- A JSON-RPC error arrives with HTTP 200, so the envelope is always checked.
- HTTP 401 triggers exactly one re-login and one resend. A rejected token
  means the request was never processed, so this is safe even for writes.
  Concurrent callers share one session; only the first re-logs in.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

from agentkit.config import TenantLogin, tenant_login
from agentkit.transport.wire import UrllibWire, Wire, WireReply, WireUnreachable

PROTOCOL_VERSION = "2025-11-25"
MCP_PATH = "/api/mcp"
LOGIN_PATH = "/api/auth/login"

MISSING = "missing"
BAD_ARGS = "bad_args"
BAD_TRANSITION = "bad_transition"
NOT_IN_SEAT = "not_in_seat"
DENIED = "denied"
TOKEN_EXPIRED = "token_expired"
FLAKY = "flaky"
UNCLASSIFIED = "unclassified"
KINDS = (MISSING, BAD_ARGS, BAD_TRANSITION, NOT_IN_SEAT, DENIED, TOKEN_EXPIRED, FLAKY, UNCLASSIFIED)

# The platform's own error.data.code values -> our kinds.
_PLATFORM_CODES = {
    "not_found": MISSING,
    "invalid_arguments": BAD_ARGS,
    "invalid_transition": BAD_TRANSITION,
    "tool_not_available": NOT_IN_SEAT,
    "permission_denied": DENIED,
    "forbidden": DENIED,
}
JSONRPC_METHOD_NOT_FOUND = -32601


class SeatCallFailure(Exception):
    def __init__(self, message: str, kind: str = UNCLASSIFIED, *, tool: str = "",
                 raw: Any = None, field_errors: Optional[list] = None, http_status: Optional[int] = None):
        super().__init__(message)
        self.kind = kind if kind in KINDS else UNCLASSIFIED
        self.tool = tool
        self.raw = raw
        self.field_errors = field_errors or []
        self.http_status = http_status

    @property
    def retryable(self) -> bool:
        return self.kind == FLAKY


def classify_rpc_error(error: Any) -> tuple[str, str, list]:
    """(kind, message, field_errors) from a JSON-RPC `error` member, which
    the spec allows to be any shape."""
    if not isinstance(error, dict):
        return UNCLASSIFIED, str(error), []
    message = str(error.get("message", "MCP error"))
    data = error.get("data")
    platform_code = data.get("code") if isinstance(data, dict) else None
    field_errors = data.get("errors", []) if isinstance(data, dict) else []
    if platform_code in _PLATFORM_CODES:
        return _PLATFORM_CODES[platform_code], message, field_errors
    lowered = message.lower()
    if error.get("code") == JSONRPC_METHOD_NOT_FOUND or "unknown tool" in lowered or "not available to your seat" in lowered:
        return NOT_IN_SEAT, message, field_errors
    if "permission" in lowered or "cannot perform" in lowered:
        return DENIED, message, field_errors
    return UNCLASSIFIED, message, field_errors


def unwrap_payload(result: Any) -> Any:
    """structuredContent if present, else the JSON inside content[].text,
    else the result itself (some tools return the record directly)."""
    if not isinstance(result, dict):
        return result
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    blocks = result.get("content")
    if isinstance(blocks, list) and blocks and all(isinstance(b, dict) for b in blocks):
        text = "".join(b.get("text", "") for b in blocks if b.get("type", "text") == "text")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}
    return result


def is_read_tool(name: str, extra_read_only: Iterable[str] = ()) -> bool:
    return name.endswith((".list", ".get")) or name in set(extra_read_only)


class SeatSession:
    """One login for one tenant, shared by every client/thread using it."""

    def __init__(self, login: TenantLogin, wire: Optional[Wire] = None, token: Optional[str] = None):
        self.login_info = login
        self.base_url = login.base_url.rstrip("/")
        self.wire: Wire = wire or UrllibWire()
        self._lock = threading.Lock()
        self.token: Optional[str] = token
        self.login_count = 0

    @classmethod
    def for_tenant(cls, tenant: str, wire: Optional[Wire] = None) -> "SeatSession":
        return cls(tenant_login(tenant), wire)

    def login(self) -> str:
        body = json.dumps({"email": self.login_info.email, "password": self.login_info.password}).encode()
        try:
            reply = self.wire.send("POST", self.base_url + LOGIN_PATH, {"Content-Type": "application/json"},
                                   body, safe_to_repeat=True)
        except WireUnreachable as e:
            raise SeatCallFailure(f"login: {self.base_url} unreachable ({e})", FLAKY) from e
        data = _json_or_none(reply.body)
        token = data.get("token") if isinstance(data, dict) else None
        if reply.status != 200 or not token:
            raise SeatCallFailure(f"login failed: HTTP {reply.status} {reply.body[:200]!r}",
                                  TOKEN_EXPIRED if reply.status == 401 else UNCLASSIFIED,
                                  http_status=reply.status)
        self.token = token
        self.login_count += 1
        return token

    def ensure_token(self) -> str:
        if not self.token:
            with self._lock:
                if not self.token:
                    self.login()
        return self.token  # type: ignore[return-value]

    def renew(self, stale: Optional[str]) -> None:
        """Re-login unless another thread already replaced `stale`."""
        with self._lock:
            if self.token == stale:
                self.login()

    def auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.ensure_token()}", "Content-Type": "application/json"}


def _json_or_none(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class SeatRpcClient:
    """MCP tools for one seat. Thread-safe: ids come from a locked counter
    and re-login goes through the shared SeatSession."""

    def __init__(self, session: SeatSession, *, client_name: str = "agentkit-agent",
                 client_version: str = "2.0", extra_read_only: Iterable[str] = ()):
        self.session = session
        self.client_name = client_name
        self.client_version = client_version
        self.extra_read_only = frozenset(extra_read_only)
        self._id = 0
        self._id_lock = threading.Lock()
        self._catalog: Optional[Dict[str, dict]] = None
        self._initialized = False

    @classmethod
    def for_tenant(cls, tenant: str, wire: Optional[Wire] = None, **kw: Any) -> "SeatRpcClient":
        return cls(SeatSession.for_tenant(tenant, wire), **kw)

    # ---- envelope ---------------------------------------------------------

    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def _post_once(self, method: str, params: Dict[str, Any], label: str, *, safe: bool,
                   notification: bool = False) -> Any:
        envelope: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            envelope["id"] = self._next_id()
        try:
            reply: WireReply = self.session.wire.send(
                "POST", self.session.base_url + MCP_PATH, self.session.auth_headers(),
                json.dumps(envelope).encode(), safe_to_repeat=safe)
        except WireUnreachable as e:
            raise SeatCallFailure(f"{label}: {e}", FLAKY, tool=label) from e

        if reply.status == 401:
            raise SeatCallFailure(f"{label}: HTTP 401", TOKEN_EXPIRED, tool=label, http_status=401)
        if reply.status == 403:
            raise SeatCallFailure(f"{label}: HTTP 403 {reply.body[:200]!r}", DENIED, tool=label, http_status=403)
        if reply.status == 429 or reply.status >= 500:
            raise SeatCallFailure(f"{label}: HTTP {reply.status}", FLAKY, tool=label, http_status=reply.status)
        if notification and not reply.body.strip():
            return None

        data = _json_or_none(reply.body)
        if not isinstance(data, dict):
            raise SeatCallFailure(f"{label}: non-JSON reply (HTTP {reply.status}) {reply.body[:200]!r}",
                                  FLAKY, tool=label, http_status=reply.status)
        if "error" in data:
            kind, message, field_errors = classify_rpc_error(data["error"])
            raise SeatCallFailure(f"{label}: {message}"[:500], kind, tool=label, raw=data,
                                  field_errors=field_errors, http_status=reply.status)
        if reply.status != 200:
            raise SeatCallFailure(f"{label}: HTTP {reply.status}", UNCLASSIFIED, tool=label,
                                  raw=data, http_status=reply.status)
        return data.get("result")

    def _post_envelope(self, method: str, params: Dict[str, Any], label: str, *, safe: bool,
                       notification: bool = False) -> Any:
        stale = self.session.ensure_token()
        try:
            return self._post_once(method, params, label, safe=safe, notification=notification)
        except SeatCallFailure as e:
            if e.kind != TOKEN_EXPIRED:
                raise
            self.session.renew(stale)
            return self._post_once(method, params, label, safe=safe, notification=notification)

    # ---- MCP methods --------------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        result = self._post_envelope("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": self.client_name, "version": self.client_version},
        }, "initialize", safe=True)
        try:
            self._post_envelope("notifications/initialized", {}, "notifications/initialized",
                                safe=True, notification=True)
        except SeatCallFailure:
            pass  # a server that answers a notification oddly is not fatal
        self._initialized = True
        return result or {}

    def seat_catalog(self, refresh: bool = False, max_pages: int = 50) -> Dict[str, dict]:
        """name -> tool definition for everything this seat's tools/list shows
        (all pages, cached)."""
        if self._catalog is not None and not refresh:
            return self._catalog
        tools: Dict[str, dict] = {}
        cursor = None
        for _ in range(max_pages):
            result = self._post_envelope("tools/list", {"cursor": cursor} if cursor else {},
                                         "tools/list", safe=True) or {}
            for t in result.get("tools") or []:
                if isinstance(t, dict) and t.get("name"):
                    tools[t["name"]] = t
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._catalog = tools
        return tools

    def offers(self, tool: str) -> bool:
        return tool in self.seat_catalog()

    def invoke_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        safe = is_read_tool(name, self.extra_read_only)
        result = self._post_envelope("tools/call", {"name": name, "arguments": arguments or {}},
                                     name, safe=safe)
        payload = unwrap_payload(result)
        if isinstance(result, dict) and result.get("isError"):
            raise SeatCallFailure(f"{name}: tool reported isError: {json.dumps(payload, default=str)[:300]}",
                                  UNCLASSIFIED, tool=name, raw=result)
        return payload

    def fetch_every_page(self, tool: str, arguments: Optional[Dict[str, Any]] = None,
                         page_size: int = 100, max_pages: int = 40) -> List[dict]:
        """All rows of a `.list` tool, de-duplicated by id, in server order."""
        by_id: Dict[Any, dict] = {}
        offset = 0
        for _ in range(max_pages):
            page = self.invoke_tool(tool, {**(arguments or {}), "limit": page_size, "offset": offset})
            rows = page.get("data") if isinstance(page, dict) else page
            if not isinstance(rows, list):
                break
            for r in rows:
                if isinstance(r, dict):
                    by_id.setdefault(r.get("id", id(r)), r)
            offset += len(rows)
            total = page.get("total") if isinstance(page, dict) else None
            if not rows or len(rows) < page_size or (total is not None and offset >= int(total)):
                break
        return list(by_id.values())


def tool_caller(client: SeatRpcClient) -> Callable[[str, Dict[str, Any]], Any]:
    """Adapter for code that wants a plain (tool, args) -> payload function."""
    return lambda name, args: client.invoke_tool(name, args)
