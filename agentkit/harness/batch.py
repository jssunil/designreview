"""
The harness loop: run every task, write everything to disk, score nothing.

    python -m agentkit.harness.batch [--pack packs.<name>] [--only t01,t02]
                                     [--tenant <tenant>] [--skip-llm] [--grade]
                                     [--write [--keep-writes]]

For each task x tenant:
  1. write runs/<run_id>/task.json
  2. read the ground truth its verifiers need, with the harness's OWN login
     -> ground_truth_before.json
  3. run the agent in a subprocess (agentkit.harness.run_one), dry run
     -> taskrun.json, tool_journal.jsonl, graph.json, llm_ledger.json, stdout.txt
  4. read the ground truth again -> ground_truth_after.json
  5. write runs only: undo what the run filed, via the pack's `cleanup`
     (registry.extras) -> cleanup.json -- AFTER step 4, so the evidence that
     the write happened is already on disk. --keep-writes skips this.
and record the batch in runs/batches/<batch_id>/manifest.json.

Write mode: `--write` lets tasks with `allow_writes = true` write through the
pack's write actions (every other task stays a dry run). It is live only --
a captured fixture can't accept writes.

Grading is a separate step over those files (agentkit.harness.grade); pass
--grade to run it right after.

Offline: `--sim <fixture.json>` replays a captured platform (agentkit.sim)
for both the agent and the ground-truth reads, running each task in-process;
`--fault <spec>` (repeatable) injects faults into the agent's run, and
edit_text faults are applied after the agent finishes -- a scripted "another
team edited the record during the run".
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agentkit.config import PROJECT_ROOT, default_pack
from agentkit.harness.checks import BUILTIN_CHECKS
from agentkit.harness.ground_truth import GroundTruthReader, capture, keys_for
from agentkit.harness.tasks import TaskDef, load_tasks, validate_task
from agentkit.record import dump_record
from agentkit.registry import load_pack

RUNS_DIR = PROJECT_ROOT / "runs"
AGENT_TIMEOUT_S = 900


def new_batch_id(runs_dir: Path = RUNS_DIR) -> str:
    """A batch id no other batch has used: the batch folder is claimed with an
    exclusive mkdir, and a same-second collision gets a -2, -3 ... suffix, so
    two batches can never overwrite each other's evidence."""
    base = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    (runs_dir / "batches").mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        candidate = base if n == 1 else f"{base}-{n}"
        try:
            (runs_dir / "batches" / candidate).mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"could not allocate a unique batch id for {base}")


def run_agent_subprocess(task: TaskDef, tenant: str, run_dir: Path, run_id: str, skip_llm: bool,
                         write: bool = False) -> Dict[str, Any]:
    cmd = [sys.executable, "-m", "agentkit.harness.run_one", "--task", task.source, "--tenant", tenant,
           "--run-dir", str(run_dir), "--run-id", run_id]
    cmd += (["--skip-llm"] if skip_llm else []) + (["--write"] if write else [])
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=AGENT_TIMEOUT_S)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = "timeout", (e.stdout or ""), f"timed out after {AGENT_TIMEOUT_S}s"
    (run_dir / "stdout.txt").write_text(f"$ {' '.join(cmd[1:])}\n\n{out}\n--- stderr ---\n{err}", encoding="utf-8")
    return {"exit_code": code, "seconds": round(time.time() - t0, 2)}


class SimSession:
    """Offline batch plumbing: a fresh SimPlatform per task (so faults and
    scripted edits never leak across tasks), serving both the ground-truth
    reads and the agent. The agent runs in-process with faults armed only
    around its own calls; scripted edits land after it finishes.

    `fixture` is one fixture file (every task replays it) or a folder of
    per-tenant fixtures `<tenant>.json` (each task replays its own tenant's)."""

    def __init__(self, fixture: Path, faults: List[str], read_only: Any = ()):
        self.fixture, self.faults, self.read_only = Path(fixture), list(faults), read_only
        self.current: Any = None

    def fixture_for(self, tenant: str) -> Path:
        if not self.fixture.is_dir():
            return self.fixture
        path = self.fixture / f"{tenant}.json"
        if not path.exists():
            raise FileNotFoundError(f"no fixture for tenant {tenant!r} in {self.fixture} "
                                    f"(capture it: python -m agentkit.sim.capture --tenant {tenant})")
        return path

    def reader_for(self, tenant: str) -> Any:
        from agentkit.sim import SimPlatform

        self.current = SimPlatform(self.fixture_for(tenant), self.faults, self.read_only)
        self.current.armed = False  # the "before" ground truth is read undisturbed
        return self.current

    def run_agent(self, task: TaskDef, tenant: str, run_dir: Path, run_id: str, skip_llm: bool) -> Dict[str, Any]:
        from agentkit.harness.run_one import run_task

        platform = self.current
        platform.armed = True
        try:
            run = run_task(task, tenant, run_dir, run_id, skip_llm=skip_llm, client=platform, retry_backoff_s=0)
        finally:
            platform.armed = False
            platform.apply_edits()
        (run_dir / "stdout.txt").write_text(f"[sim] ended={run.ended} source={run.answer_source}\n",
                                            encoding="utf-8")
        return {"exit_code": 0 if run.ended == "done" else 1, "seconds": run.seconds}


def run_batch(tasks: List[TaskDef], *, tenant_override: Optional[str] = None, skip_llm: bool = False,
              runs_dir: Path = RUNS_DIR, reader_factory: Callable[[str], Any] = GroundTruthReader.for_tenant,
              agent_runner: Callable[..., Dict[str, Any]] = run_agent_subprocess,
              batch_id: Optional[str] = None, fresh_reader_per_task: bool = False, write: bool = False,
              keep_writes: bool = False) -> Path:
    batch_id = batch_id or new_batch_id(runs_dir)
    batch_dir = runs_dir / "batches" / batch_id
    manifest: Dict[str, Any] = {"batch_id": batch_id, "started_at": dt.datetime.now().isoformat(timespec="seconds"),
                                "write": write, "skip_llm": skip_llm, "runs": []}
    dump_record(batch_dir / "manifest.json", manifest)
    readers: Dict[str, Any] = {}
    for task in tasks:
        registry = load_pack(task.pack)
        validate_task(task, registry, BUILTIN_CHECKS)
        keys = keys_for(task.verifiers, registry)
        for tenant in ([tenant_override] if tenant_override else task.tenants):
            run_id = f"{batch_id}-{task.id}-{tenant}"
            run_dir = runs_dir / run_id
            entry: Dict[str, Any] = {"task_id": task.id, "tenant": tenant, "run_id": run_id}
            try:
                if fresh_reader_per_task:
                    readers.pop(tenant, None)
                reader = readers.get(tenant) or readers.setdefault(tenant, reader_factory(tenant))
                dump_record(run_dir / "task.json", task.to_dict())
                dump_record(run_dir / "ground_truth_before.json", capture(registry, reader, keys))
                writes = write and task.allow_writes
                result = agent_runner(task, tenant, run_dir, run_id, skip_llm, **({"write": True} if writes else {}))
                dump_record(run_dir / "ground_truth_after.json", capture(registry, reader, keys))
                entry.update(status="ran", write=writes, **result)
                if writes and not keep_writes:
                    entry["cleanup"] = undo_writes(registry, reader, run_dir, tenant)
            except Exception as e:  # harness-side failure: recorded, batch continues
                entry.update(status="harness_error", reason=f"{type(e).__name__}: {e}"[:300])
            manifest["runs"].append(entry)
            dump_record(batch_dir / "manifest.json", manifest)
            print(f"  - {task.id} [{tenant}]: {entry['status']} "
                  f"{('exit ' + str(entry.get('exit_code')) + ' in ' + str(entry.get('seconds')) + 's') if entry['status'] == 'ran' else entry.get('reason', '')}")
    manifest["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    dump_record(batch_dir / "manifest.json", manifest)
    return batch_dir


def undo_writes(registry: Any, reader: Any, run_dir: Path, tenant: str) -> str:
    """Run the pack's clean-up for one write run; record what it did in cleanup.json."""
    cleanup = registry.extras.get("cleanup")
    if cleanup is None:
        return "no cleanup registered by the pack"
    try:
        report = cleanup(reader, run_dir=run_dir, tenant=tenant)
    except Exception as e:  # recorded; the evidence is already on disk
        report = {"error": f"{type(e).__name__}: {e}"[:300]}
    dump_record(run_dir / "cleanup.json", report)
    return "error" if report.get("error") or report.get("errors") else "done"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the harness task set (grading is separate).")
    ap.add_argument("--pack", default=None, help="default: config/project.toml default_pack")
    ap.add_argument("--tasks", nargs="*", type=Path, help="task files or dirs (default: the pack's tasks/)")
    ap.add_argument("--only", default=None, help="comma-separated task ids")
    ap.add_argument("--tenant", default=None, help="run every task on this tenant only")
    ap.add_argument("--skip-llm", action="store_true", help="template answers, no LLM calls")
    ap.add_argument("--grade", action="store_true", help="grade the batch when it finishes")
    ap.add_argument("--sim", type=Path, default=None, help="replay a fixture file, or a folder of <tenant>.json fixtures, instead of the live platform")
    ap.add_argument("--fault", action="append", default=[], help="inject a fault (sim only), e.g. drop_tool:X.get")
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--write", action="store_true",
                    help="LIVE WRITES: tasks with allow_writes = true may write to the platform")
    ap.add_argument("--keep-writes", action="store_true",
                    help="with --write: don't undo what the runs filed (clean up later with agentkit.harness.cleanup)")
    args = ap.parse_args(argv)
    if args.fault and not args.sim:
        ap.error("--fault needs --sim")
    if args.write and args.sim:
        ap.error("--write is live only: a captured fixture can't accept writes")
    if args.keep_writes and not args.write:
        ap.error("--keep-writes needs --write")

    registry = load_pack(args.pack or default_pack())
    tasks = load_tasks(args.tasks or [Path(registry.extras["tasks_dir"])])
    if args.only:
        wanted = {t.strip() for t in args.only.split(",") if t.strip()}
        unknown = wanted - {t.id for t in tasks}
        if unknown:
            ap.error(f"unknown task ids {sorted(unknown)}")
        tasks = [t for t in tasks if t.id in wanted]
    mode = f"SIM {args.sim.name}" + (f" faults={args.fault}" if args.fault else "") if args.sim else "live"
    writers = [t.id for t in tasks if t.allow_writes] if args.write else []
    kind = f"WRITE for {writers}" if writers else "dry run"
    print(f"[batch] {len(tasks)} task(s), {kind}, {mode}, LLM {'off' if args.skip_llm else 'on'}")
    if args.sim:
        sim = SimSession(args.sim, args.fault, registry.read_only)
        batch_dir = run_batch(tasks, tenant_override=args.tenant, skip_llm=args.skip_llm, runs_dir=args.runs_dir,
                              reader_factory=sim.reader_for, agent_runner=sim.run_agent, fresh_reader_per_task=True)
    else:
        batch_dir = run_batch(tasks, tenant_override=args.tenant, skip_llm=args.skip_llm, runs_dir=args.runs_dir,
                              write=args.write, keep_writes=args.keep_writes)
    print(f"[manifest: {batch_dir / 'manifest.json'}]")
    if args.grade:
        from agentkit.harness.grade import main as grade_main

        return grade_main([str(batch_dir)])
    print(f"Nothing graded yet. Grade with:\n  python -m agentkit.harness.grade {batch_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
