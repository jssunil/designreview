"""
Design-review pack (Team 21 seat on AgentSwitch).

Everything domain-specific lives here: actions (the allowlist), read-only
endpoint declarations, plans, the finding builder, claim rules, the
template answer, prompts, hand-off topics (handoffs.toml), ground-truth probes, verifiers, verifier
self-test mutants, tasks and platform fixtures.

    from agentkit.registry import load_pack
    reg = load_pack("packs.designreview")
"""

from __future__ import annotations

from pathlib import Path

from agentkit.registry import Registry
from packs.designreview.actions import READ_ONLY_ENDPOINTS, register_actions, register_retrieval
from packs.designreview.checks import register_checks
from packs.designreview.claims import CLAIM_RULES
from packs.designreview.finding import build_finding
from packs.designreview.handoffs import register_handoffs
from packs.designreview.mutants import register_mutants
from packs.designreview.probes import register_probes
from packs.designreview.templates import plain_rendering

PACK_DIR = Path(__file__).resolve().parent
PLANS_DIR = PACK_DIR / "plans"
PROMPTS_DIR = PACK_DIR / "prompts"
TASKS_DIR = PACK_DIR / "tasks"
FIXTURES_DIR = PACK_DIR / "fixtures"


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def register(reg: Registry) -> None:
    reg.name = "designreview"
    register_actions(reg)
    register_retrieval(reg)
    register_handoffs(reg)
    register_probes(reg)
    register_checks(reg)
    register_mutants(reg)
    reg.extras.update(
        plans_dir=PLANS_DIR,
        tasks_dir=TASKS_DIR,
        fixtures_dir=FIXTURES_DIR,
        finding_builder=build_finding,
        claim_rules=CLAIM_RULES,
        template=plain_rendering,
        system_prompt=_prompt("system.md"),
        narration_instructions=_prompt("narrate.md"),
    )


__all__ = ["PACK_DIR", "PLANS_DIR", "PROMPTS_DIR", "TASKS_DIR", "READ_ONLY_ENDPOINTS", "register"]
