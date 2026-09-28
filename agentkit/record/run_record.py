"""
TaskRun: the one on-disk record every agent run produces, written BEFORE
anything scores it.

Two ways to persist it:

- `open_in(run_dir)` / `save_in(run_dir)` -- the harness layout:
  runs/<run_id>/taskrun.json. `open_in` writes the record with
  ended="running" before any work starts, so a crash leaves evidence
  ("still running" = crashed) instead of nothing; `save_in` overwrites it
  atomically at the end.
- `save(proofs_dir)` -- the first draft's flat layout
  (<proofs_dir>/<task_id>_<started_at>.json), kept so existing callers and
  hand-written tests keep working. Also atomic now.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from agentkit.record.durable_io import dump_record, load_record

TASKRUN_FILE = "taskrun.json"
ENDED_STATES = ("running", "done", "error", "no_ready_nodes")


@dataclass
class Step:
    """One recorded action inside a run: a graph node's outcome, an LLM
    call, or the final answer. The first four fields are the original
    shape; the rest are optional detail."""

    kind: str  # "tool_call" | "llm_call" | "answer" | an action name
    target: str  # node id / tool name / model name
    ok: bool
    detail: Any = None
    status: Optional[str] = None  # node verdict, e.g. resolved/declined/failed
    reason: Optional[str] = None
    error_kind: Optional[str] = None  # SeatCallFailure.kind when a tool failed
    attempts: int = 1
    added_by: Optional[str] = None  # "plan" | "planner"
    seconds: Optional[float] = None


_STEP_FIELDS = {f.name for f in fields(Step)}


@dataclass
class TaskRun:
    """The uniform record every run produces, whatever the task or agent."""

    task_id: str
    prompt: str
    steps: List[Step] = field(default_factory=list)
    claimed_answer: Optional[str] = None
    ended: str = "done"  # running | done | error | no_ready_nodes
    error: Optional[str] = None
    seconds: float = 0.0
    started_at: float = field(default_factory=time.time)
    # --- harness fields (optional for the flat legacy layout) ---
    run_id: Optional[str] = None
    tenant: Optional[str] = None
    dry_run: bool = False
    harness_task: Optional[str] = None  # task-file id when launched by the harness
    pack: Optional[str] = None
    finding: Optional[Dict[str, Any]] = None  # the gradable, code-computed result
    answer_source: Optional[str] = None  # "llm" | "template"
    warnings: List[str] = field(default_factory=list)

    def add_step(self, kind: str, target: str, ok: bool, detail: Any = None, **extra: Any) -> Step:
        step = Step(kind=kind, target=target, ok=ok, detail=detail,
                    **{k: v for k, v in extra.items() if k in _STEP_FIELDS})
        self.steps.append(step)
        return step

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # ---- harness layout ---------------------------------------------------

    def open_in(self, run_dir: Path) -> Path:
        """Write the record with ended="running" before any work starts."""
        self.ended = "running"
        return self.save_in(run_dir)

    def save_in(self, run_dir: Path) -> Path:
        return dump_record(Path(run_dir) / TASKRUN_FILE, self.to_dict())

    def finish(self, run_dir: Optional[Path] = None, *, error: Optional[str] = None) -> Optional[Path]:
        """Stamp the end state and duration; save if a run_dir is given."""
        if error is not None:
            self.ended, self.error = "error", error
        elif self.ended == "running":
            self.ended = "done"
        self.seconds = round(time.time() - self.started_at, 2)
        return self.save_in(run_dir) if run_dir is not None else None

    # ---- legacy flat layout ------------------------------------------------

    def legacy_filename(self) -> str:
        return f"{self.task_id}_{int(self.started_at)}.json"

    def save(self, proofs_dir: Path) -> Path:
        """Persist to <proofs_dir>/<task_id>_<started_at>.json (atomic)."""
        return dump_record(Path(proofs_dir) / self.legacy_filename(), self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "TaskRun":
        path = Path(path)
        if path.is_dir():
            path = path / TASKRUN_FILE
        data = load_record(path)
        known = {f.name for f in fields(cls)}
        steps = [Step(**{k: v for k, v in s.items() if k in _STEP_FIELDS}) for s in data.pop("steps", [])]
        run = cls(**{k: v for k, v in data.items() if k in known})
        run.steps = steps
        return run


class Harness(Protocol):
    """Anything that can answer one task and return a TaskRun."""

    def run(self, task_id: str, prompt: str) -> TaskRun: ...
