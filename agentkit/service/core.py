"""
The agent as a service: one question in, one audited answer out.

`AgentService.ask(question, tenant)` runs the pack's routed task (plan = "route"): the router picks
the plan and its parameters from the question, the engine runs it, the finding builder and the claim
audit make the answer. Each question gets its own run folder `runs/svc-<id>/`, the same files as a
harness run, so the viewer, `grade` and `replay` work on it.

Rules:
- Dry run only: the service never writes to the platform (run_task with write=False).
- Back-pressure: at most `max_in_flight` questions at a time; one more raises `Busy` (HTTP 429 with
  Retry-After), so a burst waits at the caller instead of piling up here.
- Progress: `on_event(dict)` gets each span start and end (route, node, tool, llm, step) as it
  happens, with names, statuses, times and counts -- never a prompt or a key.
- History: the graph checkpoints of every run go to one SQLite file (`runs/service.sqlite`), so
  another process can list, inspect or fork any run (agentkit.graph.SqliteSaver).
- Offline: `sim=` replays captured fixtures (one file, or a folder of <tenant>.json) with the clock
  frozen at the capture time, as the batch does.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, ContextManager, Dict, Iterator, List, Optional, Tuple

from agentkit.clock import frozen_now
from agentkit.config import default_pack, default_tenant, load_tenants
from agentkit.graph.sqlite_saver import SqliteSaver
from agentkit.harness.run_one import run_task
from agentkit.harness.tasks import ROUTED, TaskDef
from agentkit.playbooks import load_playbooks
from agentkit.registry import Registry, load_pack

logger = logging.getLogger("agentkit.service")

MAX_QUESTION_CHARS = 2000
RUN_PREFIX = "svc-"
HISTORY_FILE = "service.sqlite"
TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EVENT_KINDS = ("node", "tool", "llm", "step")
Event = Dict[str, Any]


class Busy(Exception):
    """Too many questions in flight: try again after `retry_after` seconds."""

    def __init__(self, retry_after: int = 5):
        super().__init__(f"busy: retry after {retry_after} s")
        self.retry_after = retry_after


class BadRequest(ValueError):
    """The question or the tenant is not acceptable (HTTP 400)."""


class _SpanEvents:
    """A Tracer handler that turns span starts and ends into progress events."""

    def __init__(self, emit: Callable[[Event], None]):
        self.emit = emit

    def __getattr__(self, method: str) -> Callable[[Any], None]:
        # on_<run_type>_start / _end / _error, for every run type
        if not method.startswith("on_"):
            raise AttributeError(method)
        phase = method.rsplit("_", 1)[-1]
        return lambda span: self._send(phase, span)

    def _send(self, phase: str, span: Any) -> None:
        if span.kind not in EVENT_KINDS:
            return
        event: Event = {"event": f"{span.kind}_{'start' if phase == 'start' else 'end'}", "name": span.name}
        if phase != "start":
            event.update(status=span.status, duration_ms=span.duration_ms)
            if span.error:
                event["error"] = span.error
        attrs = {k: v for k, v in (span.attrs or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}
        if attrs:
            event["attrs"] = attrs
        self.emit(event)


class AgentService:
    def __init__(self, pack: Optional[str] = None, runs_dir: Optional[Path] = None, *, sim: Optional[Path] = None,
                 skip_llm: bool = False, max_in_flight: int = 4, retry_after_s: int = 5,
                 registry: Optional[Registry] = None, tenants: Optional[List[str]] = None, history: bool = True):
        from agentkit.harness.batch import RUNS_DIR

        self.pack = pack or default_pack()
        self.registry = registry or load_pack(self.pack)
        self.runs_dir = Path(runs_dir or RUNS_DIR)
        self.sim = Path(sim) if sim else None
        self.skip_llm = skip_llm
        self.max_in_flight = max(1, max_in_flight)
        self.retry_after_s = retry_after_s
        self.tenants = list(tenants) if tenants is not None else sorted(load_tenants().tenants)
        self.default_tenant = default_tenant() if tenants is None else self.tenants[0]
        self.history = SqliteSaver(self.runs_dir / HISTORY_FILE) if history else None
        self._slots = threading.BoundedSemaphore(self.max_in_flight)
        self._lock = threading.Lock()
        self.in_flight = 0

    # ---- what the service offers ----------------------------------------------

    def playbooks(self) -> List[Dict[str, Any]]:
        """The live use cases: the questions this service answers today."""
        directory = self.registry.extras.get("playbooks_dir")
        books = load_playbooks(Path(directory)) if directory else []
        return [{"id": b.id, "title": b.title, "plan": b.plan, "questions": b.questions, "writes": b.writes}
                for b in books if b.status == "live"]

    def health(self) -> Dict[str, Any]:
        return {"ok": True, "pack": self.pack, "mode": "sim" if self.sim else "live", "llm": not self.skip_llm,
                "in_flight": self.in_flight, "max_in_flight": self.max_in_flight, "tenants": self.tenants}

    # ---- one question -----------------------------------------------------------

    def check(self, question: Any, tenant: Any) -> Tuple[str, str]:
        if not isinstance(question, str) or not question.strip():
            raise BadRequest("question: a non-empty string is required")
        if len(question) > MAX_QUESTION_CHARS:
            raise BadRequest(f"question: at most {MAX_QUESTION_CHARS} characters")
        tenant = self.default_tenant if tenant in (None, "") else tenant
        if not isinstance(tenant, str) or not TENANT_RE.match(tenant) or tenant not in self.tenants:
            raise BadRequest(f"tenant: one of {self.tenants}")
        return question.strip(), tenant

    def ask(self, question: Any, tenant: Any = None, on_event: Optional[Callable[[Event], None]] = None) -> Dict[str, Any]:
        """Route, run and answer one question (dry run). Raises BadRequest or Busy; a failure inside the
        run is not raised -- it is in the result (ended = "error") and in the run folder."""
        question, tenant = self.check(question, tenant)
        with self.slot():
            return self.run(question, tenant, on_event)

    @contextlib.contextmanager
    def slot(self) -> Iterator[None]:
        """One of the `max_in_flight` places, or Busy at once (no queue here)."""
        if not self._slots.acquire(blocking=False):
            raise Busy(self.retry_after_s)
        with self._lock:
            self.in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self.in_flight -= 1
            self._slots.release()

    def run(self, question: str, tenant: str, on_event: Optional[Callable[[Event], None]] = None) -> Dict[str, Any]:
        """One run for a checked question, inside a slot the caller holds (see ask)."""
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_id = f"{RUN_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
        run_dir = self.runs_dir / run_id
        task = TaskDef(id="ask", pack=self.pack, plan=ROUTED, prompt=question, tenants=[tenant],
                       source="agentkit.service")
        client, clock = self._platform(tenant)
        emit = _quiet(on_event) if on_event else None
        handlers = [_SpanEvents(emit)] if emit else []
        if emit:
            emit({"event": "run_start", "run_id": run_id, "tenant": tenant})
        with clock:
            run = run_task(task, tenant, run_dir, run_id, skip_llm=self.skip_llm, registry=self.registry,
                           client=client, retry_backoff_s=0 if self.sim else 1.0, trace_handlers=handlers,
                           saver=self.history.for_thread(run_id) if self.history else None)
        finding = run.finding or {}
        return {"run_id": run_id, "tenant": tenant, "question": question, "ended": run.ended,
                "answer": run.claimed_answer, "answer_source": run.answer_source, "route": run.route,
                "outcome": finding.get("outcome"), "finding": run.finding, "warnings": run.warnings,
                "error": run.error, "cost_usd": _cost(run_dir), "seconds": run.seconds}

    def _platform(self, tenant: str) -> Tuple[Any, ContextManager[Any]]:
        """(client, clock) for one run: a fresh replay of the tenant's fixture with its capture time,
        or (None, no change) so run_task logs in to the live platform."""
        if self.sim is None:
            return None, contextlib.nullcontext()
        from agentkit.sim import SimPlatform

        path = self.sim / f"{tenant}.json" if self.sim.is_dir() else self.sim
        if not path.exists():
            raise BadRequest(f"tenant: no fixture {path.name} in {self.sim}")
        platform = SimPlatform(path, (), self.registry.read_only)
        return platform, frozen_now(platform.fixture.captured_at)

    # ---- earlier answers ----------------------------------------------------------

    def _run_dir(self, run_id: str) -> Optional[Path]:
        """The folder of one of this service's runs, or None (unknown id, or not a service run)."""
        if not re.match(r"^svc-[A-Za-z0-9._-]{1,120}$", run_id) or ".." in run_id:
            return None
        run_dir = (self.runs_dir / run_id).resolve()
        if run_dir.parent != self.runs_dir.resolve() or not (run_dir / "taskrun.json").is_file():
            return None
        return run_dir

    def run_record(self, run_id: str) -> Optional[Dict[str, Any]]:
        run_dir = self._run_dir(run_id)
        return json.loads((run_dir / "taskrun.json").read_text(encoding="utf-8")) if run_dir else None

    def run_history(self, run_id: str) -> Optional[List[Dict[str, Any]]]:
        """The checkpoints of one run, newest first, without the graphs; None for an unknown run."""
        if self._run_dir(run_id) is None or self.history is None:
            return None
        return [{"id": cp.id, "parent_id": cp.parent_id, "ts": cp.ts, "source": cp.source, "step": cp.step,
                 "nodes": cp.metadata.get("nodes")} for cp in self.history.for_thread(run_id).list()]


def _quiet(on_event: Callable[[Event], None]) -> Callable[[Event], None]:
    """A listener that fails (e.g. the caller hung up) never stops the run."""
    def emit(event: Event) -> None:
        try:
            on_event(event)
        except Exception:  # noqa: BLE001
            logger.debug("event listener failed", exc_info=True)
    return emit


def _cost(run_dir: Path) -> float:
    try:
        ledger = json.loads((run_dir / "llm_ledger.json").read_text(encoding="utf-8"))
        return float((ledger.get("summary") or {}).get("cost_usd_estimated") or 0.0)
    except (OSError, ValueError):
        return 0.0
