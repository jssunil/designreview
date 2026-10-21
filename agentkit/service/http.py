"""
HTTP API of the agent service (standard library only).

  GET  /healthz            {"ok": true, ...}; no token needed
  GET  /v1/playbooks       the live use cases (id, title, plan, example questions)
  POST /v1/ask             {"question": "...", "tenant": "..."} -> the answer, route, finding, run id
  POST /v1/ask             with "Accept: text/event-stream": Server-Sent Events while the run works
                           (run_start, node_start/end, tool_*, llm_*, step_*), then "answer"
  GET  /v1/runs/<run_id>   the record of an earlier service run
  POST /mcp                the MCP server (JSON-RPC; also at /api/mcp): see mcp.py

Errors are JSON {"error": "..."} with 400, 401, 404, 405, 413, 429 (+ Retry-After) or 500, never a
stack trace. A refusal (an unknown part, an ambiguous question) is a normal 200 answer.

Safety: binds to 127.0.0.1 by default. Another host needs AGENTKIT_SERVICE_TOKEN; when it is set,
every route but /healthz needs "Authorization: Bearer <token>" (constant-time compare). Bodies are
limited to 64 KB. JSON escapes <, > and & as \\u sequences.

`ServiceApp.handle()` and `ServiceApp.stream()` hold the logic, so tests need no socket.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple, Union

from agentkit.service.core import AgentService, BadRequest, Busy
from agentkit.service.mcp import McpServer

TOKEN_ENV = "AGENTKIT_SERVICE_TOKEN"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_BODY = 64 * 1024
PING_S = 15.0
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer"}
MCP_PATHS = ("/mcp", "/api/mcp")
ROUTES = {"/healthz": "GET", "/v1/playbooks": "GET", "/v1/ask": "POST", **{p: "POST" for p in MCP_PATHS}}

# (status, content type, body, extra headers)
Response = Tuple[int, str, bytes, Dict[str, str]]


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _json(status: int, obj: Any, **headers: str) -> Response:
    return status, "application/json; charset=utf-8", _dumps(obj).encode("utf-8"), dict(headers)


def _error(status: int, message: str, **headers: str) -> Response:
    return _json(status, {"error": message}, **headers)


class TooLarge(Exception):
    pass


def sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {_dumps(data)}\n\n".encode("utf-8")


class ServiceApp:
    def __init__(self, service: AgentService, token: Optional[str] = None):
        self.service = service
        self.token = token
        self.mcp = McpServer(service)

    # ---- shared checks --------------------------------------------------------------

    def authorized(self, path: str, headers: Mapping[str, str]) -> bool:
        if not self.token or path == "/healthz":
            return True
        given = headers.get("Authorization") or headers.get("authorization") or ""
        return hmac.compare_digest(given.encode("utf-8"), f"Bearer {self.token}".encode("utf-8"))

    @staticmethod
    def _question(body: bytes) -> Dict[str, Any]:
        if len(body) > MAX_BODY:
            raise TooLarge()
        try:
            data = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, ValueError):
            raise BadRequest("the body must be a JSON object") from None
        if not isinstance(data, dict):
            raise BadRequest("the body must be a JSON object")
        return data

    def _precheck(self, method: str, path: str, headers: Mapping[str, str]) -> Optional[Response]:
        if path.startswith("/v1/runs/"):
            allowed = "GET"
        else:
            allowed = ROUTES.get(path, "")
        if not allowed:
            return _error(404, "not found")
        if method != allowed:
            return _error(405, "method not allowed", Allow=allowed)
        if not self.authorized(path, headers):
            return _error(401, "unauthorized", **{"WWW-Authenticate": "Bearer"})
        return None

    # ---- plain JSON -----------------------------------------------------------------

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"") -> Response:
        path = path.split("?", 1)[0]
        refused = self._precheck(method, path, headers)
        if refused:
            return refused
        if path == "/healthz":
            return _json(200, self.service.health())
        if path == "/v1/playbooks":
            return _json(200, {"playbooks": self.service.playbooks()})
        if path.startswith("/v1/runs/"):
            record = self.service.run_record(path[len("/v1/runs/"):])
            return _json(200, record) if record is not None else _error(404, "no such service run")
        if path in MCP_PATHS:
            if len(body) > MAX_BODY:
                return _error(413, f"the body is larger than {MAX_BODY} bytes")
            reply = self.mcp.handle_bytes(body)
            if reply is None:  # a notification
                return 202, "application/json; charset=utf-8", b"", {}
            return 200, "application/json; charset=utf-8", reply, {}
        try:
            data = self._question(body)
            return _json(200, self.service.ask(data.get("question"), data.get("tenant")))
        except BadRequest as e:
            return _error(400, str(e))
        except Busy as e:
            return _error(429, str(e), **{"Retry-After": str(e.retry_after)})
        except TooLarge:
            return _error(413, f"the body is larger than {MAX_BODY} bytes")
        except Exception:  # noqa: BLE001 -- the run folder has the detail; the caller gets no trace
            return _error(500, "internal error")

    # ---- Server-Sent Events ---------------------------------------------------------

    def stream(self, path: str, headers: Mapping[str, str], body: bytes) -> Union[Response, Iterator[bytes]]:
        """An error Response (sent before any event), or the SSE byte chunks of one run."""
        path = path.split("?", 1)[0]
        refused = self._precheck("POST", path, headers)
        if refused:
            return refused
        if path != "/v1/ask":
            return _error(404, "not found")
        stack = contextlib.ExitStack()
        try:
            data = self._question(body)
            question, tenant = self.service.check(data.get("question"), data.get("tenant"))
            stack.enter_context(self.service.slot())
        except BadRequest as e:
            return _error(400, str(e))
        except Busy as e:
            return _error(429, str(e), **{"Retry-After": str(e.retry_after)})
        except TooLarge:
            return _error(413, f"the body is larger than {MAX_BODY} bytes")
        return self._events(stack, question, tenant)

    def _events(self, stack: contextlib.ExitStack, question: str, tenant: str) -> Iterator[bytes]:
        events: "queue.Queue[Optional[Tuple[str, Any]]]" = queue.Queue()

        def work() -> None:
            with stack:  # gives the slot back when the run ends, even if the caller has gone
                try:
                    result = self.service.run(question, tenant, lambda e: events.put((e["event"], e)))
                    events.put(("answer", result))
                except Exception:  # noqa: BLE001
                    events.put(("error", {"error": "internal error"}))
                finally:
                    events.put(None)

        threading.Thread(target=work, name="service-ask", daemon=True).start()
        while True:
            try:
                item = events.get(timeout=PING_S)
            except queue.Empty:
                yield b": ping\n\n"  # keeps proxies from closing a quiet stream
                continue
            if item is None:
                return
            yield sse(*item)


def make_handler(app: ServiceApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentkit-service"

        def _body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            return self.rfile.read(min(length, MAX_BODY + 1)) if length > 0 else b""

        def _send(self, response: Response, head_only: bool = False) -> None:
            status, ctype, body, extra = response
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **extra}.items():
                self.send_header(k, v)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._send(app.handle("GET", self.path, self.headers))

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            if "text/event-stream" not in (self.headers.get("Accept") or ""):
                self._send(app.handle("POST", self.path, self.headers, body))
                return
            result = app.stream(self.path, self.headers, body)
            if isinstance(result, tuple):
                self._send(result)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            try:
                for chunk in result:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the caller left; the run still finishes and keeps its folder

        def _other(self) -> None:
            self._send(app.handle(self.command, self.path, self.headers))

        do_PUT = do_DELETE = do_PATCH = _other  # noqa: N815 -- each gets 404 or 405

        def log_message(self, fmt: str, *args: Any) -> None:
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

    return Handler


def make_server(service: AgentService, host: str = "127.0.0.1", port: int = 8780, *, token: Optional[str] = None,
                verbose: bool = False) -> ThreadingHTTPServer:
    token = token if token is not None else (os.environ.get(TOKEN_ENV) or None)
    if host not in LOOPBACK and not token:
        raise ValueError(f"serving on {host} needs {TOKEN_ENV}: anyone who can reach it could ask questions")
    server = ThreadingHTTPServer((host, port), make_handler(ServiceApp(service, token)))
    server.daemon_threads = True
    server.verbose = verbose  # type: ignore[attr-defined]
    return server
