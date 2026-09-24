"""
Script to file platform bugs to AgentSwitch via POST /api/bug-report.
Team 21 (Design Review Seat).

Loads bug definitions from platform_bugs.json.
Checks filing status/flags and skips already filed bugs.
Files only pending bugs and updates platform_bugs.json with the returned Bug ID.
"""

import datetime
import json
from pathlib import Path
from as_client import AgentSwitchClient

BUGS_FILE = Path(__file__).parent / "platform_bugs.json"
MY_FILED_BUGS_FILE = Path(__file__).parent / "myfiledbugs.json"


def sync_and_file_bugs(tenant: str = "suryodaya"):
    if not BUGS_FILE.exists():
        print(f"Error: {BUGS_FILE} does not exist.")
        return

    with open(BUGS_FILE, "r", encoding="utf-8") as f:
        bugs_catalog = json.load(f)

    client = AgentSwitchClient(tenant)
    client.login()

    # Query current filed bugs on the platform to prevent duplicate filings
    print("=== Fetching Existing Bugs (GET /api/bug-report/mine) ===")
    try:
        remote_bugs_resp = client.request("GET", "/api/bug-report/mine")
        remote_bugs = remote_bugs_resp.get("data", [])
        print(f"Found {len(remote_bugs)} existing bugs on platform for this seat.")

        # Save to myfiledbugs.json
        with open(MY_FILED_BUGS_FILE, "w", encoding="utf-8") as f:
            json.dump(remote_bugs_resp, f, indent=4)
    except Exception as e:
        print(f"Warning: Could not fetch remote bugs: {e}")
        remote_bugs = []

    # Map existing remote bugs by page and first line of description for deduplication
    remote_map = {}
    for rb in remote_bugs:
        desc_first_line = (rb.get("description") or "").strip().split("\n")[0]
        page = rb.get("page")
        key = (page, desc_first_line)
        remote_map[key] = rb

    updated_any = False

    for bug in bugs_catalog:
        bug_id = bug.get("id")
        title = bug.get("title")
        payload = bug.get("payload", {})
        page = payload.get("page")
        desc_first_line = (payload.get("description") or "").strip().split("\n")[0]
        key = (page, desc_first_line)

        # 1. Check if already marked filed locally
        if bug.get("filed") and bug.get("filed_bug_id"):
            print(f"[SKIPPED - ALREADY FILED] '{title}' (Bug ID: {bug.get('filed_bug_id')})")
            continue

        # 2. Check if already exists on remote platform
        if key in remote_map:
            matched_rb = remote_map[key]
            remote_id = matched_rb.get("id")
            print(f"[FOUND ON REMOTE] '{title}' matches remote Bug ID: {remote_id}. Updating JSON.")
            bug["filed"] = True
            bug["filed_bug_id"] = remote_id
            bug["filed_at"] = matched_rb.get("created_at")
            updated_any = True
            continue

        # 3. File the new bug
        print(f"\n[FILING NEW BUG] '{title}' ...")
        try:
            res = client.request("POST", "/api/bug-report", payload=payload)
            new_id = res.get("id") or (res.get("data") or {}).get("id")
            print(f"Successfully filed bug! Response ID: {new_id}")
            print(json.dumps(res, indent=2))

            bug["filed"] = True
            bug["filed_bug_id"] = new_id
            bug["filed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            updated_any = True
        except Exception as e:
            print(f"Error filing bug '{title}': {e}")

    # Persist updated status back to platform_bugs.json
    if updated_any:
        with open(BUGS_FILE, "w", encoding="utf-8") as f:
            json.dump(bugs_catalog, f, indent=2)
        print(f"\nUpdated bug status and IDs saved to {BUGS_FILE}")

        # Re-sync myfiledbugs.json
        try:
            remote_bugs_resp = client.request("GET", "/api/bug-report/mine")
            with open(MY_FILED_BUGS_FILE, "w", encoding="utf-8") as f:
                json.dump(remote_bugs_resp, f, indent=4)
            print(f"Synced {MY_FILED_BUGS_FILE} with latest {len(remote_bugs_resp.get('data', []))} filed bugs.")
        except Exception as e:
            print(f"Warning: Could not re-sync myfiledbugs.json: {e}")
    else:
        print("\nAll bugs were already filed. No changes made.")


if __name__ == "__main__":
    import sys
    target_env = sys.argv[1] if len(sys.argv) > 1 else "suryodaya"
    if target_env.lower() == "both":
        print("========== Filing Bugs to KEYSTONE (class.agentswitch) ==========")
        sync_and_file_bugs(tenant="keystone")
        print("\n========== Filing Bugs to SURYODAYA (agentswitch) ==========")
        sync_and_file_bugs(tenant="suryodaya")
    else:
        sync_and_file_bugs(tenant=target_env)

