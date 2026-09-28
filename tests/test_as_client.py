"""
Offline tests for as_client.py (the compatibility client).    Markers: none (offline)

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover (tick when written):
  [ ] A missing password raises ValueError naming the variable (e.g. AS_KEYSTONE_PASSWORD)
  [ ] An unknown tenant name ("mumbai") raises ValueError; "KeyStone" works (case-insensitive)
  [ ] call_mcp() returns a JSON-RPC error envelope that arrived with HTTP 200 (it doesn't hide it)
  [ ] An HTTP 401 from request() raises RuntimeError mentioning the status
  [ ] Request body is JSON and carries the Bearer token header
  [ ] TLS is verified by default (ssl.CERT_REQUIRED); AS_INSECURE_TLS=1 is the only opt-out
  [ ] Every request passes a timeout
  [ ] JSON-RPC ids increase per call (1, 2, 3 ...)
  [ ] Importing as_client loads .env (tests/conftest.py relies on this)
  [ ] seat() returns a SeatRpcClient that reuses the logged-in token
  [ ] read_body() recovers plain JSON sent with a wrong "Transfer-Encoding: chunked" header
Hint: monkeypatch as_client.urllib.request.urlopen with a fake (req, context=None, timeout=None).
"""

import pytest

import as_client
from as_client import AgentSwitchClient, get_environment_config

# Write your tests below. One behaviour per test; name it after the behaviour.
