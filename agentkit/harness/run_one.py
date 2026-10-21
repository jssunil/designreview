"""
Run ONE task into ONE run folder -- the agent side of the harness.

    python -m agentkit.harness.run_one --task <task.toml> --tenant <tenant> \
        --run-dir runs/<run_id> --run-id <run_id> [--skip-llm] [--write]

Writes, in order: taskrun.json (ended="running", before any work) and an
empty tool_journal.jsonl, then journal lines and graph.json as the plan runs,
and finally telemetry.json (the run tree: task -> node -> tool / llm spans;
agentkit.telemetry -- never graded), then taskrun.json again
with the finding, answer and ended=done|error, and llm_ledger.json.
The batch runner starts this in a subprocess, so an agent crash can never
take the harness down with it; the running-record is the evidence either way.
A dry run unless BOTH --write is given and the task sets allow_writes = true;
a dry run never calls a write action (the engine records its preview instead).

Approval gate (a write run only):
    python -m agentkit.harness.run_one ... --write --require-approval
stops before every real write: the node pauses with the write's preview,
the run ends with ended="paused" (no answer yet), and graph.json keeps the
paused state. A person reads the preview (taskrun.json steps, or the viewer)
and then
    python -m agentkit.harness.run_one --resume runs/<run_id> --approve <node> [--reject <node> ...]
continues the same run folder: approved writes run, rejected ones end
DECLINED ("a person did not approve this write"), and the run finishes with
its finding and answer as usual. Resuming appends to the journal and
rewrites taskrun.json and telemetry.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from agentkit.agents import answer_with_pack, plan_executor, record_steps, run_plan
from agentkit.config import default_tenant
from agentkit.graph import GraphStore, RunEnv, load_plan
from agentkit.harness.tasks import ROUTED, TaskDef, load_task
from agentkit.llm import LLMGateway
from agentkit.record import TaskRun, dump_record, load_record
from agentkit.record.durable_io import durable_write_text, read_jsonl
from agentkit.registry import Registry, load_pack
from agentkit.transport import SeatRpcClient
from agentkit.telemetry import Tracer, export_run, span, use_tracer
from agentkit.telemetry.otlp import export_from_env
from agentkit.transport.journal import JOURNAL_FILE, JournaledSeatClient

GRAPH_FILE = "graph.json"
LEDGER_FILE = "llm_ledger.json"
CALLS_FILE = "llm_calls.jsonl"  # what the model saw and said, per attempt (AGENTKIT_LOG_PROMPTS=0: not written)


def save_llm_records(gateway: Any, run_dir: Path, *, keep_earlier: bool = False) -> None:
    """The ledger (tokens, cost) and the call log (prompts and replies) of this run's gateway.
    `keep_earlier`: a resumed run adds its calls after the ones already in the run folder."""
    ledger = getattr(gateway, "ledger", None)
    if ledger is None:
        return
    dump_record(run_dir / LEDGER_FILE, {"summary": ledger.summary(),
                                       "calls": [e.__dict__ for e in ledger.entries]})
    if os.environ.get("AGENTKIT_LOG_PROMPTS", "1") == "0":
        return
    rows = read_jsonl(run_dir / CALLS_FILE) if keep_earlier else []
    rows += [c.__dict__ for c in getattr(ledger, "calls", [])]
    if rows:
        durable_write_text(run_dir / CALLS_FILE, "".join(json.dumps(r, default=str) + "\n" for r in rows))


def describe_catalog(client: Any, registry: Registry) -> Optional[dict]:
    """The seat catalogue's fingerprint and any problem with the tools the pack
    depends on (registry.extras["required_tools"]); None if the client has no
    catalogue (a hand-made test double)."""
    catalog = getattr(client, "catalog", None)
    if not callable(catalog):
        return None
    cat = catalog()
    return {**cat.summary(), "problems": cat.check_requirements(registry.extras.get("required_tools") or {})}


def plan_and_params(task: TaskDef, registry: Registry, client: Any,
                    route: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]]]:
    """(plan name, plan params, routing decision). A task with a plan runs it as written;
    a task without one is routed by the pack's router from its prompt -- or, when
    resuming or replaying, by the decision the original run recorded."""
    if task.plan != ROUTED:
        return task.plan, task.plan_params(), None
    if route is None:
        with span("step", "route") as s:  # the router's reads are tool spans under this step
            route = registry.extras["router"].route(task.prompt, client).to_dict()
            s.set(plan=route["plan"])
    return route["plan"], {**task.params, **route["params"], "prompt": task.prompt}, route


def _conclude(run: TaskRun, store: GraphStore, env: RunEnv, registry: Registry, task: TaskDef, run_dir: Path,
              skip_llm: bool) -> None:
    """Copy the graph onto the run; answer it, or stop as "paused" when a
    node is waiting for a person."""
    record_steps(run, store)
    paused = store.paused()
    if paused:
        run.ended = "paused"
        run.warnings.append("waiting for a person: " + ", ".join(s.id for s in paused)
                            + " (python -m agentkit.harness.run_one --resume <run_dir> --approve/--reject <node>)")
        run.finish(run_dir)
        return
    answer_with_pack(run, store, env, registry, task.prompt)
    if skip_llm:
        run.warnings.append("LLM skipped (--skip-llm): template answer")
    run.finish(run_dir)


def run_task(task: TaskDef, tenant: str, run_dir: Path, run_id: str, *, skip_llm: bool = False,
             registry: Optional[Registry] = None, client: Any = None, gateway: Any = None,
             retry_backoff_s: float = 1.0, write: bool = False, require_approval: bool = False,
             trace_handlers: Sequence[Any] = ()) -> TaskRun:
    """`trace_handlers` get the start and the end of each span as it happens (Tracer handlers), e.g. to
    stream progress to a caller."""
    run_dir = Path(run_dir)
    dry_run = not (write and task.allow_writes)
    run = TaskRun(task_id=task.id, prompt=task.prompt, run_id=run_id, tenant=tenant, dry_run=dry_run,
                  harness_task=task.id, pack=task.pack)
    run.open_in(run_dir)  # on disk before any work
    if not (run_dir / "task.json").exists():  # the batch writes it; a standalone run needs it to resume / grade
        dump_record(run_dir / "task.json", task.to_dict())
    # An empty journal from the start: "made no tool call" (a catalogue-only
    # refusal) must look different from "the journal was lost".
    (run_dir / JOURNAL_FILE).touch()
    t0 = time.time()
    tracer = Tracer(run_id, handlers=list(trace_handlers))
    try:
        with use_tracer(tracer), span("task", task.id, tenant=tenant, dry_run=dry_run, pack=task.pack,
                                      plan=task.plan) as root:
            try:
                registry = registry or load_pack(task.pack)
                if client is None:
                    client = SeatRpcClient.for_tenant(tenant, extra_read_only=registry.read_only)
                    client.initialize()
                run.tool_catalog = describe_catalog(client, registry)
                journaled = JournaledSeatClient(client, run_dir / JOURNAL_FILE,
                                                extra_read_only=tuple(registry.read_only))
                if gateway is None and not skip_llm:
                    gateway = LLMGateway()
                plan_name, params, run.route = plan_and_params(task, registry, journaled)
                env = RunEnv(client=journaled, run_id=run_id, params=params, dry_run=dry_run,
                             tenant=tenant, gateway=None if skip_llm else gateway)
                plan = load_plan(Path(registry.extras["plans_dir"]) / f"{plan_name}.toml")
                store = run_plan(env, registry, plan, checkpoint_path=run_dir / GRAPH_FILE,
                                 retry_backoff_s=retry_backoff_s, approve_writes=require_approval)
                _conclude(run, store, env, registry, task, run_dir, skip_llm)
            except Exception as e:  # recorded, never swallowed silently
                run.finish(run_dir, error=f"{type(e).__name__}: {e}")
            root.set(ended=run.ended, ok=run.ended == "done", outcome=(run.finding or {}).get("outcome"),
                     answer_source=run.answer_source, tool_catalog=(run.tool_catalog or {}).get("fingerprint"))
    finally:
        save_llm_records(gateway, run_dir)
        write_telemetry(tracer, run_dir, run)
    run.seconds = round(time.time() - t0, 2)
    return run


def write_telemetry(tracer: Tracer, run_dir: Path, run: TaskRun) -> None:
    """telemetry.json, then the OTLP export when OTEL_EXPORTER_OTLP_ENDPOINT is set. Telemetry is never
    graded and must never fail a run: a failure of either is a warning."""
    try:
        export_run(tracer, run_dir)
    except Exception as e:
        run.warnings.append(f"telemetry not written: {type(e).__name__}: {e}")
        return
    try:
        export_from_env(run_dir)
    except Exception as e:
        run.warnings.append(f"OTLP export failed: {type(e).__name__}: {e}"[:300])


def resume_task(run_dir: Path, *, approve: Iterable[str] = (), reject: Iterable[str] = (), skip_llm: bool = False,
                registry: Optional[Registry] = None, client: Any = None, gateway: Any = None,
                retry_backoff_s: float = 1.0) -> TaskRun:
    """Answer a paused run's waiting nodes and finish it in the same folder."""
    run_dir = Path(run_dir)
    task = TaskDef.from_dict(load_record(run_dir / "task.json"))
    before = load_record(run_dir / "taskrun.json")
    if before.get("ended") != "paused":
        raise ValueError(f"{run_dir} is not paused (ended={before.get('ended')!r})")
    registry = registry or load_pack(task.pack)
    store = GraphStore.load(run_dir / GRAPH_FILE, registry)
    waiting = {s.id for s in store.paused()}
    answers = {**{n: {"approved": True} for n in approve}, **{n: {"approved": False, "note": "rejected"} for n in reject}}
    unknown = sorted(set(answers) - waiting)
    if unknown:
        raise ValueError(f"not waiting for an answer: {unknown}; waiting: {sorted(waiting)}")
    for node_id, answer in answers.items():
        store.resume(node_id, answer)
    run_id, tenant = before["run_id"], before.get("tenant") or default_tenant()
    run = TaskRun(task_id=task.id, prompt=task.prompt, run_id=run_id, tenant=tenant, dry_run=before.get("dry_run", True),
                  harness_task=task.id, pack=task.pack)
    run.open_in(run_dir)
    last_seq = max((e.get("seq") or 0 for e in read_jsonl(run_dir / JOURNAL_FILE)), default=0)
    t0 = time.time()
    tracer = Tracer(run_id)
    try:
        with use_tracer(tracer), span("task", task.id, tenant=tenant, dry_run=run.dry_run, pack=task.pack,
                                      plan=task.plan, resumed=sorted(answers)) as root:
            try:
                if client is None:
                    client = SeatRpcClient.for_tenant(tenant, extra_read_only=registry.read_only)
                    client.initialize()
                journaled = JournaledSeatClient(client, run_dir / JOURNAL_FILE,
                                                extra_read_only=tuple(registry.read_only), start_seq=last_seq + 1)
                if gateway is None and not skip_llm:
                    gateway = LLMGateway()
                plan_name, params, run.route = plan_and_params(task, registry, journaled, before.get("route"))
                env = RunEnv(client=journaled, run_id=run_id, params=params, dry_run=run.dry_run,
                             tenant=tenant, gateway=None if skip_llm else gateway)
                plan = load_plan(Path(registry.extras["plans_dir"]) / f"{plan_name}.toml")
                plan_executor(env, registry, plan, checkpoint_path=run_dir / GRAPH_FILE,
                              retry_backoff_s=retry_backoff_s, approve_writes=True).continue_from(store)
                run.warnings.append("resumed after a person answered: " + ", ".join(
                    f"{n}={'approved' if a['approved'] else 'rejected'}" for n, a in sorted(answers.items())))
                _conclude(run, store, env, registry, task, run_dir, skip_llm)
            except Exception as e:
                run.finish(run_dir, error=f"{type(e).__name__}: {e}")
            root.set(ended=run.ended, ok=run.ended == "done", outcome=(run.finding or {}).get("outcome"))
    finally:
        save_llm_records(gateway, run_dir, keep_earlier=True)
        write_telemetry(tracer, run_dir, run)
    run.seconds = round(time.time() - t0, 2)
    return run


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Run one harness task into one run folder.")
    ap.add_argument("--task", type=Path)
    ap.add_argument("--tenant", default=None, help="default: config/project.toml default_tenant")
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--run-id")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--write", action="store_true", help="let a task with allow_writes = true write to the platform")
    ap.add_argument("--require-approval", action="store_true",
                    help="with --write: pause before every write until a person approves it (--resume)")
    ap.add_argument("--resume", type=Path, metavar="RUN_DIR", help="continue a paused run")
    ap.add_argument("--approve", action="append", default=[], metavar="NODE", help="with --resume (repeatable)")
    ap.add_argument("--reject", action="append", default=[], metavar="NODE", help="with --resume (repeatable)")
    args = ap.parse_args(argv)
    if args.resume:
        if not (args.approve or args.reject):
            ap.error("--resume needs at least one --approve NODE or --reject NODE")
        run = resume_task(args.resume, approve=args.approve, reject=args.reject, skip_llm=args.skip_llm)
    else:
        if not (args.task and args.run_dir and args.run_id):
            ap.error("--task, --run-dir and --run-id are required (or --resume RUN_DIR)")
        run = run_task(load_task(args.task), args.tenant or default_tenant(), args.run_dir, args.run_id,
                       skip_llm=args.skip_llm, write=args.write, require_approval=args.require_approval)
    print(f"[{run.run_id}] ended={run.ended} {'dry run' if run.dry_run else 'WRITE'} source={run.answer_source} "
          f"outcome={(run.finding or {}).get('outcome')}")
    if run.error:
        print(f"ERROR: {run.error}")
    for step in run.steps:
        if step.status == "paused":
            print(f"PAUSED {step.target}: {step.detail}")
    return 0 if run.ended in ("done", "paused") else 1


if __name__ == "__main__":
    sys.exit(main())
