"""
Team 21 Capstone Agent Implementation.
Wires the generic core/ framework (LLMGateway + LiveGraphExecutor) to
Design-Review-specific MCP knowledge: gather evidence about a DesignFile's
revision history and release readiness, then synthesize a manufacturability
judgement. Conforms to core.harness.Harness (`run(task_id, prompt) -> TaskRun`).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from as_client import AgentSwitchClient
from core.dag_engine import GraphPatch, GraphStore, LiveGraphExecutor, TaskSpec
from core.harness import TaskRun
from core.llm_gateway import LLMGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("capstone_agent")

PROOFS_DIR = Path(__file__).parent / "proofs" / "runs"
CHECKPOINT_DIR = Path(__file__).parent / "proofs" / "checkpoints"

# The one file on the platform with a genuine multi-revision commit history
# (Bharat EV Battery Tray Assembly, DF-2026-00001) -- see PLAN.md §8 /
# SESSION_NOTES.md §1.1.
BATTERY_TRAY_FILE_ID = "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"

DEFAULT_PROMPT = "What changed between rev B and rev C, and are there manufacturability problems in this part?"


class DesignReviewPlanner:
    """Interleaved planner: after the release-readiness node comes back, adds
    a follow-up node to fetch the *specific* blocking checklist category
    instead of relying on a blind, fixed task list. This is what replaces
    the first draft's static 5-node run with an agent that reacts to what it
    just learned (PLAN.md's "hold the goal across many steps")."""

    def __init__(self) -> None:
        self._added_followup = False
        self._file_id: Optional[str] = None

    def on_outcome(self, store: GraphStore, node_id: str, spec: TaskSpec) -> Optional[GraphPatch]:
        if node_id == "get_readiness" and spec.state.value == "succeeded" and not self._added_followup:
            # endpoint.designreview.release_readiness's structuredContent is
            # {"status": "ok", "result": {"ready":.., "blockers":[...], "reason_codes":[...], ...}}
            readiness = (spec.result or {}).get("result", {})
            blockers = readiness.get("blockers") or readiness.get("reason_codes") or []
            if blockers:
                self._added_followup = True
                return GraphPatch(
                    add=[
                        TaskSpec(
                            id="get_blocking_checklists",
                            name="Fetch failed DFM checklist results for this file",
                            tool_name="DesignChecklistResult.list",
                            tool_args={"file_id": self._file_id, "overall_result": "fail", "limit": 10},
                            dependencies=["get_readiness"],
                        )
                    ]
                )
        return None

    def bind(self, file_id: str) -> "DesignReviewPlanner":
        self._file_id = file_id
        return self


class Team21DesignReviewAgent:
    """Specialized Capstone Agent for Team 21 (Design Review Seat)."""

    def __init__(self, tenant: str = "suryodaya"):
        self.tenant = tenant
        self.mcp_client = AgentSwitchClient(tenant)
        self.mcp_client.login()
        self.mcp_client.init_mcp()
        self.gateway = LLMGateway()

    def _mcp_tool_executor(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        res = self.mcp_client.call_mcp("tools/call", {"name": tool_name, "arguments": tool_args})
        if "error" in res:
            # JSON-RPC errors return HTTP 200 -- must check the envelope, not
            # just the status code (PLAN.md §3).
            raise RuntimeError(f"MCP error calling {tool_name}({tool_args}): {res['error']}")
        result = res.get("result", {})
        return result.get("structuredContent", result)

    def run(self, task_id: str = "T01_BATTERY_TRAY_REVC_DFM", prompt: str = DEFAULT_PROMPT,
            file_id: str = BATTERY_TRAY_FILE_ID) -> TaskRun:
        """Execute the full domain evaluation for Team 21's Capstone
        benchmark. Conforms to core.harness.Harness."""
        logger.info("Team 21 Agent answering: '%s'", prompt)
        t0 = time.time()
        run = TaskRun(task_id=task_id, prompt=prompt)

        # Tool names/params below are the real scoped MCP catalogue
        # (Entity.verb, flat kwargs -- confirmed 2026-09-18 via a live
        # `tools/list` call), NOT the generic list/get/filters shape in the
        # repo's static agentswitch_tools.json/tools.json, which turned out
        # to describe a different tool surface than what's actually exposed
        # to this seat over MCP.
        initial_tasks = [
            TaskSpec(
                id="get_file",
                name="Retrieve DesignFile metadata",
                tool_name="DesignFile.get",
                tool_args={"id": file_id},
            ),
            TaskSpec(
                id="get_versions",
                name="Query revision history and commit messages",
                tool_name="DesignVersion.list",
                tool_args={"file_id": file_id, "sort_by": "version_number", "sort_order": "asc", "limit": 20},
            ),
            TaskSpec(
                id="get_readiness",
                name="Read aggregated release-readiness evidence",
                tool_name="endpoint.designreview.release_readiness",
                tool_args={"file_id": file_id},
            ),
            TaskSpec(
                id="get_feedback",
                name="Fetch past feedback / defect precedents for this file",
                tool_name="DesignFeedback.list",
                tool_args={"file_id": file_id, "limit": 20},
            ),
        ]

        executor = LiveGraphExecutor(
            tool_executor=self._mcp_tool_executor,
            planner=DesignReviewPlanner().bind(file_id),
            checkpoint_dir=CHECKPOINT_DIR,
        )
        run_id = f"{task_id}_{uuid.uuid4().hex[:8]}"
        store = executor.run(run_id, initial_tasks)

        for spec in store.all_specs():
            ok = spec.state.value == "succeeded"
            run.add_step(kind="tool_call", target=spec.id, ok=ok, detail=spec.result if ok else spec.error)

        evidence = store.results()

        synthesis_prompt = f"""
You are the Lead Design Review & DFM Engineer evaluating a manufacturing change in AgentSwitch.

EVALUATION QUERY:
"{prompt}"

GATHERED PLATFORM EVIDENCE (MCP Tool Outputs):
1. Design File:
{json.dumps(evidence.get('get_file'), indent=2, default=str)}

2. Revision Versions & Commit Logs:
{json.dumps(evidence.get('get_versions'), indent=2, default=str)}

3. Release Readiness:
{json.dumps(evidence.get('get_readiness'), indent=2, default=str)}

4. Failed DFM Checklist Results (if any):
{json.dumps(evidence.get('get_blocking_checklists'), indent=2, default=str)}

5. Historical Feedback / Precedents:
{json.dumps(evidence.get('get_feedback'), indent=2, default=str)}

INSTRUCTIONS:
Provide a rigorous, structured engineering analysis with the following exact
sections. If the evidence above does not actually support a claim (e.g. no
commit message describes a geometric change, or release-readiness data is
missing), say so explicitly -- do not fabricate a confident-sounding answer.
1. Executive Summary: Direct answer to the prompt.
2. Revision Delta: Concrete geometric, dimensional, and tooling changes found
   in commit_message text across the version chain (diff_from_parent_json is
   not populated in this build -- do not claim it is).
3. Manufacturability (DFM/DFA) Assessment: stress concentration, formability,
   tolerance risk implied by the revision delta.
4. Downstream Impact: fixtures/tooling/assembly impact, if evidenced.
5. Recommended Action & State Transition: whether this design is ready for
   release gating, grounded in the actual release_readiness evidence above.
"""
        system_prompt = "You are a professional manufacturing engineer and AI agent for Team 21 (Design Review Seat)."
        try:
            response_text = self.gateway.call(synthesis_prompt, system_prompt=system_prompt, temperature=0.1, tier="quality")
            run.claimed_answer = response_text
            run.add_step(kind="llm_call", target="synthesis", ok=True)
        except Exception as e:
            run.error = str(e)
            run.ended = "error"
            run.add_step(kind="llm_call", target="synthesis", ok=False, detail=str(e))

        run.seconds = round(time.time() - t0, 2)
        run.save(PROOFS_DIR)
        return run

    # Kept for readability at call sites that prefer a domain-named entrypoint.
    def answer_capstone_benchmark(self, query: str = DEFAULT_PROMPT) -> TaskRun:
        return self.run(prompt=query)


if __name__ == "__main__":
    agent = Team21DesignReviewAgent("suryodaya")
    result = agent.run()
    print("\n==================== TEAM 21 CAPSTONE ANSWER ====================\n")
    print(result.claimed_answer)
    print("\n--- Cost ledger ---")
    print(agent.gateway.ledger.summary())
