"""
Core Framework Package for Autonomous Agents.
Pure Python foundation: rate-aware LLM gateway, event-sourced DAG/live-graph
orchestration, a uniform run harness, and ground-truth-separated evaluation.

See each module's docstring for which prior EAGv3 project (D:\\sjk\\eagv3\\...)
its design pattern was ported from.
"""

from core.dag_engine import GraphPatch, GraphStore, LiveGraphExecutor, NodeState, Planner, TaskSpec
from core.economics import CostLedger
from core.eval_framework import EvaluationResult, evaluate_run
from core.harness import Harness, Step, TaskRun
from core.judge import JudgeScore, judge_analysis
from core.llm_gateway import LLMGateway

__all__ = [
    "LLMGateway",
    "CostLedger",
    "TaskSpec",
    "GraphPatch",
    "GraphStore",
    "LiveGraphExecutor",
    "NodeState",
    "Planner",
    "Harness",
    "Step",
    "TaskRun",
    "EvaluationResult",
    "evaluate_run",
    "JudgeScore",
    "judge_analysis",
]
