"""
The agent as an MCP server: other agents (Claude Code, an IDE, another team's agent) call it as a tool.

JSON-RPC 2.0, the MCP methods a tool server needs: initialize, notifications/*, ping, tools/list,
tools/call. Two transports:
- stdio: one JSON message per line in, one per line out (`python -m agentkit.service --mcp-stdio`);
- HTTP: POST /mcp (and /api/mcp) on the service's HTTP server; a notification gets 202 and no body.

The tools come from the pack's live playbooks: one tool per playbook (pb-01 -> "pb_01"), with the
playbook's title and example questions as its description, plus one general "ask". Blocked and
spec-only playbooks are not offered. The tool name guides the caller; the router still picks the
plan from the question, and the result says which plan ran.

A tool result: content = the audited answer text; structuredContent = {answer, run_id, tenant,
answer_source, route, outcome, ended}. A refusal is a normal result. isError is true only when the
run failed or the service is busy; it never carries a stack trace.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, IO, List, Optional

from agentkit.service.core import AgentService, BadRequest, Busy

logger = logging.getLogger("agentkit.service.mcp")

PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_VERSIONS = (PROTOCOL_VERSION, "2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
GENERAL_TOOL = "ask"


def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _text_result(text: str, *, error: bool, structured: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if structured is not None:
        out["structuredContent"] = structured
    return out


class McpServer:
    def __init__(self, service: AgentService, name: str = "agentkit-service", version: str = "1.0"):
        self.service = service
        self.name, self.version = name, version

    # ---- tools ----------------------------------------------------------------------

    def _schema(self) -> Dict[str, Any]:
        return {"type": "object", "additionalProperties": False, "required": ["question"],
                "properties": {"question": {"type": "string", "description": "The question, in plain words.",
                                            "maxLength": 2000},
                               "tenant": {"type": "string", "enum": list(self.service.tenants),
                                          "description": f"Default: {self.service.default_tenant}."}}}

    def tools(self) -> List[Dict[str, Any]]:
        annotations = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        out = []
        for book in self.service.playbooks():
            examples = "; ".join(f'"{q}"' for q in book["questions"][:3])
            out.append({"name": book["id"].replace("-", "_"), "title": book["title"],
                        "description": f"{book['title']}. Example questions: {examples}. Dry run: never changes "
                                       f"the platform. The answer is audited against the platform data.",
                        "inputSchema": self._schema(), "annotations": annotations})
        out.append({"name": GENERAL_TOOL, "title": "Ask the agent",
                    "description": "Any question for the agent. The agent routes it to a use case, or says why it "
                                   "cannot answer. Dry run: never changes the platform.",
                    "inputSchema": self._schema(), "annotations": annotations})
        return out

    def call_tool(self, name: Any, arguments: Any) -> Dict[str, Any]:
        """The tools/call result. Raises BadRequest for an unknown tool or bad arguments (-32602)."""
        if name not in {t["name"] for t in self.tools()}:
            raise BadRequest(f"unknown tool: {name!r}")
        if not isinstance(arguments, dict):
            raise BadRequest("arguments: an object is required")
        extra = sorted(set(arguments) - {"question", "tenant"})
        if extra:
            raise BadRequest(f"arguments: unknown {extra}")
        question, tenant = self.service.check(arguments.get("question"), arguments.get("tenant"))
        try:
            with self.service.slot():
                result = self.service.run(question, tenant)
        except Busy as e:
            return _text_result(f"The agent is busy. Try again after {e.retry_after} s.", error=True)
        except Exception:  # noqa: BLE001 -- the run folder has the detail
            logger.exception("tools/call %s failed", name)
            return _text_result("Internal error.", error=True)
        structured = {k: result.get(k) for k in ("answer", "run_id", "tenant", "answer_source", "route", "outcome",
                                                  "ended")}
        if result.get("ended") == "error":
            return _text_result(f"The run failed (run {result['run_id']}).", error=True, structured=structured)
        return _text_result(result.get("answer") or "", error=False, structured=structured)

    # ---- JSON-RPC -------------------------------------------------------------------

    def handle(self, message: Any) -> Optional[Dict[str, Any]]:
        """One JSON-RPC message -> its response, or None for a notification."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "invalid request")
        method, msg_id = message["method"], message.get("id")
        params = message.get("params") or {}
        if "id" not in message:  # a notification: never answered
            return None
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params: an object is required")
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                return _result(msg_id, {
                    "protocolVersion": asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                    "instructions": "Ask questions with the tools. Each answer comes from the platform data and is "
                                    "audited; a refusal says why. Nothing is ever written to the platform."})
            if method == "ping":
                return _result(msg_id, {})
            if method == "tools/list":
                return _result(msg_id, {"tools": self.tools()})
            if method == "tools/call":
                return _result(msg_id, self.call_tool(params.get("name"), params.get("arguments") or {}))
        except BadRequest as e:
            return _error(msg_id, INVALID_PARAMS, str(e))
        except Exception:  # noqa: BLE001
            logger.exception("%s failed", method)
            return _error(msg_id, INTERNAL_ERROR, "internal error")
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def handle_bytes(self, raw: bytes) -> Optional[bytes]:
        """One message as bytes -> the response bytes, or None (a notification)."""
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return json.dumps(_error(None, PARSE_ERROR, "parse error")).encode("utf-8")
        response = self.handle(message)
        return None if response is None else json.dumps(response, default=str).encode("utf-8")


def serve_stdio(server: McpServer, stdin: Optional[IO[str]] = None, stdout: Optional[IO[str]] = None) -> None:
    """Newline-delimited JSON-RPC on stdin/stdout until stdin closes. While a message is handled,
    sys.stdout points to stderr, so no print in the agent can break the protocol stream."""
    if stdin is None:
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr] -- MCP is UTF-8, on Windows too
    if stdout is None:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        saved, sys.stdout = sys.stdout, sys.stderr
        try:
            reply = server.handle_bytes(line.encode("utf-8"))
        finally:
            sys.stdout = saved
        if reply is not None:
            out.write(reply.decode("utf-8") + "\n")
            out.flush()
