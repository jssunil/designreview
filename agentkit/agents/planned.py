"""
Run a pack's plan through the live graph and copy the outcome into a TaskRun.

run_plan() gathers evidence through the live graph; answer_with_pack() turns
the results into the pack's finding (computed in code) and a checked,
readable answer (agentkit.answer.compose_answer).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from agentkit.answer import compose_answer
from agentkit.config import llm_budget_usd
from agentkit.graph.checkpoint import CheckpointSaver
from agentkit.graph.engine import GraphStore, LiveGraphExecutor, RunEnv
from agentkit.graph.node_result import HANDED_OFF, RESOLVED
from agentkit.graph.plan_loader import Plan
from agentkit.graph.rule_planner import RulePlanner
from agentkit.memory import SECTION, FindingMemory, MemorySpec
from agentkit.record.run_record import TaskRun
from agentkit.registry import Registry
from agentkit.telemetry import span


def plan_executor(env: RunEnv, registry: Registry, plan: Plan, *, checkpoint_path: Optional[Path] = None,
                  max_workers: int = 4, retry_backoff_s: float = 1.0, mode: Optional[str] = None,
                  saver: Optional[CheckpointSaver] = None, approve_writes: bool = False) -> LiveGraphExecutor:
    """The executor a plan runs on: its rules as the planner, its mode (unless
    `mode` overrides it) and its channels."""
    return LiveGraphExecutor(env, registry, planner=RulePlanner(plan.rules, env.params),
                             max_workers=max_workers, checkpoint_path=checkpoint_path,
                             retry_backoff_s=retry_backoff_s, mode=mode or plan.mode,
                             channels=plan.channels or None, saver=saver, approve_writes=approve_writes)


def run_plan(env: RunEnv, registry: Registry, plan: Plan, *, checkpoint_path: Optional[Path] = None,
             max_workers: int = 4, retry_backoff_s: float = 1.0, mode: Optional[str] = None,
             saver: Optional[CheckpointSaver] = None, approve_writes: bool = False) -> GraphStore:
    executor = plan_executor(env, registry, plan, checkpoint_path=checkpoint_path, max_workers=max_workers,
                             retry_backoff_s=retry_backoff_s, mode=mode, saver=saver,
                             approve_writes=approve_writes)
    return executor.run(env.run_id or plan.name, plan.initial_tasks(env.params))


def record_steps(run: TaskRun, store: GraphStore) -> None:
    """One Step per node, in the order nodes started (never-started last)."""
    specs = sorted(store.specs.values(), key=lambda s: (s.started_at is None, s.started_at or 0.0))
    for spec in specs:
        r = spec.result
        if r is None and spec.state == "paused":  # waiting for a person (interrupt())
            run.add_step(kind=spec.action, target=spec.id, ok=False, detail=spec.interrupt, status="paused",
                         reason="waiting for a person's answer", attempts=spec.attempts, added_by=spec.added_by)
            continue
        # A hand-off is a normal result, and its data (what a person would get) is the evidence.
        carries_data = bool(r and r.verdict in (RESOLVED, HANDED_OFF))
        run.add_step(kind=spec.action, target=spec.id, ok=carries_data,
                     detail=(r.data if carries_data else (r.reason if r else "not run")),
                     status=r.verdict if r else "not_run", reason=r.reason if r else None,
                     error_kind=r.error_kind if r else None, attempts=spec.attempts,
                     added_by=spec.added_by, seconds=spec.seconds)


def answer_with_pack(run: TaskRun, store: GraphStore, env: RunEnv, registry: Registry, query: str,
                     memory: Optional[FindingMemory] = None) -> None:
    """Build the pack's finding from the graph, word it (checked), and put
    both on the run. The pack supplies, via registry.extras:
    finding_builder, claim_rules, template, system_prompt, narration_instructions,
    and (optional) memory: with a FindingMemory, the finding also gets a comparison with
    the last kept facts of the same record, and an audited answer keeps its facts."""
    x = registry.extras
    t0 = time.time()
    with span("step", "build_finding") as s:
        finding = x["finding_builder"](store.results(), env.params, run_id=env.run_id, dry_run=env.dry_run)
        s.set(outcome=finding.get("outcome"))
    kept = _recall(finding, env, x.get("memory"), memory)
    run.finding = finding
    run.add_step(kind="build_finding", target="finding", ok=True, status=RESOLVED,
                 reason=f"outcome={finding.get('outcome')}", seconds=round(time.time() - t0, 3))

    t0 = time.time()
    with span("step", "narrate") as s:
        answer = compose_answer(query, finding, gateway=env.gateway, rules=x["claim_rules"], template=x["template"],
                                system_prompt=x.get("system_prompt", ""),
                                instructions=x.get("narration_instructions", ""),
                                tiers=tuple(x.get("narration_tiers") or ("quality",)),
                                max_cost_usd=llm_budget_usd())
        s.set(source=answer["source"], tier=answer.get("tier"), provider=answer.get("provider"),
              model=answer.get("model"), audit_ok=answer["audit"]["ok"],
              attempts=len(answer["attempts"]))
    run.claimed_answer = answer["text"]
    run.answer_source = answer["source"]
    if kept is not None and answer["audit"]["ok"] and finding.get("outcome") == "answered":
        key, kind, facts = kept
        memory.remember(env.tenant or "", key, kind, env.run_id or "", facts)  # type: ignore[union-attr]
    if answer.get("fallback_reason"):
        run.warnings.append(f"answer fell back to template: {answer['fallback_reason']}")
    run.add_step(kind="narrate", target="answer", ok=answer["audit"]["ok"], status=RESOLVED,
                 detail={"source": answer["source"], "tier": answer.get("tier"),
                         "provider": answer.get("provider"), "model": answer.get("model"), "audit": answer["audit"],
                         "attempts": [{"audit": a["audit"], "chars": len(a["text"]), "tier": a.get("tier"),
                                       "provider": a.get("provider"), "model": a.get("model")}
                                      for a in answer["attempts"]],
                         "fallback_reason": answer.get("fallback_reason")},
                 reason=answer.get("fallback_reason"), attempts=max(1, len(answer["attempts"])),
                 seconds=round(time.time() - t0, 3))


def _recall(finding: Dict[str, Any], env: RunEnv, spec: Optional[MemorySpec],
            memory: Optional[FindingMemory]) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """(key, kind, facts) to keep after an audited answer, or None. Adds the comparison with the
    last kept facts of the same record to the finding (under SECTION). Memory never replaces a
    platform read: the plan has already run when this is called."""
    if memory is None or spec is None:
        return None
    with span("step", "memory") as s:
        kept = spec.facts(finding)
        if kept is None:
            return None
        key, kind, facts = kept
        previous = memory.last(env.tenant or "", key, kind, exclude_run=env.run_id)
        if previous is not None:
            finding[SECTION] = spec.compare(previous, facts)
        s.set(previous=previous["run_id"] if previous else None)
    return key, kind, facts
