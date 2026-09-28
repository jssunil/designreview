"""
AgentSwitch client helper for Team 21 (Design Review seat) -- compatibility shim.

New code should use agentkit.transport (SeatRpcClient for MCP tools,
RestReader for verifier reads). This module keeps the first draft's public
API working for capstone_agent.py and
file_platform_bugs.py while they are migrated:

- get_environment_config(env) -> {"email", "url", "password"}
- AgentSwitchClient(env).login() / get_me() / request() / call_mcp() /
  init_mcp() / list_mcp_tools()
- AgentSwitchClient.seat() -> an agentkit SeatRpcClient sharing this login

Changes from the first draft: TLS is verified (opt-out: AS_INSECURE_TLS=1),
every request has a timeout, and tenant URLs/credentials come from
config/tenants.toml via agentkit.config instead of hard-coded branches.
call_mcp() still returns the raw JSON-RPC envelope (errors included) for
old callers; new callers get typed SeatCallFailure errors from seat().
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentkit.config import TenantLogin, load_env, tenant_login  # noqa: E402
from agentkit.transport.seat_rpc import SeatRpcClient, SeatSession  # noqa: E402
from agentkit.transport.wire import read_body  # noqa: E402

REQUEST_TIMEOUT_S = 60.0

# Importing this module loads .env, as the first draft did -- tests/conftest.py
# and scripts rely on AS_* variables being set after `import as_client`.
load_env()


def get_environment_config(env_name: str) -> Dict[str, str]:
    """Retrieve environment credentials and URL from environment variables."""
    login = tenant_login(env_name)
    return {"email": login.email, "url": login.base_url, "password": login.password}


def _tls_context() -> ssl.SSLContext:
    if os.getenv("AS_INSECURE_TLS") == "1":
        return ssl._create_unverified_context()  # noqa: S323 -- explicit opt-in
    return ssl.create_default_context()


class AgentSwitchClient:
    def __init__(self, environment: str = "suryodaya"):
        config = get_environment_config(environment)
        self.env_name = environment.lower()
        self.base_url = config["url"]
        self.email = config["email"]
        self.password = config["password"]
        self.token: Optional[str] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self._ctx = _tls_context()
        self._rpc_id = 0

    def login(self) -> str:
        """Authenticate and retrieve Bearer token."""
        url = f"{self.base_url}/api/auth/login"
        payload = json.dumps({"email": self.email, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=REQUEST_TIMEOUT_S) as response:
                data = json.loads(read_body(response).decode("utf-8"))
                self.token = data.get("token")
                return self.token
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise RuntimeError(f"Login failed ({e.code}): {error_body}") from e

    def get_me(self) -> Dict[str, Any]:
        """Verify identity and seat roles via /api/auth/me."""
        data = self.request("GET", "/api/auth/me")
        self.user_info = data
        return data

    def request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Perform an authenticated HTTP request to the API."""
        if not self.token:
            self.login()

        url = f"{self.base_url}{path}"
        req_headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=REQUEST_TIMEOUT_S) as response:
                body = read_body(response).decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code} for {method} {path}: {error_body}") from e

    def call_mcp(self, method: str, params: Optional[Dict[str, Any]] = None, rpc_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute a JSON-RPC 2.0 call against /api/mcp. Returns the raw
        envelope: a JSON-RPC error arrives with HTTP 200 and is the caller's
        to check."""
        if rpc_id is None:
            self._rpc_id += 1
            rpc_id = self._rpc_id
        body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}
        return self.request("POST", "/api/mcp", payload=body)

    def init_mcp(self) -> Dict[str, Any]:
        """Run standard MCP handshake."""
        init_res = self.call_mcp(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "team21-agent", "version": "1.0"},
            },
        )
        self.call_mcp("notifications/initialized")
        return init_res

    def list_mcp_tools(self) -> Dict[str, Any]:
        """List available MCP tools for our seat (first page, raw envelope)."""
        return self.call_mcp("tools/list")

    def seat(self) -> SeatRpcClient:
        """An agentkit SeatRpcClient reusing this client's login (and token,
        if already logged in)."""
        session = SeatSession(TenantLogin(self.env_name, self.base_url, self.email, self.password),
                              token=self.token)
        return SeatRpcClient(session)


if __name__ == "__main__":
    env_choice = sys.argv[1] if len(sys.argv) > 1 else "suryodaya"
    print(f"=== Testing AgentSwitch Login from .env ({env_choice.upper()}) ===")
    client = AgentSwitchClient(env_choice)
    token = client.login()
    print(f"Token obtained: {token[:12]}...{token[-8:]}")

    me = client.get_me()
    print(f"User: {me.get('email')} | Role: {me.get('role')}")
    print(f"Allowed Apps: {me.get('allowed_apps')}")
    print(f"Company ID: {me.get('company_id')}")

    print("\n=== Initializing MCP (agentkit SeatRpcClient) ===")
    seat = client.seat()
    seat.initialize()
    catalog = seat.seat_catalog()
    print(f"MCP Tools count (all pages): {len(catalog)}")
    print(f"Available tools (first 10): {sorted(catalog)[:10]}")
