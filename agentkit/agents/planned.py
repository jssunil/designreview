"""
Run a pack's plan through the live graph and copy the outcome into a TaskRun.

run_plan() gathers evidence through the live graph; answer_with_pack() turns
the results into the pack's finding (computed in code) and a checked,
readable answer (agentkit.answer.compose_answer).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from agentkit.answer import compose_answer
from agentkit.graph.engine import GraphStore, LiveGraphExecutor, RunEnv
from agentkit.graph.node_result import RESOLVED
from agentkit.graph.plan_loader import Plan
from agentkit.graph.rule_planner import RulePlanner
from agentkit.record.run_record import TaskRun
from agentkit.registry import Registry


def run_plan(env: RunEnv, registry: Registry, plan: Plan, *, checkpoint_path: Optional[Path] = None,
             max_workers: int = 4, retry_backoff_s: float = 1.0) -> GraphStore:
    executor = LiveGraphExecutor(env, registry, planner=RulePlanner(plan.rules, env.params),
                                 max_workers=max_workers, checkpoint_path=checkpoint_path,
                                 retry_backoff_s=retry_backoff_s)
    return executor.run(env.run_id or plan.name, plan.initial_tasks(env.params))


def record_steps(run: TaskRun, store: GraphStore) -> None:
    """One Step per node, in the order nodes started (never-started last)."""
    specs = sorted(store.specs.values(), key=lambda s: (s.started_at is None, s.started_at or 0.0))
    for spec in specs:
        r = spec.result
        run.add_step(kind=spec.action, target=spec.id, ok=bool(r and r.verdict == RESOLVED),
                     detail=(r.data if r and r.verdict == RESOLVED else (r.reason if r else "not run")),
                     status=r.verdict if r else "not_run", reason=r.reason if r else None,
                     error_kind=r.error_kind if r else None, attempts=spec.attempts,
                     added_by=spec.added_by, seconds=spec.seconds)


def answer_with_pack(run: TaskRun, store: GraphStore, env: RunEnv, registry: Registry, query: str) -> None:
    """Build the pack's finding from the graph, word it (checked), and put
    both on the run. The pack supplies, via registry.extras:
    finding_builder, claim_rules, template, system_prompt, narration_instructions."""
    x = registry.extras
    t0 = time.time()
    finding = x["finding_builder"](store.results(), env.params, run_id=env.run_id, dry_run=env.dry_run)
    run.finding = finding
    run.add_step(kind="build_finding", target="finding", ok=True, status=RESOLVED,
                 reason=f"outcome={finding.get('outcome')}", seconds=round(time.time() - t0, 3))

    t0 = time.time()
    answer = compose_answer(query, finding, gateway=env.gateway, rules=x["claim_rules"], template=x["template"],
                            system_prompt=x.get("system_prompt", ""),
                            instructions=x.get("narration_instructions", ""))
    run.claimed_answer = answer["text"]
    run.answer_source = answer["source"]
    if answer.get("fallback_reason"):
        run.warnings.append(f"answer fell back to template: {answer['fallback_reason']}")
    run.add_step(kind="narrate", target="answer", ok=answer["audit"]["ok"], status=RESOLVED,
                 detail={"source": answer["source"], "audit": answer["audit"],
                         "attempts": [{"audit": a["audit"], "chars": len(a["text"])} for a in answer["attempts"]],
                         "fallback_reason": answer.get("fallback_reason")},
                 reason=answer.get("fallback_reason"), attempts=max(1, len(answer["attempts"])),
                 seconds=round(time.time() - t0, 3))
