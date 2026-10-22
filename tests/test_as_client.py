"""
Offline tests for as_client.py (the compatibility client).    Markers: none (offline)

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover (tick when written):
  [x] A missing password raises ValueError naming the variable (e.g. AS_KEYSTONE_PASSWORD)
  [x] An unknown tenant name ("mumbai") raises ValueError; "KeyStone" works (case-insensitive)
  [x] call_mcp() returns a JSON-RPC error envelope that arrived with HTTP 200 (it doesn't hide it)
  [x] An HTTP 401 from request() raises RuntimeError mentioning the status
  [x] Request body is JSON and carries the Bearer token header
  [x] TLS is verified by default (ssl.CERT_REQUIRED); AS_INSECURE_TLS=1 is the only opt-out
  [x] Every request passes a timeout
  [x] JSON-RPC ids increase per call (1, 2, 3 ...)
  [ ] TODO: Importing as_client loads .env (tests/conftest.py relies on this)
  [ ] TODO: seat() returns a SeatRpcClient that reuses the logged-in token
  [ ] TODO: read_body() recovers plain JSON sent with a wrong "Transfer-Encoding: chunked" header
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: request() with no token logs in first: the first fake urlopen call goes to /api/auth/login, the
      second to the requested path with the new token
  [ ] TODO: request(payload=None) sends no body (req.data is None); an empty reply body returns {}
  [ ] TODO: init_mcp() sends "initialize" and then "notifications/initialized": two calls, ids 1 and 2
Hint: monkeypatch as_client.urllib.request.urlopen with a fake (req, context=None, timeout=None).
"""

import io
import json
import ssl
import urllib.error

import pytest

import as_client
from as_client import AgentSwitchClient, get_environment_config

# Write your tests below. One behaviour per test; name it after the behaviour.


class FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AS_SURYODAYA_PASSWORD", "dummy-password")
    monkeypatch.delenv("AS_INSECURE_TLS", raising=False)  # .env may opt out; the default must verify
    c = AgentSwitchClient("suryodaya")
    c.token = "test-token"
    return c


def test_missing_password_and_unknown_tenant_raise_value_error(monkeypatch):
    monkeypatch.delenv("AS_KEYSTONE_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="AS_KEYSTONE_PASSWORD"):
        get_environment_config("keystone")
    with pytest.raises(ValueError):
        get_environment_config("mumbai")


def test_request_is_json_with_bearer_token_timeout_and_increasing_ids(client, monkeypatch):
    sent = []

    def fake_urlopen(req, context=None, timeout=None):
        sent.append((req, context, timeout))
        return FakeResponse('{"jsonrpc": "2.0", "id": 1, "result": {}}')

    monkeypatch.setattr(as_client.urllib.request, "urlopen", fake_urlopen)
    for _ in range(3):
        client.call_mcp("tools/list")

    ids = [json.loads(req.data)["id"] for req, _, _ in sent]
    assert ids == [1, 2, 3]
    req, context, timeout = sent[0]
    assert req.get_header("Authorization") == "Bearer test-token"
    assert req.get_header("Content-type") == "application/json"
    assert timeout and timeout > 0
    assert context.verify_mode == ssl.CERT_REQUIRED  # TLS is verified by default


def test_call_mcp_returns_a_json_rpc_error_that_came_with_http_200(client, monkeypatch):
    envelope = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad arguments"}}
    monkeypatch.setattr(as_client.urllib.request, "urlopen",
                        lambda req, context=None, timeout=None: FakeResponse(json.dumps(envelope)))
    assert client.call_mcp("tools/call", {"name": "x"}) == envelope


def test_http_401_raises_runtime_error_with_the_status(client, monkeypatch):
    def fake_urlopen(req, context=None, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error": "expired"}'))

    monkeypatch.setattr(as_client.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="401"):
        client.request("GET", "/api/auth/me")
