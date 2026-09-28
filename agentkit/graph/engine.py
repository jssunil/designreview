"""
Live task graph: nodes run when their dependencies allow, in parallel, and
a planner may extend the graph in reaction to what just finished.

Rules the engine enforces (none of them domain-specific):
- A node names a registered *action*, never a raw tool. A patch naming
  anything else is rejected, so no plan or planner can reach a tool the
  pack didn't expose.
- `requires="resolved"`: run only if every dependency RESOLVED, otherwise
  the node is BYPASSED with the reason. `requires="settled"`: run once
  every dependency has finished in any verdict (a partial join -- one bad
  branch can't take the other answers down with it).
- A whole patch is validated before anything changes: unknown action,
  duplicate id, missing dependency, an edge into a node that already
  started, a cycle, or exceeding the node budget -> ValueError, graph
  untouched.
- Transport errors map to verdicts: MISSING / NOT_IN_SEAT / DENIED ->
  DECLINED (the platform said no -- a refusal, not a crash); FLAKY is
  retried per action (never for writes) then FAILED; anything else FAILED.
- A write action in a dry run is DECLINED without being called.
- After every outcome the whole graph is checkpointed (atomic write); a
  checkpoint can be loaded and continued. Nodes that were mid-flight at a
  crash go back to pending on resume.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from agentkit.graph.node_result import (BYPASSED, DECLINED, FAILED, RESOLVED, NodeResult)
from agentkit.record.durable_io import dump_record, load_record
from agentkit.registry import Registry
from agentkit.transport.seat_rpc import DENIED, FLAKY, MISSING, NOT_IN_SEAT, SeatCallFailure

logger = logging.getLogger("agentkit.graph")

CHECKPOINT_FORMAT = "agentkit-graph-v1"
DEFAULT_MAX_NODES = 30
REQUIRES = ("resolved", "settled")
_DECLINE_KINDS = {MISSING: "record not found", NOT_IN_SEAT: "tool not available to this seat",
                  DENIED: "access denied"}


@dataclass
class RunEnv:
    """What an action gets besides its own args."""

    client: Any  # anything with invoke_tool(name, args) (+ offers(tool) for catalogue checks)
    run_id: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    dry_run: bool = True
    tenant: str = ""
    gateway: Any = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskSpec:
    id: str
    action: str
    args: Dict[str, Any] = field(default_factory=dict)
    after: List[str] = field(default_factory=list)
    requires: str = "resolved"
    added_by: str = "plan"  # "plan" | "planner"
    result: Optional[NodeResult] = None
    attempts: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def state(self) -> str:
        if self.result is not None:
            return "done"
        return "running" if self.started_at is not None else "pending"

    @property
    def seconds(self) -> Optional[float]:
        if self.started_at is None or self.finished_at is None:
            return None
        return round(self.finished_at - self.started_at, 3)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: getattr(self, k) for k in ("id", "action", "args", "after", "requires", "added_by",
                                           "attempts", "started_at", "finished_at")}
        d["result"] = self.result.to_dict() if self.result else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskSpec":
        d = dict(d)
        result = d.pop("result", None)
        spec = cls(**d)
        spec.result = NodeResult.from_dict(result) if result else None
        return spec


@dataclass
class GraphPatch:
    """The only way to change the graph. `link` adds (dependency, node)
    edges to nodes that haven't started yet."""

    add: List[TaskSpec] = field(default_factory=list)
    link: List[Tuple[str, str]] = field(default_factory=list)


class Planner(Protocol):
    def on_outcome(self, store: "GraphStore", spec: TaskSpec) -> Optional[GraphPatch]: ...


def _has_cycle(deps: Dict[str, List[str]]) -> bool:
    state: Dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(n: str) -> bool:
        mark = state.get(n)
        if mark == 1:
            return True
        if mark == 2:
            return False
        state[n] = 1
        if any(visit(d) for d in deps.get(n, [])):
            return True
        state[n] = 2
        return False

    return any(visit(n) for n in list(deps))


class GraphStore:
    def __init__(self, run_id: str, registry: Registry, checkpoint_path: Optional[Path] = None,
                 max_nodes: int = DEFAULT_MAX_NODES):
        self.run_id = run_id
        self.registry = registry
        self.checkpoint_path = checkpoint_path
        self.max_nodes = max_nodes
        self.specs: Dict[str, TaskSpec] = {}  # insertion order = plan order
        self._lock = threading.RLock()

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.specs

    def get(self, node_id: str) -> Optional[TaskSpec]:
        return self.specs.get(node_id)

    def results(self) -> Dict[str, NodeResult]:
        with self._lock:
            return {i: s.result for i, s in self.specs.items() if s.result is not None}

    def resolved_data(self) -> Dict[str, Any]:
        return {i: r.data for i, r in self.results().items() if r.verdict == RESOLVED}

    # ---- mutation -----------------------------------------------------------

    def apply(self, patch: GraphPatch) -> None:
        with self._lock:
            new_ids = [s.id for s in patch.add]
            if len(self.specs) + len(new_ids) > self.max_nodes:
                raise ValueError(f"patch would exceed the {self.max_nodes}-node budget")
            for spec in patch.add:
                if spec.action not in self.registry.actions:
                    raise ValueError(f"node {spec.id}: {spec.action!r} is not a registered action")
                if spec.id in self.specs or new_ids.count(spec.id) > 1:
                    raise ValueError(f"duplicate node id {spec.id!r}")
                if spec.requires not in REQUIRES:
                    raise ValueError(f"node {spec.id}: requires must be one of {REQUIRES}")
                for dep in spec.after:
                    if dep not in self.specs and dep not in new_ids:
                        raise ValueError(f"node {spec.id} depends on unknown node {dep!r}")
            for dep, node_id in patch.link:
                target = self.specs.get(node_id) or next((s for s in patch.add if s.id == node_id), None)
                if target is None or (dep not in self.specs and dep not in new_ids):
                    raise ValueError(f"link {dep}->{node_id}: unknown node")
                if target.started_at is not None or target.result is not None:
                    raise ValueError(f"link {dep}->{node_id}: {node_id} already started")

            proposed = {i: list(s.after) for i, s in self.specs.items()}
            proposed.update({s.id: list(s.after) for s in patch.add})
            for dep, node_id in patch.link:
                if dep not in proposed[node_id]:
                    proposed[node_id].append(dep)
            if _has_cycle(proposed):
                raise ValueError("patch introduces a dependency cycle")

            for spec in patch.add:
                self.specs[spec.id] = spec
            for node_id, deps in proposed.items():
                self.specs[node_id].after = deps
            self.checkpoint()

    def claim_ready(self) -> List[TaskSpec]:
        """Bypass nodes whose requirement can no longer be met, then mark
        and return every pending node whose dependencies allow it to run."""
        with self._lock:
            progressed = True
            while progressed:
                progressed = False
                for spec in self.specs.values():
                    if spec.state != "pending" or spec.requires != "resolved":
                        continue
                    deps = [self.specs[d] for d in spec.after]
                    blocking = [d for d in deps if d.result is not None and d.result.verdict != RESOLVED]
                    if blocking:
                        b = blocking[0]
                        spec.result = NodeResult(BYPASSED, reason=f"dependency {b.id} was {b.result.verdict}"
                                                 + (f": {b.result.reason}" if b.result.reason else ""))
                        spec.finished_at = time.time()
                        progressed = True
            ready = []
            for spec in self.specs.values():
                if spec.state != "pending":
                    continue
                if all(self.specs[d].result is not None for d in spec.after):
                    spec.started_at = time.time()
                    ready.append(spec)
            if ready:
                self.checkpoint()
            return ready

    def record(self, spec: TaskSpec, result: NodeResult) -> None:
        with self._lock:
            spec.result = result
            spec.finished_at = time.time()
            self.checkpoint()

    def in_flight(self) -> bool:
        return any(s.state == "running" for s in self.specs.values())

    # ---- persistence --------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {"format": CHECKPOINT_FORMAT, "run_id": self.run_id,
                    "nodes": [s.to_dict() for s in self.specs.values()]}

    def checkpoint(self) -> None:
        if self.checkpoint_path:
            dump_record(self.checkpoint_path, self.to_dict())

    @classmethod
    def from_dict(cls, d: Dict[str, Any], registry: Registry, checkpoint_path: Optional[Path] = None,
                  max_nodes: int = DEFAULT_MAX_NODES) -> "GraphStore":
        if d.get("format") != CHECKPOINT_FORMAT:
            raise ValueError(f"unsupported checkpoint format {d.get('format')!r}")
        store = cls(d["run_id"], registry, checkpoint_path, max_nodes)
        for n in d["nodes"]:
            spec = TaskSpec.from_dict(n)
            if spec.state == "running":  # was mid-flight when the run died
                spec.started_at = None
            store.specs[spec.id] = spec
        return store

    @classmethod
    def load(cls, path: Path, registry: Registry, max_nodes: int = DEFAULT_MAX_NODES) -> "GraphStore":
        return cls.from_dict(load_record(path), registry, checkpoint_path=path, max_nodes=max_nodes)


class LiveGraphExecutor:
    def __init__(self, env: RunEnv, registry: Registry, planner: Optional[Planner] = None,
                 max_workers: int = 4, checkpoint_path: Optional[Path] = None,
                 max_nodes: int = DEFAULT_MAX_NODES, retry_backoff_s: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.env = env
        self.registry = registry
        self.planner = planner
        self.max_workers = max_workers
        self.checkpoint_path = checkpoint_path
        self.max_nodes = max_nodes
        self.retry_backoff_s = retry_backoff_s
        self._sleep = sleep

    def run(self, run_id: str, tasks: List[TaskSpec]) -> GraphStore:
        store = GraphStore(run_id, self.registry, self.checkpoint_path, self.max_nodes)
        store.apply(GraphPatch(add=tasks))
        return self.continue_from(store)

    def continue_from(self, store: GraphStore) -> GraphStore:
        """Run every node that can still run. A planner exception is not
        swallowed: a broken planner should fail the run loudly (the caller
        still saves its record)."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            running: Dict[Future, TaskSpec] = {}
            while True:
                for spec in store.claim_ready():
                    running[pool.submit(self._execute, spec, store)] = spec
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    spec = running.pop(fut)
                    store.record(spec, fut.result())
                    if self.planner:
                        patch = self.planner.on_outcome(store, spec)
                        if patch and (patch.add or patch.link):
                            for new in patch.add:
                                new.added_by = "planner"
                            store.apply(patch)
        store.checkpoint()
        return store

    def _execute(self, spec: TaskSpec, store: GraphStore) -> NodeResult:
        action = self.registry.get_action(spec.action)
        if action.writes and self.env.dry_run:
            return NodeResult(DECLINED, reason="dry run: write action not executed")
        logger.info("node [%s] -> %s", spec.id, spec.action)
        while True:
            spec.attempts += 1
            try:
                return NodeResult.of(action.fn(self.env, spec.args, store.results()))
            except SeatCallFailure as e:
                if e.kind == FLAKY and not action.writes and spec.attempts <= action.retries:
                    self._sleep(self.retry_backoff_s * 2 ** (spec.attempts - 1))
                    continue
                if e.kind in _DECLINE_KINDS:
                    return NodeResult(DECLINED, reason=f"{_DECLINE_KINDS[e.kind]}: {e}", error_kind=e.kind)
                return NodeResult(FAILED, reason=str(e), error_kind=e.kind)
            except Exception as e:  # a bug in an action: record it, keep the other branches
                logger.exception("node [%s] crashed", spec.id)
                return NodeResult(FAILED, reason=f"{type(e).__name__}: {e}", error_kind="internal")
