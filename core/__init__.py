"""
Core Framework Package for Autonomous Agents.
Pure Python foundation (NetworkX DAG orchestration, Multi-Provider Gateway, Evaluation Engine).
"""

from core.dag_engine import DAGOrchestrator, TaskNode
from core.eval_framework import BaseEvaluator, EvaluationResult
from core.llm_gateway import LLMGateway

__all__ = [
    "LLMGateway",
    "DAGOrchestrator",
    "TaskNode",
    "BaseEvaluator",
    "EvaluationResult",
]
