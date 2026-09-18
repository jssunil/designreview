"""
AgentSwitch Client Helper for Team 21 (Design Review Seat)
Supports both Suryodaya and Keystone instances.
Loads credentials and URLs from .env file.
Handles login, token caching, REST requests, and MCP tool calls.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    # Load .env from current directory or parent directory
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    # Fallback basic .env loader if python-dotenv is absent
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())


def get_environment_config(env_name: str) -> Dict[str, str]:
    """Retrieve environment credentials and URL from environment variables."""
    env = env_name.lower()
    email = os.getenv("AS_EMAIL", "team21@theschoolofai.in")

    if env == "suryodaya":
        url = os.getenv("AS_SURYODAYA_URL", "https://agentswitch.theschoolofai.in")
        password = os.getenv("AS_SURYODAYA_PASSWORD")
    elif env == "keystone":
        url = os.getenv("AS_KEYSTONE_URL", "https://class.agentswitch.theschoolofai.in")
        password = os.getenv("AS_KEYSTONE_PASSWORD")
    else:
        raise ValueError(f"Unknown environment '{env_name}'. Choose 'suryodaya' or 'keystone'.")

    if not password:
        raise ValueError(
            f"Missing password for '{env}'. Please ensure AS_{env.upper()}_PASSWORD is set in your .env file."
        )

    return {
        "email": email,
        "url": url,
        "password": password,
    }


class AgentSwitchClient:
    def __init__(self, environment: str = "suryodaya"):
        config = get_environment_config(environment)
        self.env_name = environment.lower()
        self.base_url = config["url"]
        self.email = config["email"]
        self.password = config["password"]
        self.token: Optional[str] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self._ctx = ssl._create_unverified_context()

    def login(self) -> str:
        """Authenticate and retrieve Bearer token."""
        url = f"{self.base_url}/api/auth/login"
        payload = json.dumps({"email": self.email, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx) as response:
                data = json.loads(response.read().decode("utf-8"))
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
        req_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        if headers:
            req_headers.update(headers)

        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

        try:
            with urllib.request.urlopen(req, context=self._ctx) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code} for {method} {path}: {error_body}") from e

    def call_mcp(self, method: str, params: Optional[Dict[str, Any]] = None, rpc_id: int = 1) -> Dict[str, Any]:
        """Execute a JSON-RPC 2.0 call against /api/mcp."""
        body = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params or {},
        }
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
        """List available MCP tools for our seat."""
        return self.call_mcp("tools/list")


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

    print("\n=== Initializing MCP ===")
    client.init_mcp()
    tools_res = client.list_mcp_tools()
    tools = tools_res.get("result", {}).get("tools", [])
    print(f"MCP Tools count: {len(tools)}")
    print(f"Available tools (first 10): {[t.get('name') for t in tools[:10]]}")
