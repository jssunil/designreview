"""
Offline tests for agentkit/transport (wire, seat_rpc, rest_reader, journal).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] error.data.code not_found / invalid_arguments / invalid_transition / tool_not_available map to
      MISSING / BAD_ARGS / BAD_TRANSITION / NOT_IN_SEAT; BAD_ARGS carries field_errors
  [ ] HTTP 403 -> DENIED; 429 and 5xx -> FLAKY (retryable); a non-JSON body -> FLAKY
  [ ] result.isError == true raises even though HTTP was 200
  [ ] Payload unwrapping: structuredContent, else JSON inside content[].text, else the raw result
  [ ] HTTP 401 -> exactly one re-login and one resend; a second 401 raises TOKEN_EXPIRED
  [ ] 10 threads hitting 401 at once cause ONE re-login (shared session lock)
  [ ] Reads (.list / .get / declared read-only endpoints) may be retried; writes get one attempt
  [ ] tools/list follows nextCursor across pages and is cached
  [ ] fetch_every_page de-duplicates rows by id and stops at `total` / a short page
  [ ] RestReader: HTTP 404 -> MISSING; list_all pages until total
  [ ] JournaledSeatClient writes one line per call (seq, tool, read, ok, error_kind), never the token
Hint: write a tiny fake Wire -- an object with
  send(method, url, headers, body, *, safe_to_repeat) -> agentkit.transport.WireReply(status, body_bytes)
and build SeatRpcClient(SeatSession(TenantLogin("t", "https://x", "e", "p"), wire=fake)). No network.
"""

import pytest

from agentkit.config import TenantLogin
from agentkit.transport import (BAD_ARGS, DENIED, FLAKY, MISSING, NOT_IN_SEAT, TOKEN_EXPIRED, SeatCallFailure,
                                SeatRpcClient, SeatSession, WireReply)

# Write your tests below.
