"""
Run ONE task into ONE run folder -- the agent side of the harness.

    python -m agentkit.harness.run_one --task <task.toml> --tenant <tenant> \
        --run-dir runs/<run_id> --run-id <run_id> [--skip-llm]

Writes, in order: taskrun.json (ended="running", before any work), then
tool_journal.jsonl and graph.json as the plan runs, then taskrun.json again
with the finding, answer and ended=done|error, and llm_ledger.json.
The batch runner starts this in a subprocess, so an agent crash can never
take the harness down with it; the running-record is the evidence either way.
Always a dry run: the pack exposes no write actions.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Optional

from agentkit.agents import answer_with_pack, record_steps, run_plan
from agentkit.config import default_tenant
from agentkit.graph import RunEnv, load_plan
from agentkit.harness.tasks import TaskDef, load_task
from agentkit.llm import LLMGateway
from agentkit.record import TaskRun, dump_record
from agentkit.registry import Registry, load_pack
from agentkit.transport import SeatRpcClient
from agentkit.transport.journal import JOURNAL_FILE, JournaledSeatClient

GRAPH_FILE = "graph.json"
LEDGER_FILE = "llm_ledger.json"


def run_task(task: TaskDef, tenant: str, run_dir: Path, run_id: str, *, skip_llm: bool = False,
             registry: Optional[Registry] = None, client: Any = None, gateway: Any = None,
             retry_backoff_s: float = 1.0) -> TaskRun:
    run_dir = Path(run_dir)
    run = TaskRun(task_id=task.id, prompt=task.prompt, run_id=run_id, tenant=tenant, dry_run=True,
                  harness_task=task.id, pack=task.pack)
    run.open_in(run_dir)  # on disk before any work
    t0 = time.time()
    try:
        registry = registry or load_pack(task.pack)
        if client is None:
            client = SeatRpcClient.for_tenant(tenant, extra_read_only=registry.read_only)
            client.initialize()
        journaled = JournaledSeatClient(client, run_dir / JOURNAL_FILE, extra_read_only=tuple(registry.read_only))
        if gateway is None and not skip_llm:
            gateway = LLMGateway()
        env = RunEnv(client=journaled, run_id=run_id, params=task.plan_params(), dry_run=True, tenant=tenant,
                     gateway=None if skip_llm else gateway)
        plan = load_plan(Path(registry.extras["plans_dir"]) / f"{task.plan}.toml")
        store = run_plan(env, registry, plan, checkpoint_path=run_dir / GRAPH_FILE, retry_backoff_s=retry_backoff_s)
        record_steps(run, store)
        answer_with_pack(run, store, env, registry, task.prompt)
        if skip_llm:
            run.warnings.append("LLM skipped (--skip-llm): template answer")
        run.finish(run_dir)
    except Exception as e:  # recorded, never swallowed silently
        run.finish(run_dir, error=f"{type(e).__name__}: {e}")
    finally:
        ledger = getattr(gateway, "ledger", None)
        if ledger is not None:
            dump_record(run_dir / LEDGER_FILE, {"summary": ledger.summary(),
                                               "calls": [e.__dict__ for e in ledger.entries]})
    run.seconds = round(time.time() - t0, 2)
    return run


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="Run one harness task into one run folder.")
    ap.add_argument("--task", required=True, type=Path)
    ap.add_argument("--tenant", default=None, help="default: config/project.toml default_tenant")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--skip-llm", action="store_true")
    args = ap.parse_args(argv)
    run = run_task(load_task(args.task), args.tenant or default_tenant(), args.run_dir, args.run_id,
                   skip_llm=args.skip_llm)
    print(f"[{run.run_id}] ended={run.ended} source={run.answer_source} "
          f"outcome={(run.finding or {}).get('outcome')}")
    if run.error:
        print(f"ERROR: {run.error}")
    return 0 if run.ended == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
