"""
Live, READ-ONLY checks against the AgentSwitch DesignReview app.
Markers: live (module-wide); add @pytest.mark.keystone to US-tenant tests.

Author(s): ____________    Written by hand: [ ] yes

Fixtures (see conftest.py): suryodaya, keystone, known
    suryodaya.call("Tool.name", arg=value)  -> structuredContent (raises MCPError on an error envelope)
    suryodaya.rpc("tools/list")             -> raw JSON-RPC envelope
    suryodaya.rest("GET", "/api/...")       -> httpx.Response (for status-code checks)
    suryodaya.tool_names()                  -> set of tool names visible to the seat

Use cases to cover:
 Versions / revisions
  [ ] Battery Tray has versions 1..3 with no gaps; rev letters are found in commit_message
  [ ] Each version's parent_version_id points at the previous version
  [ ] diff_from_parent_json.parent_file_hash equals the parent version's file_hash
  [ ] DesignFile.current_version equals the highest version_number (every multi-version file)
 Release readiness / impact
  [ ] Battery Tray readiness: ready is False, critical_open >= 1, blocker mentions bend radius
  [ ] DF-2026-00100 readiness reports no_version
  [ ] release_impact.get: every listed item's design_bom belongs to the same file   (candidate bug C-1)
  [ ] release_impact.get: counts.design_boms is consistent with the listed DesignBOM ids
  [ ] released_version_id always belongs to the same file                             (candidate bug C-2)
 Permissions / MCP contract
  [ ] A tool not in tools/list returns a JSON-RPC error (code -32602) with HTTP 200
  [ ] An argument that is not in the tool schema is rejected, not ignored
  [ ] The design_admin-only release path is refused for team21
  [ ] A design tool not given to this seat (e.g. endpoint.designreview.ai_review) is refused
 Known stubs (regression)
  [ ] POST /api/designreview/analysis/thickness -> 501
  [ ] POST /api/designreview/analysis/interference -> 501
  [ ] GET /api/designreview/files/{id}/gltf -> 404 (flip the expectation once fixed)
 Keystone
  [ ] The same contract checks pass on Keystone
"""

import pytest

from tests.conftest import MCPError

pytestmark = pytest.mark.live

# Write your tests below.
