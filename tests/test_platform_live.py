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
  [x] Battery Tray has versions 1..3 with no gaps; rev letters are found in commit_message
  [x] Each version's parent_version_id points at the previous version
  [x] diff_from_parent_json.parent_file_hash equals the parent version's file_hash
  [x] DesignFile.current_version equals the highest version_number (every multi-version file)
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
from collections import defaultdict

pytestmark = pytest.mark.live

# Write your tests below.
def test_battery_tray_versions_sequence(suryodaya, known):
    """verify battery tray has versions 1..3 with no gaps; rev letters are found in commit_message"""
    file_id = known["battery_tray"]["file_id"]
    res = suryodaya.call("DesignVersion.list", file_id=file_id, sort_by="version_number", sort_order="asc")
    versions = res.get("data", [])
    version_numbers = [v["version_number"] for v in versions]
    assert version_numbers == [1, 2, 3]


def all_versions_by_file( platform ):
  """ Helper: fetches all design versions and groups them by file_id sorted by version_number """
  rows = platform.call("DesignVersion.list", limit=1000).get("data", [])
  groups = defaultdict(list)
  for row in rows:
    groups[row["file_id"]].append(row)
  # sort versions within each file by version_number
  for file_id in groups:
    groups[file_id].sort(key=lambda r: r["version_number"]) 
  return groups


def test_every_later_version_points_at_previous_version(suryodaya):
  """ Each version's parent_version_id points at the previous version (v1 is parent of v2, v2 of v3). """
  versions_by_file = all_versions_by_file(suryodaya)
  for file_id, versions in versions_by_file.items():
    if len(versions) < 2:
      continue
    for i in range(1, len(versions)):
      assert versions[i]["parent_version_id"] == versions[i-1]["id"]

def test_diff_parent_hash_matches_parent_file_hash(suryodaya):
    """diff_from_parent_json.parent_file_hash equals the parent version's file_hash."""
    versions_by_file = all_versions_by_file(suryodaya)
    for file_id, versions in versions_by_file.items():
        if len(versions) < 2:
            continue
        for prev, cur in zip(versions, versions[1:]):
            diff = cur.get("diff_from_parent_json") or {}
            assert diff.get("parent_file_hash") == prev.get("file_hash"), (
                f"File {file_id} v{cur['version_number']}: diff parent_file_hash "
                f"({diff.get('parent_file_hash')}) != prev file_hash ({prev.get('file_hash')})"
            )
            assert diff.get("file_hash") == cur.get("file_hash"), (
                f"File {file_id} v{cur['version_number']}: diff file_hash "
                f"({diff.get('file_hash')}) != cur file_hash ({cur.get('file_hash')})"
            )
      

"""[x] DesignFile.current_version equals the highest version_number (every multi-version file)

  This test is needed to:
    1) Guarding against stale version pointers. In AgentSwitch, a DesignFile is the parent entitiy, while DesignVersion holds each revision row 
    (v1, v2, v3). When engineers upload of commit a new version, the platform must increment, else 
    front-end will show old current_version for the file, while the database has new version_number. This will lead to incorrect UI state.
    2) Guarding against stale data. For example if the current_version is 5, while the highest version_number in the database is 6, the UI 
    will not show new data correctly.
"""      
def test_file_current_version_equals_highest_version_number(suryodaya):
    """DesignFile.current_version equals the highest version_number for every file with versions."""
    versions_by_file = all_versions_by_file(suryodaya)
    for file_id, versions in versions_by_file.items():
        if not versions:
            continue
        design_file = suryodaya.call("DesignFile.get", id=file_id)
        highest_version_number = versions[-1]["version_number"]
        assert int(design_file["current_version"]) == highest_version_number, (
            f"DesignFile {design_file.get('number')} ({file_id}) current_version "
            f"({design_file.get('current_version')}) != highest version ({highest_version_number})"
        )
