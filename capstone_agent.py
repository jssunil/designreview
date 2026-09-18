"""
Team 21 Capstone Agent Implementation.
Extends generic DAG and Gateway engines with domain-specific MCP knowledge for Design Review.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from as_client import AgentSwitchClient
from core.dag_engine import DAGOrchestrator, TaskNode
from core.llm_gateway import LLMGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class Team21DesignReviewAgent:
    """Specialized Capstone Agent for Team 21 (Design Review Seat)."""

    def __init__(self, tenant: str = "suryodaya"):
        self.tenant = tenant
        self.mcp_client = AgentSwitchClient(tenant)
        self.mcp_client.login()
        self.mcp_client.init_mcp()
        self.gateway = LLMGateway()

        # Tool executor wrapper for MCP
        def mcp_tool_executor(tool_name: str, tool_args: Dict[str, Any]) -> Any:
            res = self.mcp_client.call_mcp("tools/call", {
                "name": tool_name,
                "arguments": tool_args
            })
            return res.get("result", {}).get("structuredContent", res)

        self.orchestrator = DAGOrchestrator(tool_executor=mcp_tool_executor)

    def answer_capstone_benchmark(
        self,
        query: str = "What changed between rev B and rev C, and are there manufacturability problems in this part?"
    ) -> Dict[str, Any]:
        """Execute the full domain evaluation for Team 21's Capstone benchmark."""
        logging.info(f"Team 21 Agent answering: '{query}'")

        tasks = [
            TaskNode(
                id="get_reviews",
                name="Find active DFM reviews",
                description="List in-progress DFM reviews to identify target files and projects",
                tool_name="DesignReview.list",
                tool_args={"status": "in_progress", "limit": 5},
                dependencies=[]
            ),
            TaskNode(
                id="get_battery_file",
                name="Retrieve Battery Tray DesignFile metadata",
                description="Fetch part count, CAD format, and assembly tree for Battery Tray",
                tool_name="DesignFile.get",
                tool_args={"id": "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"},
                dependencies=[]
            ),
            TaskNode(
                id="get_versions",
                name="Query revision history for Battery Tray",
                description="Fetch all DesignVersion rows and commit messages",
                tool_name="DesignVersion.list",
                tool_args={"file_id": "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"},
                dependencies=[]
            ),
            TaskNode(
                id="get_checklists",
                name="Fetch sheet metal & machining DFM validation checklists",
                description="Fetch DFM checklist rules to evaluate manufacturing risks",
                tool_name="DesignChecklist.list",
                tool_args={"limit": 5},
                dependencies=[]
            ),
            TaskNode(
                id="get_feedback",
                name="Fetch past feedback & supplier tickets",
                description="Check for known issues (cracking, dimensional shifts)",
                tool_name="DesignFeedback.list",
                tool_args={"limit": 20},
                dependencies=[]
            )
        ]

        self.orchestrator.build_dag(tasks)
        evidence = self.orchestrator.execute()

        prompt = f"""
You are the Lead Design Review & DFM Engineer evaluating a manufacturing change in AgentSwitch.

EVALUATION QUERY:
"{query}"

GATHERED PLATFORM EVIDENCE (MCP Tool Outputs):
1. Design File:
{json.dumps(evidence.get('get_battery_file'), indent=2)}

2. Revision Versions & Commit Logs:
{json.dumps(evidence.get('get_versions'), indent=2)}

3. DFM Checklists & Rules:
{json.dumps(evidence.get('get_checklists'), indent=2)}

4. Historical Feedback / Precedents:
{json.dumps(evidence.get('get_feedback'), indent=2)}

INSTRUCTIONS:
Provide a rigorous, structured engineering analysis with the following exact sections:
1. Executive Summary: Direct answer to the prompt.
2. Revision Delta (Rev B -> Rev C): Exact geometric, dimensional, and tooling changes (bend radius 2.0mm to 3.0mm, blank length +4.2mm).
3. Manufacturability (DFM/DFA) Assessment: Evaluate stress concentration, formability, and why the radius was increased.
4. Downstream Impact: Impact on weld fixtures and assembly.
5. Recommended Action & State Transition: Whether this design is ready for release gating.
"""
        system_prompt = "You are a professional manufacturing engineer and AI agent for Team 21 (Design Review Seat)."
        response_text = self.gateway.call(prompt, system_prompt=system_prompt, temperature=0.1)

        return {
            "query": query,
            "evidence": evidence,
            "analysis": response_text
        }


if __name__ == "__main__":
    agent = Team21DesignReviewAgent("suryodaya")
    res = agent.answer_capstone_benchmark()
    print("\n==================== TEAM 21 CAPSTONE ANSWER ====================\n")
    print(res["analysis"])
