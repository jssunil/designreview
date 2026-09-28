"""
Team 21 Capstone Agent Implementation.

1. Evidence: agentkit's live graph runs the design-review pack's plan
   (packs/designreview/plans/rev_diff_dfm.toml) through the pack's action
   allowlist; every MCP call is journaled.
2. Finding: the pack computes the gradable answer in code
   (packs/designreview/finding.py) -- rev letters -> versions, change notes
   and their quantities, release verdict, blockers, DFM signals, refusals.
3. Answer: the LLM only words the finding; the wording is audited against
   it (packs/designreview/claims.py), rewritten once if wrong, and replaced
   by the deterministic template if it still fails or the LLM is down.
Conforms to core.harness.Harness (`run(task_id, prompt) -> TaskRun`).

Migration note: the default file id and prompt
below move into pack task files in Phase 6.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from agentkit.agents import answer_with_pack, record_steps, run_plan
from agentkit.graph import RunEnv, load_plan
from agentkit.registry import Registry, load_pack
from agentkit.transport.journal import JournaledSeatClient
from as_client import AgentSwitchClient
from core.harness import TaskRun
from core.llm_gateway import LLMGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("capstone_agent")

PROOFS_DIR = Path(__file__).parent / "proofs" / "runs"
CHECKPOINT_DIR = Path(__file__).parent / "proofs" / "checkpoints"
JOURNAL_DIR = Path(__file__).parent / "proofs" / "journals"
PACK_REF = "packs.designreview"
PLAN_NAME = "rev_diff_dfm"

# The one file on the platform with a genuine multi-revision commit history
# (Bharat EV Battery Tray Assembly, DF-2026-00001) -- see PLAN.md §8 /
# SESSION_NOTES.md §1.1.
BATTERY_TRAY_FILE_ID = "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"

DEFAULT_PROMPT = "What changed between rev B and rev C, and are there manufacturability problems in this part?"


class Team21DesignReviewAgent:
    """Specialized Capstone Agent for Team 21 (Design Review Seat)."""

    def __init__(self, tenant: str = "suryodaya", dry_run: bool = True):
        self.tenant = tenant
        self.dry_run = dry_run
        # Raw-envelope client, kept for scripts that call call_mcp() directly.
        self.mcp_client = AgentSwitchClient(tenant)
        self.mcp_client.login()
        self.pack: Registry = load_pack(PACK_REF)
        self.seat = self.mcp_client.seat()
        self.seat.extra_read_only = frozenset(self.pack.read_only)
        self.seat.initialize()
        self.gateway = LLMGateway()

    def run(self, task_id: str = "T01_BATTERY_TRAY_REVC_DFM", prompt: str = DEFAULT_PROMPT,
            file_id: str = BATTERY_TRAY_FILE_ID) -> TaskRun:
        """Execute the full domain evaluation for Team 21's Capstone
        benchmark. Conforms to core.harness.Harness."""
        logger.info("Team 21 Agent answering: '%s'", prompt)
        t0 = time.time()
        run = TaskRun(task_id=task_id, prompt=prompt, tenant=self.tenant, ended="running")
        # On disk before any work: a crash leaves ended="running", not nothing.
        run.save(PROOFS_DIR)
        try:
            self._run_steps(run, prompt, file_id)
        except Exception as e:  # record it; the run file is the evidence either way
            run.ended, run.error = "error", f"{type(e).__name__}: {e}"
        if run.ended == "running":
            run.ended = "done"
        run.seconds = round(time.time() - t0, 2)
        run.save(PROOFS_DIR)
        return run

    def _run_steps(self, run: TaskRun, prompt: str, file_id: str) -> None:
        task_id = run.task_id
        run_id = f"{task_id}_{uuid.uuid4().hex[:8]}"
        run.run_id, run.dry_run, run.pack = run_id, self.dry_run, PACK_REF

        client = JournaledSeatClient(self.seat, JOURNAL_DIR / f"{run_id}.jsonl",
                                     extra_read_only=tuple(self.pack.read_only))
        env = RunEnv(client=client, run_id=run_id, params={"file_id": file_id, "prompt": prompt},
                     dry_run=self.dry_run, tenant=self.tenant, gateway=self.gateway)
        plan = load_plan(self.pack.extras["plans_dir"] / f"{PLAN_NAME}.toml")
        store = run_plan(env, self.pack, plan, checkpoint_path=CHECKPOINT_DIR / f"{run_id}.json")
        record_steps(run, store)

        answer_with_pack(run, store, env, self.pack, prompt)
        logger.info("finding outcome=%s, answer source=%s", run.finding.get("outcome"), run.answer_source)

    # Kept for readability at call sites that prefer a domain-named entrypoint.
    def answer_capstone_benchmark(self, query: str = DEFAULT_PROMPT) -> TaskRun:
        return self.run(prompt=query)


def main(argv=None) -> int:
    """`python capstone_agent.py --task packs/designreview/tasks/t01_battery_tray_rev_diff.toml`
    runs one task file into runs/<run_id>/ (the harness layout, dry run).
    With no --task, runs the default T01 question the first-draft way."""
    import argparse
    import datetime as dt
    import sys

    from agentkit.harness.batch import RUNS_DIR
    from agentkit.harness.run_one import run_task
    from agentkit.harness.tasks import load_task

    ap = argparse.ArgumentParser(description="Team 21 design-review agent (always a dry run).")
    ap.add_argument("--task", type=Path, help="a task file (packs/designreview/tasks/*.toml)")
    ap.add_argument("--tenant", default="suryodaya")
    ap.add_argument("--skip-llm", action="store_true", help="template answer, no LLM call")
    args = ap.parse_args(argv)

    if args.task:
        task = load_task(args.task)
        run_id = f"{dt.datetime.now():%Y%m%dT%H%M%S}-{task.id}-{args.tenant}"
        run = run_task(task, args.tenant, RUNS_DIR / run_id, run_id, skip_llm=args.skip_llm)
        print(run.claimed_answer or f"(no answer: {run.error})")
        print(f"\n[run record: {RUNS_DIR / run_id}]  ended={run.ended} source={run.answer_source}")
        print("Graded runs come from the batch (it captures ground truth): python capstone_evals.py")
        return 0 if run.ended == "done" else 1

    agent = Team21DesignReviewAgent(args.tenant)
    result = agent.run()
    print("\n==================== TEAM 21 CAPSTONE ANSWER ====================\n")
    print(result.claimed_answer)
    print("\n--- Cost ledger ---")
    print(agent.gateway.ledger.summary())
    return 0 if result.ended == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
