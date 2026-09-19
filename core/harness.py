"""
Uniform run record: every agent run -- whatever the task, whatever it did --
produces one on-disk artifact before anything scores it.

Provenance: trimmed port of D:\\sjk\\eagv3\\S18Code\\harnesses\\base.py's
Harness protocol + shared Step/TaskRun dataclasses, so scorers "never learn
which harness produced" a run and a raw run can be re-scored later without
re-running the (paid) agent. See rescore.py.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class Step:
    """One recorded action inside a run: a DAG node's outcome, an LLM call,
    or the final answer."""

    kind: str  # "tool_call" | "llm_call" | "answer"
    target: str  # tool name / node id / model name
    ok: bool
    detail: Any = None


@dataclass
class TaskRun:
    """The one uniform record every harness run produces, regardless of task
    or which harness (agent version) produced it."""

    task_id: str
    prompt: str
    steps: List[Step] = field(default_factory=list)
    claimed_answer: Optional[str] = None
    ended: str = "done"  # done | error | no_ready_nodes
    error: Optional[str] = None
    seconds: float = 0.0
    started_at: float = field(default_factory=time.time)

    def add_step(self, kind: str, target: str, ok: bool, detail: Any = None) -> None:
        self.steps.append(Step(kind=kind, target=target, ok=ok, detail=detail))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "steps": [asdict(s) for s in self.steps],
            "claimed_answer": self.claimed_answer,
            "ended": self.ended,
            "error": self.error,
            "seconds": self.seconds,
            "started_at": self.started_at,
        }

    def save(self, proofs_dir: Path) -> Path:
        """Persist the raw run to disk BEFORE any scoring touches it (S18's
        raw-run-then-score split: a bug in scoring logic becomes a free
        rescore against saved evidence, not a re-run of the agent)."""
        proofs_dir.mkdir(parents=True, exist_ok=True)
        ts = int(self.started_at)
        path = proofs_dir / f"{self.task_id}_{ts}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "TaskRun":
        data = json.loads(path.read_text(encoding="utf-8"))
        run = cls(
            task_id=data["task_id"],
            prompt=data["prompt"],
            claimed_answer=data.get("claimed_answer"),
            ended=data.get("ended", "done"),
            error=data.get("error"),
            seconds=data.get("seconds", 0.0),
            started_at=data.get("started_at", 0.0),
        )
        run.steps = [Step(**s) for s in data.get("steps", [])]
        return run


class Harness(Protocol):
    """Anything that can answer one task and return a TaskRun."""

    def run(self, task_id: str, prompt: str) -> TaskRun: ...
