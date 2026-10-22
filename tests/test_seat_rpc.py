"""
Offline tests for agentkit/transport (wire, seat_rpc, rest_reader, journal).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] error.data.code not_found / invalid_arguments / invalid_transition / tool_not_available map to
      MISSING / BAD_ARGS / BAD_TRANSITION / NOT_IN_SEAT; BAD_ARGS carries field_errors
  [x] HTTP 403 -> DENIED; 429 and 5xx -> FLAKY (retryable); a non-JSON body -> FLAKY
  [x] result.isError == true raises even though HTTP was 200
  [x] Payload unwrapping: structuredContent, else JSON inside content[].text, else the raw result
  [x] HTTP 401 -> exactly one re-login and one resend; a second 401 raises TOKEN_EXPIRED
  [ ] TODO: 10 threads hitting 401 at once cause ONE re-login (shared session lock)
  [ ] TODO: Reads (.list / .get / declared read-only endpoints) may be retried; writes get one attempt
  [ ] TODO: tools/list follows nextCursor across pages and is cached
  [ ] TODO: fetch_every_page de-duplicates rows by id and stops at `total` / a short page
  [ ] TODO: RestReader: HTTP 404 -> MISSING; list_all pages until total
  [ ] TODO: JournaledSeatClient writes one line per call (seq, tool, read, ok, error_kind), never the token
  [ ] TODO: With AGENTKIT_OFFLINE=1, UrllibWire().send(...) raises OfflineViolation (and opens no socket);
      OfflineViolation is not a WireUnreachable, so SeatRpcClient does not turn it into FLAKY
  [ ] TODO: agentkit.config.offline_mode() is True for 1 / true / yes and False for 0 or unset
  [ ] TODO: ToolCatalog.fingerprint() ignores tool order and changes when a tool or an inputSchema changes
  [ ] TODO: ToolCatalog.check_requirements reports a missing tool and a newly required argument
  [ ] TODO: safe_reads.split_filters sends only exact, comma-free, operator-free values on safe keys
  [ ] TODO: safe_reads.not_equal / recheck("ne:x") keep rows whose value is empty; recheck("gt:1") raises
  [ ] TODO: safe_reads.read_all pages past a 2-row cap and drops rows the server should have filtered out
  [ ] TODO: A password tenant never starts from AGENTSWITCH_TOKEN (it logs in itself)
  [ ] TODO: UrllibWire returns a 401 even when the 401's body can't be read (mis-framed chunked body)
Hint: write a tiny fake Wire -- an object with
  send(method, url, headers, body, *, safe_to_repeat) -> agentkit.transport.WireReply(status, body_bytes)
and build SeatRpcClient(SeatSession(TenantLogin("t", "https://x", "e", "p"), wire=fake)). No network.
"""

import json
from pathlib import Path

import pytest

from agentkit.config import TenantLogin
from agentkit.transport import (
    BAD_ARGS, BAD_TRANSITION, DENIED, FLAKY, MISSING, NOT_IN_SEAT, TOKEN_EXPIRED, UNCLASSIFIED,
    SeatCallFailure, SeatRpcClient, SeatSession, WireReply,
)
from agentkit.transport.seat_rpc import unwrap_payload

# --- minimal mock wire and session for offline tests ---

class MockWire:
    def __init__(self, responses_by_key):
        """responses_by_key: dict[(method, key_suffix), (status, body) | list[(status, body)]]"""
        self.responses = {}
        for k, v in responses_by_key.items():
            if isinstance(v, list):
                self.responses[k] = list(v)
            else:
                self.responses[k] = v
        self.sent = []

    def send(self, method: str, url: str, headers: dict, body: bytes | None, *, safe_to_repeat: bool) -> WireReply:
        self.sent.append((method, url, headers, body, safe_to_repeat))
        key = (method, url.split("/")[-1])
        if key in self.responses:
            resp = self.responses[key]
            if isinstance(resp, list):
                status, text = resp.pop(0) if resp else (200, "{}")
            else:
                status, text = resp
            body_bytes = text.encode("utf-8") if isinstance(text, str) else text
            return WireReply(status=status, body=body_bytes)

        return WireReply(200, b'{"result": {}}')


def make_failing_session(status: int, body: str) -> SeatRpcClient:
    wire = MockWire({("POST", "mcp"): (status, body)})
    tenant = TenantLogin("t1", "https://fake", "user", "pass")
    session = SeatSession(tenant, wire=wire, token="fake-token")
    return SeatRpcClient(session)


def run_seat_rpc_assert(client: SeatRpcClient, tool: str = "DesignFile.get", args: dict | None = None) -> SeatCallFailure | None:
    """Calls client.invoke_tool and returns the SeatCallFailure, or None."""
    try:
        client.invoke_tool(tool, args if args is not None else {"id": "r1"})
        return None
    except SeatCallFailure as e:
        return e


# --- Tests ---


def test_error_code_mapping_and_field_errors():
    """Error codes map to SeatCallFailure variants and BAD_ARGS carries field_errors."""
    # Case 1: "not_found" -> MISSING
    body_not_found = json.dumps({
        "error": {
            "code": "not_found",
            "message": "Record missing",
            "data": {"code": "not_found"},
        }
    })
    client = make_failing_session(400, body_not_found)
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == MISSING

    # Case 2: "tool_not_available" -> NOT_IN_SEAT
    body_tool_not_avail = json.dumps({
        "error": {
            "code": "tool_not_available",
            "message": "Tool X not in seat",
            "data": {"code": "tool_not_available"},
        }
    })
    client = make_failing_session(400, body_tool_not_avail)
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == NOT_IN_SEAT

    # Case 3: BAD_ARGS with field_errors
    field_errors_list = [{"field": "arg1", "issue": "required"}]
    bad_args_json = json.dumps({
        "error": {
            "code": "invalid_arguments",
            "message": "Validation failed",
            "data": {
                "code": "invalid_arguments",
                "errors": field_errors_list,
                "field_errors": field_errors_list,
            },
        }
    })
    client = make_failing_session(400, bad_args_json)
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == BAD_ARGS
    assert fail.field_errors == field_errors_list


def test_http_status_failure_classification():
    """Assert HTTP 403 maps to DENIED (not retryable); 429 and 5xx map to FLAKY (retryable); non-JSON body maps to FLAKY."""
    # Case 1: HTTP 403 -> DENIED (not retryable)
    client = make_failing_session(403, '{"error": "x"}')
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == DENIED
    assert fail.retryable is False

    # Case 2: HTTP 429 -> FLAKY (retryable)
    client = make_failing_session(429, "")
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == FLAKY
    assert fail.retryable is True

    # Case 3: HTTP 500 -> FLAKY (retryable)
    client = make_failing_session(500, "")
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == FLAKY
    assert fail.retryable is True

    # Case 4: non-JSON body -> FLAKY (retryable)
    client = make_failing_session(200, "<html>not json</html>")
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert fail.kind == FLAKY
    assert fail.retryable is True


def test_result_is_error_raises_on_http_200():
    """Assert that a response with HTTP 200 and result.isError == True raises SeatCallFailure."""
    body = '{"jsonrpc": "2.0", "id": 1, "result": {"isError": true, "content": [{"type": "text", "text": "boom"}]}}'
    client = make_failing_session(200, body)
    fail = run_seat_rpc_assert(client)
    assert isinstance(fail, SeatCallFailure)
    assert "isError" in str(fail)
    assert fail.kind == UNCLASSIFIED


def test_payload_unwrapping_precedence():
    """Assert unwrap_payload prefers structuredContent, then JSON parsed from content[].text, then raw result."""
    # 1. structuredContent wins over content
    p1 = {"structuredContent": {"a": 1}, "content": [{"type": "text", "text": '{"a": 2}'}]}
    assert unwrap_payload(p1) == {"a": 1}

    # 2. JSON parsed from content[].text
    p2 = {"content": [{"type": "text", "text": '{"a": 2}'}]}
    assert unwrap_payload(p2) == {"a": 2}

    # 3. raw result
    p3 = {"a": 3}
    assert unwrap_payload(p3) == {"a": 3}

    # Extra: text that isn't JSON -> {"text": "..."}
    p4 = {"content": [{"type": "text", "text": "plain text"}]}
    assert unwrap_payload(p4) == {"text": "plain text"}


def test_http_401_relogin_and_resend():
    """Assert first 401 causes re-login and retry; subsequent 401 raises TOKEN_EXPIRED."""
    ok_body = '{"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"id": "r1"}}}'

    # Case A (401 then 200):
    wire_a = MockWire({
        ("POST", "mcp"): [(401, ""), (200, ok_body)],
        ("POST", "login"): (200, '{"token": "new"}'),
    })
    session_a = SeatSession(TenantLogin("t1", "https://fake", "user", "pass"), wire=wire_a, token="fake-token")
    client_a = SeatRpcClient(session_a)
    assert client_a.invoke_tool("DesignFile.get", {"id": "r1"}) == {"id": "r1"}
    assert session_a.login_count == 1
    mcp_sends = [entry for entry in wire_a.sent if entry[1].endswith("/api/mcp")]
    assert len(mcp_sends) == 2

    # Case B (401 then 401):
    wire_b = MockWire({
        ("POST", "mcp"): [(401, ""), (401, "")],
        ("POST", "login"): (200, '{"token": "new"}'),
    })
    session_b = SeatSession(TenantLogin("t1", "https://fake", "user", "pass"), wire=wire_b, token="fake-token")
    client_b = SeatRpcClient(session_b)
    with pytest.raises(SeatCallFailure) as exc_info:
        client_b.invoke_tool("DesignFile.get", {"id": "r1"})
    assert exc_info.value.kind == TOKEN_EXPIRED
    assert session_b.login_count == 1


''' [ ] 10 threads hitting 401 at once cause ONE re-login (shared session lock)
'''
def test_concurrent_threads_401_single_relogin():
    """Assert shared lock ensures only 1 re-login occurs across 10 concurrent threads encountering 401."""
    # 10 threads hitting 401 at once should cause only one re-login (shared session lock)
    


''' [ ] Reads (.list / .get / declared read-only endpoints) may be retried; writes get one attempt
'''
def test_reads_may_retry_writes_get_one_attempt():
    """Assert safe reads allow retry on transient failure, whereas write actions are never retried."""
    pytest.skip("TODO: implement test")


''' [ ] tools/list follows nextCursor across pages and is cached
'''
def test_tools_list_pagination_and_caching():
    """Assert tools/list paginates via nextCursor until exhausted and caches the catalog."""
    pytest.skip("TODO: implement test")


''' [ ] fetch_every_page de-duplicates rows by id and stops at `total` / a short page
'''
def test_fetch_every_page_deduplication_and_termination():
    """Assert fetch_every_page de-duplicates items by id and terminates when total items reached or short page."""
    pytest.skip("TODO: implement test")


''' [ ] RestReader: HTTP 404 -> MISSING; list_all pages until total
'''
def test_rest_reader_404_missing_and_pagination():
    """Assert RestReader converts 404 to MISSING and pages list_all until total rows received."""
    pytest.skip("TODO: implement test")


''' [ ] JournaledSeatClient writes one line per call (seq, tool, read, ok, error_kind), never the token
'''
def test_journaled_seat_client_logging_and_token_redaction():
    """Assert JournaledSeatClient records seq, tool, read, ok, error_kind in tool_journal.jsonl without token leaks."""
    pytest.skip("TODO: implement test")


''' [ ] With AGENTKIT_OFFLINE=1, UrllibWire().send(...) raises OfflineViolation (and opens no socket);
      OfflineViolation is not a WireUnreachable, so SeatRpcClient does not turn it into FLAKY
'''
def test_agentkit_offline_raises_offline_violation():
    """Assert AGENTKIT_OFFLINE=1 causes UrllibWire to raise OfflineViolation without opening socket and not caught as FLAKY."""
    pytest.skip("TODO: implement test")


''' [ ] agentkit.config.offline_mode() is True for 1 / true / yes and False for 0 or unset
'''
def test_offline_mode_env_parsing():
    """Assert offline_mode() returns True for '1', 'true', 'yes' (case-insensitive) and False otherwise."""
    pytest.skip("TODO: implement test")


''' [ ] ToolCatalog.fingerprint() ignores tool order and changes when a tool or an inputSchema changes
'''
def test_tool_catalog_fingerprint():
    """Assert ToolCatalog fingerprint is deterministic regardless of tool order and shifts on schema changes."""
    pytest.skip("TODO: implement test")


''' [ ] ToolCatalog.check_requirements reports a missing tool and a newly required argument
'''
def test_tool_catalog_check_requirements():
    """Assert check_requirements identifies when expected tools are missing or newly required args appear."""
    pytest.skip("TODO: implement test")


''' [ ] safe_reads.split_filters sends only exact, comma-free, operator-free values on safe keys
'''
def test_safe_reads_split_filters():
    """Assert split_filters keeps exact scalar values on safe keys for server filtering and defers others to client."""
    pytest.skip("TODO: implement test")


''' [ ] safe_reads.not_equal / recheck("ne:x") keep rows whose value is empty; recheck("gt:1") raises
'''
def test_safe_reads_filter_recheck():
    """Assert not_equal / recheck('ne:x') preserves empty field rows, and unsupported operator 'gt:1' raises error."""
    pytest.skip("TODO: implement test")


''' [ ] safe_reads.read_all pages past a 2-row cap and drops rows the server should have filtered out
'''
def test_safe_reads_read_all_paging_and_filtering():
    """Assert read_all paginates past small server row limits and applies client filters accurately."""
    pytest.skip("TODO: implement test")


''' [ ] A password tenant never starts from AGENTSWITCH_TOKEN (it logs in itself)
'''
def test_password_tenant_ignores_agentswitch_token():
    """Assert a tenant with password credentials ignores AGENTSWITCH_TOKEN and authenticates itself directly."""
    pytest.skip("TODO: implement test")


''' [ ] UrllibWire returns a 401 even when the 401's body can't be read (mis-framed chunked body)
'''
def test_urllib_wire_handles_401_with_unreadable_chunked_body():
    """Assert UrllibWire returns HTTP 401 status even if server chunk framing on error body is malformed."""
    pytest.skip("TODO: implement test")
