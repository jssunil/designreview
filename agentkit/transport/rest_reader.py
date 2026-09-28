"""
Read-only REST access to AgentSwitch, for verifiers and ground-truth probes.

Deliberately a *different code path* from the agent's MCP client: a bug in
how the agent reads the platform must not be able to pass its own check.
Only GET is exposed.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Dict, List, Optional

from agentkit.transport.seat_rpc import (DENIED, FLAKY, MISSING, TOKEN_EXPIRED, UNCLASSIFIED,
                                         SeatCallFailure, SeatSession)
from agentkit.transport.wire import WireUnreachable


class RestReader:
    def __init__(self, session: SeatSession):
        self.session = session

    @classmethod
    def for_tenant(cls, tenant: str) -> "RestReader":
        # A fresh session = the reader's own login, separate from the agent's.
        return cls(SeatSession.for_tenant(tenant))

    def _get_once(self, path: str, params: Optional[Dict[str, Any]]) -> Any:
        url = self.session.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        try:
            reply = self.session.wire.send("GET", url, self.session.auth_headers(), None, safe_to_repeat=True)
        except WireUnreachable as e:
            raise SeatCallFailure(f"GET {path}: {e}", FLAKY, tool=path) from e
        if reply.status == 200:
            try:
                return json.loads(reply.body) if reply.body else {}
            except ValueError as e:
                raise SeatCallFailure(f"GET {path}: non-JSON body", FLAKY, tool=path) from e
        kind = {401: TOKEN_EXPIRED, 403: DENIED, 404: MISSING}.get(reply.status)
        if kind is None:
            kind = FLAKY if reply.status == 429 or reply.status >= 500 else UNCLASSIFIED
        raise SeatCallFailure(f"GET {path} -> HTTP {reply.status} {reply.body[:200]!r}", kind,
                              tool=path, http_status=reply.status)

    def raw(self, path: str, **params: Any) -> Any:
        stale = self.session.ensure_token()
        try:
            return self._get_once(path, params or None)
        except SeatCallFailure as e:
            if e.kind != TOKEN_EXPIRED:
                raise
            self.session.renew(stale)
            return self._get_once(path, params or None)

    def get(self, entity: str, record_id: str) -> Dict[str, Any]:
        return self.raw(f"/api/{entity}/{urllib.parse.quote(record_id)}")

    def list_all(self, entity: str, page_size: int = 200, max_pages: int = 40, **params: Any) -> List[dict]:
        rows: List[dict] = []
        offset = 0
        for _ in range(max_pages):
            payload = self.raw(f"/api/{entity}", limit=page_size, offset=offset, **params)
            batch = payload.get("data", []) if isinstance(payload, dict) else payload
            if not isinstance(batch, list):
                break
            rows.extend(batch)
            offset += len(batch)
            total = payload.get("total") if isinstance(payload, dict) else None
            if not batch or len(batch) < page_size or (total is not None and offset >= int(total)):
                break
        return rows
