"""
Autonomous Design Review Agent (Team 21) using NetworkX DAG Task Orchestration.
Pure Python foundation (no LangChain, no LangGraph, no CrewAI).
Coordinates Model Context Protocol (MCP) data gathering, DFM rule evaluation, and artifact generation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import networkx as nx

from as_client import AgentSwitchClient
from llm_gateway import LLMGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class PlanTask:
    id: str
    name: str
    description: str
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    result: Any = None
    completed: bool = False


class DesignReviewAgent:
    """NetworkX DAG-based Autonomous Agent for the Design Review Seat."""

    def __init__(self, tenant: str = "suryodaya"):
        self.tenant = tenant
        self.mcp_client = AgentSwitchClient(tenant)
        self.mcp_client.login()
        self.mcp_client.init_mcp()
        self.gateway = LLMGateway()
        self.dag = nx.DiGraph()

    def build_graph_from_plan(self, tasks: List[PlanTask]):
        """Construct a NetworkX DAG dependency graph from planned tasks."""
        self.dag.clear()
        for t in tasks:
            self.dag.add_node(t.id, task=t)
        for t in tasks:
            for dep in t.dependencies:
                if self.dag.has_node(dep):
                    self.dag.add_edge(dep, t.id)

    def execute_dag(self) -> Dict[str, Any]:
        """Execute tasks in topological order honoring dependency outputs."""
        if not nx.is_directed_acyclic_graph(self.dag):
            raise ValueError("Task plan contains cyclical dependencies!")

        execution_results: Dict[str, Any] = {}
        for node_id in nx.topological_sort(self.dag):
            task: PlanTask = self.dag.nodes[node_id]["task"]
            logging.info(f"Executing Task [{task.id}]: {task.name}")

            # 1. Execute MCP tool if specified
            if task.tool_name:
                try:
                    res = self.mcp_client.call_mcp("tools/call", {
                        "name": task.tool_name,
                        "arguments": task.tool_args
                    })
                    task.result = res.get("result", {}).get("structuredContent", res)
                except Exception as e:
                    logging.error(f"Tool {task.tool_name} failed: {e}")
                    task.result = {"error": str(e)}
            
            task.completed = True
            execution_results[task.id] = task.result

        return execution_results

    def answer_capstone_benchmark(self, query: str = "What changed between rev B and rev C, and are there manufacturability problems in this part?") -> Dict[str, Any]:
        """Full autonomous resolution of the Capstone prompt."""
        logging.info(f"Initiating autonomous resolution for: '{query}'")

        # Step 1: Define the NetworkX DAG Execution Plan
        tasks = [
            # 1. Find active DFM Reviews
            PlanTask(
                id="get_reviews",
                name="Find active DFM reviews",
                description="List in-progress DFM reviews to identify target files and projects",
                tool_name="DesignReview.list",
                tool_args={"status": "in_progress", "limit": 5},
                dependencies=[]
            ),
            # 2. Get Battery Tray File details
            PlanTask(
                id="get_battery_file",
                name="Retrieve Battery Tray DesignFile metadata",
                description="Fetch part count, CAD format, and assembly tree for Battery Tray",
                tool_name="DesignFile.get",
                tool_args={"id": "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"},
                dependencies=[]
            ),
            # 3. Get Revision History (Rev A, B, C)
            PlanTask(
                id="get_versions",
                name="Query revision history for Battery Tray",
                description="Fetch all DesignVersion rows and commit messages",
                tool_name="DesignVersion.list",
                tool_args={"file_id": "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"},
                dependencies=[]
            ),
            # 4. Get DFM Checklists
            PlanTask(
                id="get_checklists",
                name="Fetch sheet metal & machining DFM validation checklists",
                description="Fetch DFM checklist rules to evaluate manufacturing risks",
                tool_name="DesignChecklist.list",
                tool_args={"limit": 5},
                dependencies=[]
            ),
            # 5. Query Historical Feedback & Precedents
            PlanTask(
                id="get_feedback",
                name="Fetch past feedback & supplier tickets",
                description="Check for known issues (cracking, dimensional shifts)",
                tool_name="DesignFeedback.list",
                tool_args={"limit": 20},
                dependencies=[]
            )
        ]

        self.build_graph_from_plan(tasks)
        evidence = self.execute_dag()

        # Step 2: Synthesize findings using LLM Gateway
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
2. Revision Delta (Rev B -> Rev C): Exact geometric, dimensional, and tooling changes (e.g. bend radius, blank length, locator positions).
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
    agent = DesignReviewAgent("suryodaya")
    result = agent.answer_capstone_benchmark()
    print("\n==================== CAPSTONE AGENT EVALUATION RESULT ====================\n")
    print(result["analysis"])
