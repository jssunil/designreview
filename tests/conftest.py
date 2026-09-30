"""
Shared pytest fixtures for the Team 21 test suite.

This file is test *infrastructure* only (login, MCP calls, known record IDs).
It contains no tests. Every test in tests/test_*.py must be written by hand
by a team member -- the brief scores AI-written tests at zero.

Quick reference:
    pytest -m "not live and not agent"   # offline, fast, free
    pytest -m live                        # read-only platform checks
    pytest -m agent                       # full agent runs (LLM cost)
    pytest -m keystone                    # US tenant only
"""

from __future__ import annotations

import json
import os
import ssl
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import pytest

from as_client import get_environment_config  # also loads .env

REPO_ROOT = Path(__file__).resolve().parent.parent
PROOFS_RUNS_DIR = REPO_ROOT / "proofs" / "runs"
TASKS_DIR = REPO_ROOT / "tasks"


# ---------------------------------------------------------------------------
# Known platform records (Suryodaya). Verified 2026-09-24 -- see research.md §3.
# Re-check before relying on them: other teams can change shared data.
# ---------------------------------------------------------------------------
KNOWN = {
    "battery_tray": {
        "file_id": "39b69109-b56a-4bf4-af48-1ecf2b18f8a6",  # DF-2026-00001
        "rev_a_version": 1,
        "rev_b_version": 2,
        "rev_c_version": 3,
        "rev_c_version_id": "85696cb0-8662-44da-8021-2a56453234ed",
    },
    "propeller": {
        "file_id": "1131649b-a525-410a-97e3-e6b995a524fb",  # DF-2026-00012
        "rev_b_version_id": "0cc63461-9080-485c-8f9a-e23d873fee38",  # v1
        "rev_c_version_id": "d9e585a4-d72d-4ae7-be02-35cbb8484927",  # v2
    },
    "bench_vice": {"file_id": "4e6bf256-9e16-4158-bdf5-633b4bc2ecb4"},
    "trolling_propeller": {"file_id": "25247d97-56d0-43e3-9b53-8ac2900df806"},
    "no_version_file": {"file_id": "0bc2b05d-f11f-46b6-9b91-463af65eea8e"},  # DF-2026-00100
    "design_bom_ids": [
        "f82743f6-a82e-47f5-8578-4017ee9f3cd5",  # owned by BT-2400 Base Pan DXF
        "725be709-6e46-46f6-98c2-795a490c4b6f",  # owned by PMP-BRKT Mounting Bracket
    ],
    "review_open": "0100adb6-3b19-4ccb-ac00-2df07436d0b5",  # DR-2026-00084
    "review_completed": "60942da6-420a-4601-8c4a-64a99a977043",  # DR-2026-00098
}


class MCPError(RuntimeError):
    """A JSON-RPC error envelope (arrives with HTTP 200)."""

    def __init__(self, error: Dict[str, Any]):
        super().__init__(f"{error.get('code')}: {error.get('message')}")
        self.error = error


class Platform:
    """Minimal, verified-TLS client for tests: REST + MCP tools/call."""

    def __init__(self, tenant: str):
        cfg = get_environment_config(tenant)
        self.tenant = tenant
        self.base_url = cfg["url"]
        # Verify TLS against the OS trust store (or disable with AS_INSECURE_TLS=1).
        verify_tls: Any = False if os.getenv("AS_INSECURE_TLS") == "1" else ssl.create_default_context()
        self._http = httpx.Client(base_url=cfg["url"], timeout=60, verify=verify_tls)
        resp = self._http.post("/api/auth/login", json={"email": cfg["email"], "password": cfg["password"]})
        resp.raise_for_status()
        self._http.headers["Authorization"] = f"Bearer {resp.json()['token']}"
        self._rpc_id = 0

    # -- REST ---------------------------------------------------------------
    def rest(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Raw REST call; returns the httpx.Response so tests can assert status codes."""
        return self._http.request(method, path, **kwargs)

    # -- MCP ----------------------------------------------------------------
    def rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Raw JSON-RPC call; returns the whole envelope (result or error)."""
        self._rpc_id += 1
        body = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params or {}}
        resp = self._http.post("/api/mcp", json=body)
        resp.raise_for_status()
        return resp.json()

    def call(self, tool: str, **arguments: Any) -> Any:
        """tools/call that returns structuredContent, raising MCPError on an error envelope."""
        env = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        if "error" in env:
            raise MCPError(env["error"])
        result = env.get("result", {})
        if "structuredContent" in result:
            return result["structuredContent"]
        return json.loads(result["content"][0]["text"])

    def tool_names(self) -> set:
        return {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}

    def close(self) -> None:
        self._http.close()


def _has_credentials(tenant: str) -> bool:
    return bool(os.getenv(f"AS_{tenant.upper()}_PASSWORD"))


@pytest.fixture(scope="session")
def suryodaya():
    if not _has_credentials("suryodaya"):
        pytest.skip("AS_SURYODAYA_PASSWORD not set in .env")
    p = Platform("suryodaya")
    yield p
    p.close()


@pytest.fixture(scope="session")
def keystone():
    if not _has_credentials("keystone"):
        pytest.skip("AS_KEYSTONE_PASSWORD not set in .env")
    p = Platform("keystone")
    yield p
    p.close()


@pytest.fixture(scope="session")
def known() -> Dict[str, Any]:
    return KNOWN


@pytest.fixture
def saved_runs() -> list:
    """Paths of saved TaskRun proofs (for offline harness / verifier tests)."""
    return sorted(PROOFS_RUNS_DIR.glob("*.json"))


@pytest.fixture
def load_json():
    def _load(path: Path) -> Any:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    return _load
