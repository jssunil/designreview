"""
Event-sourced task graph engine: a "live graph" the agent can extend while it
runs, instead of a static list executed once in one topological pass.

Provenance: trimmed port of the scheduling design in
D:\\sjk\\eagv3\\S17Code\\s17code\\core\\live_graph\\{core,store}.py --
TaskSpec/GraphPatch/NodeState as an event-sourced mutation model, and
GraphStore.ready() as a "predecessors all succeeded" ready-set scheduler,
instead of nx.topological_sort() run once (the first draft's approach, which
also had no way to stop a failed node's children from running anyway).

Two deliberate deviations from S17Code's design, both scope decisions rather
than oversights:
1. Concurrency uses concurrent.futures.ThreadPoolExecutor, not asyncio,
   because this project's MCP transport (as_client.py) is synchronous
   urllib -- same ready-set scheduling algorithm, a different executor.
2. No Deferred/wait-resume (pausing a node for an external webhook/cron
   callback): every MCP/REST call this agent makes resolves inline, so
   there's nothing to wait on asynchronously.

S13Code's Z3-proved admission oracle (invariants.py) and hedged/speculative
branch racing (speculation.py) were surveyed but not ported -- real
engineering value, but a z3-solver dependency and formal-verification
surface is disproportionate to a ~5-10 node evidence-gathering graph.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

import networkx as nx

logger = logging.getLogger("core.dag_engine")


class NodeState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskSpec:
    """One node in the live graph."""

    id: str
    name: str
    description: str = ""
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    state: NodeState = NodeState.PENDING
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass
class GraphPatch:
    """The only way to mutate the graph -- an event, not a direct edit.
    `add`: new TaskSpecs to insert (their `dependencies` must already exist
    in the graph). `cancel`: node ids whose still-PENDING subtree should be
    marked CANCELLED (used to propagate a failed dependency instead of
    silently running its children with no valid input)."""

    add: List[TaskSpec] = field(default_factory=list)
    cancel: List[str] = field(default_factory=list)


class Planner(Protocol):
    """Optional interleaved-planning hook. Called once per finished node with
    the store and that node's (now-updated) spec; may return a GraphPatch
    adding follow-up nodes or cancelling others. Returning None means
    'nothing to add' -- this is what turns a static task list into a loop
    that can react to what it just learned."""

    def on_outcome(self, store: "GraphStore", node_id: str, spec: TaskSpec) -> Optional[GraphPatch]:
        ...


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


class GraphStore:
    """NetworkX-backed graph with an event-sourced mutation API, a ready-set
    scheduler, and one JSON checkpoint per run (written after every mutation)
    -- this is what gives the harness its "every run logged to disk" record,
    independent of whatever the eval layer later does with it."""

    FORMAT = "designreview-dag-v1"

    def __init__(self, run_id: str, checkpoint_dir: Optional[Path] = None):
        self.run_id = run_id
        self.graph = nx.DiGraph()
        self.checkpoint_dir = checkpoint_dir

    def apply_patch(self, patch: GraphPatch) -> None:
        for spec in patch.add:
            self.graph.add_node(spec.id, spec=spec)
        for spec in patch.add:
            for dep in spec.dependencies:
                if not self.graph.has_node(dep):
                    raise ValueError(f"Task [{spec.id}] depends on unknown task [{dep}]")
                self.graph.add_edge(dep, spec.id)
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("Cyclical dependency detected in task graph!")
        for node_id in patch.cancel:
            if self.graph.has_node(node_id):
                self._cancel_subtree(node_id)
        self._checkpoint()

    def _cancel_subtree(self, node_id: str) -> None:
        spec: TaskSpec = self.graph.nodes[node_id]["spec"]
        if spec.state == NodeState.PENDING:
            spec.state = NodeState.CANCELLED
        for child in self.graph.successors(node_id):
            self._cancel_subtree(child)

    def ready(self) -> List[TaskSpec]:
        """PENDING nodes whose every predecessor has SUCCEEDED."""
        out = []
        for node_id in self.graph.nodes:
            spec: TaskSpec = self.graph.nodes[node_id]["spec"]
            if spec.state != NodeState.PENDING:
                continue
            preds = list(self.graph.predecessors(node_id))
            if all(self.graph.nodes[p]["spec"].state == NodeState.SUCCEEDED for p in preds):
                out.append(spec)
        return out

    def record_outcome(self, node_id: str, result: Any = None, error: Optional[str] = None) -> TaskSpec:
        spec: TaskSpec = self.graph.nodes[node_id]["spec"]
        spec.finished_at = time.time()
        if error is not None:
            spec.state = NodeState.FAILED
            spec.error = error
            self._cancel_subtree(node_id)  # don't run children on a failed dependency
        else:
            spec.state = NodeState.SUCCEEDED
            spec.result = result
        self._checkpoint()
        return spec

    def all_specs(self) -> List[TaskSpec]:
        return [self.graph.nodes[n]["spec"] for n in self.graph.nodes]

    def pending_or_running(self) -> bool:
        return any(s.state in (NodeState.PENDING, NodeState.RUNNING) for s in self.all_specs())

    def results(self) -> Dict[str, Any]:
        """Successful node results, keyed by node id -- what
        capstone_agent.py hands to the LLM as gathered evidence."""
        return {s.id: s.result for s in self.all_specs() if s.state == NodeState.SUCCEEDED}

    def _checkpoint(self) -> None:
        if not self.checkpoint_dir:
            return
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self.checkpoint_dir / f"{self.run_id}.json"
        payload = {
            "format": self.FORMAT,
            "run_id": self.run_id,
            "nodes": [
                {
                    "id": s.id,
                    "name": s.name,
                    "state": s.state.value,
                    "dependencies": s.dependencies,
                    "result": _safe_json(s.result),
                    "error": s.error,
                    "started_at": s.started_at,
                    "finished_at": s.finished_at,
                }
                for s in self.all_specs()
            ],
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


class LiveGraphExecutor:
    """Runs the ready-set on a thread pool, records each outcome, and lets an
    optional Planner add follow-up nodes before the next tick -- interleaved
    planning + execution instead of a single static topological pass."""

    def __init__(
        self,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        planner: Optional[Planner] = None,
        max_workers: int = 4,
        checkpoint_dir: Optional[Path] = None,
    ):
        self.tool_executor = tool_executor
        self.planner = planner
        self.max_workers = max_workers
        self.checkpoint_dir = checkpoint_dir

    def run(self, run_id: str, initial_tasks: List[TaskSpec]) -> GraphStore:
        store = GraphStore(run_id, checkpoint_dir=self.checkpoint_dir)
        store.apply_patch(GraphPatch(add=initial_tasks))

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while store.pending_or_running():
                ready = store.ready()
                if not ready:
                    break  # nothing ready and nothing in flight -> stuck or fully cancelled

                futures = {}
                for spec in ready:
                    spec.state = NodeState.RUNNING
                    spec.started_at = time.time()
                    futures[pool.submit(self._execute_node, spec)] = spec

                for fut in as_completed(futures):
                    spec = futures[fut]
                    try:
                        result = fut.result()
                        store.record_outcome(spec.id, result=result)
                    except Exception as e:
                        logger.error("Node [%s] failed: %s", spec.id, e)
                        store.record_outcome(spec.id, error=str(e))

                    if self.planner:
                        updated = store.graph.nodes[spec.id]["spec"]
                        patch = self.planner.on_outcome(store, spec.id, updated)
                        if patch and (patch.add or patch.cancel):
                            store.apply_patch(patch)

        return store

    def _execute_node(self, spec: TaskSpec) -> Any:
        logger.info("Executing DAG node [%s]: %s", spec.id, spec.name)
        if spec.tool_name and self.tool_executor:
            return self.tool_executor(spec.tool_name, spec.tool_args)
        return None
