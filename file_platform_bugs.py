"""
Script to file platform bugs to AgentSwitch via POST /api/bug-report.
Team 21 (Design Review Seat).
"""

from as_client import AgentSwitchClient
import json

def file_bugs():
    client = AgentSwitchClient('suryodaya')
    client.login()

    bug1 = {
        'description': (
            "**Title:** DesignVersion.diff_from_parent_json and file_hash are unpopulated (null) across multi-revision designs\n\n"
            "**Context / Module:** DesignVersion / DesignReview\n\n"
            "**Description:**\n"
            "On multi-revision files such as DF-2026-00001 (BEV-BT-2400 Battery Tray Assembly, v1 -> v3), the revision delta metadata "
            "(diff_from_parent_json), file_hash, and parent_version_id are null across all version rows.\n\n"
            "**Steps to Reproduce:**\n"
            "1. Call DesignVersion.list or GET /api/v1/design_versions for DF-2026-00001.\n"
            "2. Inspect version records (v1, v2, v3).\n\n"
            "**Expected Result:**\n"
            "The platform should pre-compute or populate structured geometric/BOM revision diffs in diff_from_parent_json to enable automated revision comparison between parent and child versions.\n\n"
            "**Actual Result:**\n"
            "Fields are null, forcing agents and users to infer changes solely from free-text commit_message strings."
        ),
        'page': 'DesignVersion',
        'agent_seat': 'designreview',
        'job_id': ''
    }

    bug2 = {
        'description': (
            "**Title:** CAD analysis endpoints return HTTP 501 Not Implemented (OpenCascade missing)\n\n"
            "**Context / Module:** DesignFile / CAD Analysis Pipeline\n\n"
            "**Description:**\n"
            "Invoking REST endpoints /analysis/thickness and /analysis/interference for any uploaded DesignFile returns HTTP 501: "
            "'needs a CAD kernel (OpenCascade), which this build does not ship'. Additionally, has_brep is 0 across all uploaded CAD files.\n\n"
            "**Steps to Reproduce:**\n"
            "1. Call GET /api/v1/design_files/39b69109-b56a-4bf4-af48-1ecf2b18f8a6/analysis/thickness.\n"
            "2. Observe HTTP 501 response.\n\n"
            "**Expected Result:**\n"
            "Geometric DFM analysis endpoints should compute ray-cast wall thickness and interference clashing, or provide simulated rule outputs for active designs.\n\n"
            "**Actual Result:**\n"
            "Endpoints return 501, blocking server-side CAD geometric validation."
        ),
        'page': 'DesignFile:analysis',
        'agent_seat': 'designreview',
        'job_id': ''
    }

    print("=== Filing Bug 1 (Missing diff_from_parent_json) ===")
    try:
        res1 = client.request('POST', '/api/bug-report', payload=bug1)
        print("Bug 1 filed successfully:", json.dumps(res1, indent=2))
    except Exception as e:
        print("Error filing Bug 1:", e)

    print("\n=== Filing Bug 2 (501 OpenCascade CAD kernel) ===")
    try:
        res2 = client.request('POST', '/api/bug-report', payload=bug2)
        print("Bug 2 filed successfully:", json.dumps(res2, indent=2))
    except Exception as e:
        print("Error filing Bug 2:", e)

    print("\n=== Verifying All Filed Bugs (GET /api/bug-report/mine) ===")
    my_bugs = client.request('GET', '/api/bug-report/mine')
    print("Total bugs now on file:", len(my_bugs.get('data', [])))
    for b in my_bugs.get('data', []):
        print(f"- ID: {b.get('id')} | Status: {b.get('status')} | Page: {b.get('page')}")

if __name__ == "__main__":
    file_bugs()
