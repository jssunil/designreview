"""
Generic NetworkX DAG Task Orchestration Engine.
Allows constructing arbitrary task dependency graphs, resolving them in topological order,
and executing tool calls with zero external framework dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import networkx as nx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class TaskNode:
    id: str
    name: str
    description: str
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    result: Any = None
    completed: bool = False


class DAGOrchestrator:
    """Generic Directed Acyclic Graph (DAG) executor for multi-step tasks."""

    def __init__(self, tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None):
        self.dag = nx.DiGraph()
        self.tool_executor = tool_executor

    def build_dag(self, tasks: List[TaskNode]):
        """Populate DAG nodes and edges from a list of tasks."""
        self.dag.clear()
        for t in tasks:
            self.dag.add_node(t.id, task=t)
        for t in tasks:
            for dep in t.dependencies:
                if self.dag.has_node(dep):
                    self.dag.add_edge(dep, t.id)
                else:
                    raise ValueError(f"Task [{t.id}] depends on unknown task [{dep}]")

        if not nx.is_directed_acyclic_graph(self.dag):
            raise ValueError("Cyclical dependency detected in task graph!")

    def execute(self) -> Dict[str, Any]:
        """Execute all nodes in topological sort order."""
        results: Dict[str, Any] = {}
        for node_id in nx.topological_sort(self.dag):
            task: TaskNode = self.dag.nodes[node_id]["task"]
            logging.info(f"Executing DAG Node [{task.id}]: {task.name}")

            if task.tool_name and self.tool_executor:
                try:
                    task.result = self.tool_executor(task.tool_name, task.tool_args)
                except Exception as e:
                    logging.error(f"Execution failed for [{task.id}]: {e}")
                    task.result = {"error": str(e)}

            task.completed = True
            results[task.id] = task.result

        return results
