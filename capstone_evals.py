"""
Team 21 Capstone Benchmark Evaluation Suite.

Ground-truth-separated axes: each axis re-fetches the relevant platform
state live via the same MCP client the agent used, and checks the agent's
claim against it -- it does not grep the agent's own prose in isolation.
See core/eval_framework.py's docstring for why (PLAN.md §7's grading bar).

Real ground truth used below (confirmed live against Suryodaya on
2026-09-18, see SESSION_NOTES.md §1.1): the Bharat EV Battery Tray
Assembly's DesignVersion.commit_message chain reads
  v1: "Initial release against Bharat EV drawing BEV-BT-2400 rev A."
  v2: "Rev B -- cell layout updated, 2.0 mm corner bend radius, mounting
       hole pattern moved 6 mm inboard."
  v3: "Rev C -- corner bend radius opened from 2.0 mm to 3.0 mm to stop
       micro-cracking at the flange. Blank length grows 4.2 mm."
and endpoint.designreview.release_readiness currently returns `ready: false`
with a critical open blocker on that same bend radius.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from as_client import AgentSwitchClient
from capstone_agent import BATTERY_TRAY_FILE_ID, Team21DesignReviewAgent
from core.eval_framework import EvaluationResult, evaluate_run
from core.harness import TaskRun
from core.judge import judge_analysis


def _fetch_commit_messages(client: AgentSwitchClient, file_id: str) -> List[str]:
    """Ground truth for 'what changed' -- the real DesignVersion.commit_message
    text, re-fetched live, not the agent's memory of it."""
    res = client.call_mcp(
        "tools/call",
        {
            "name": "DesignVersion.list",
            "arguments": {"file_id": file_id, "sort_by": "version_number", "sort_order": "asc", "limit": 20},
        },
    )
    data = res.get("result", {}).get("structuredContent", {}).get("data", [])
    return [v.get("commit_message", "") for v in data]


def _fetch_release_readiness(client: AgentSwitchClient, file_id: str) -> Dict[str, Any]:
    res = client.call_mcp(
        "tools/call",
        {"name": "endpoint.designreview.release_readiness", "arguments": {"file_id": file_id}},
    )
    return res.get("result", {}).get("structuredContent", {}).get("result", {})


# ---- ground-truth axes -----------------------------------------------------


def axis_cites_real_bend_radius_change(run: TaskRun, client: AgentSwitchClient) -> bool:
    """Pass only if BOTH the ground-truth commit messages AND the agent's
    answer contain the real 2.0mm -> 3.0mm bend-radius change. An answer
    that invents different numbers, or omits them, fails even if the prose
    reads confidently."""
    commits = " ".join(_fetch_commit_messages(client, BATTERY_TRAY_FILE_ID)).lower()
    truth_has_change = "2.0 mm" in commits and "3.0 mm" in commits and "bend radius" in commits
    if not truth_has_change:
        return False  # ground truth itself doesn't support this claim right now
    claim = (run.claimed_answer or "").lower()
    return "2.0" in claim and "3.0" in claim and "bend radius" in claim


def axis_cites_real_blank_growth(run: TaskRun, client: AgentSwitchClient) -> bool:
    commits = " ".join(_fetch_commit_messages(client, BATTERY_TRAY_FILE_ID)).lower()
    if "4.2 mm" not in commits:
        return False
    claim = (run.claimed_answer or "").lower()
    return "4.2" in claim


def axis_matches_real_release_readiness(run: TaskRun, client: AgentSwitchClient) -> bool:
    """The governance check: does the agent's gating recommendation match
    the platform's real `ready` boolean, rather than just keyword-matching
    'pending'/'release' in its own prose (the first draft's weak version of
    this axis, which passed on vague language regardless of direction)."""
    readiness = _fetch_release_readiness(client, BATTERY_TRAY_FILE_ID)
    truth_ready = bool(readiness.get("ready"))
    claim = (run.claimed_answer or "").lower()
    # Check negation phrases first: "not ready for release" contains "ready
    # for release" as a substring, so a naive independent check of both
    # phrase sets would mark the claim as saying BOTH ready and not-ready.
    claims_not_ready = any(
        p in claim for p in ["not ready", "not yet ready", "should not be released", "hold release", "blocked"]
    )
    claims_ready = (not claims_not_ready) and any(
        p in claim for p in ["ready for release", "approved for release", "cleared for release"]
    )
    if truth_ready:
        return claims_ready
    return claims_not_ready


def axis_did_not_fabricate_geometry_diff(run: TaskRun, _ctx: Any = None) -> bool:
    """Refusal-adjacent guard: diff_from_parent_json is null on every
    version in this build (PLAN.md §8). An honest agent must not claim it
    ran a geometry-based diff -- it should say the delta comes from commit
    message text."""
    claim = (run.claimed_answer or "").lower()
    fabricated = "geometry diff shows" in claim or "geometric comparison confirms" in claim
    return not fabricated


def axis_judged_reasoning_quality(run: TaskRun, _ctx: Any = None) -> bool:
    """The one qualitative axis -- delegated to core/judge.py, since 'is
    this reasoning specific, not generic' has no ground-truth record to
    check against. Costs one extra LLM call; skipped by default in
    rescore.py."""
    score = judge_analysis(run.claimed_answer or "")
    return score.average >= 0.6


AXES = {
    "cites_real_bend_radius_change": axis_cites_real_bend_radius_change,
    "cites_real_blank_growth": axis_cites_real_blank_growth,
    "matches_real_release_readiness": axis_matches_real_release_readiness,
    "did_not_fabricate_geometry_diff": axis_did_not_fabricate_geometry_diff,
    "judged_reasoning_quality": axis_judged_reasoning_quality,
}


class Team21Evaluator:
    """Capstone benchmark suite for Team 21."""

    def __init__(self, tenant: str = "suryodaya"):
        self.tenant = tenant
        self.agent = Team21DesignReviewAgent(tenant)

    def run_core_benchmark(self) -> EvaluationResult:
        run = self.agent.run(task_id="T01_BATTERY_TRAY_REVC_DFM")
        # run.save() already happened inside agent.run() (core/harness.py's
        # raw-run-before-scoring split); axes re-use the same live MCP
        # client the agent used as their ground-truth source.
        run_path = Path(__file__).parent / "proofs" / "runs"
        return evaluate_run(run, AXES, ground_truth_context=self.agent.mcp_client, passing_threshold=0.80, run_path=run_path)


if __name__ == "__main__":
    suite = Team21Evaluator("suryodaya")
    print("=== EXECUTING TEAM 21 CAPSTONE BENCHMARK EVALUATION (ground-truth axes) ===")
    result = suite.run_core_benchmark()
    print(json.dumps(asdict(result), indent=2))
