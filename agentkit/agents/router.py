"""
From a question to a plan and its parameters, when a task names only the question.

A task file may give `plan` and `params` itself (the harness's original style: the grader
then tests the plan's execution, not the understanding of the question). A task with
`plan = "route"` (agentkit.harness.tasks.ROUTED) is routed: the pack's router reads the question and decides which plan runs
and with which parameters -- so the run also tests whether the agent understood what was
asked and which record it is about.

Generic part (this module):
  Route            one rule: a plan, the regular expressions any of which select it
                   (first route that matches wins, in file order), fixed params, and
                   which parameters must be resolved from the question
  RouteDecision    plan + params + why; or a decline (plan = DECLINE_PLAN, reason)
  RuleRouter       routes + a resolver; route(prompt, client) -> RouteDecision
  Declined         raised by a resolver that can't name the record ("ambiguous: ...")

The pack supplies the routes (data) and a resolver
`resolve(client, prompt, needs) -> {param: value}` that may raise Declined. A decline runs
the pack's DECLINE_PLAN with {"reason": ...}, so it ends as an ordinary refusal: a
finding, a templated answer, a graded run. The decision is kept on the run record
(`taskrun.route`) and is gradable.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern, Sequence

DECLINE_PLAN = "route_declined"
NO_ROUTE = "no route matched"  # the why of a decline that no route matched (the LLM planner may try it)


class Declined(Exception):
    """The question can't be routed (no matching record, or more than one)."""


@dataclass
class Route:
    plan: str
    when_any: Sequence[str]
    params: Dict[str, Any] = field(default_factory=dict)
    needs: Sequence[str] = ()  # parameters the resolver must find in the question

    def __post_init__(self) -> None:
        self._patterns: List[Pattern[str]] = [re.compile(p, re.I) for p in self.when_any]

    def matches(self, prompt: str) -> Optional[str]:
        return next((p.pattern for p in self._patterns if p.search(prompt or "")), None)


@dataclass
class RouteDecision:
    plan: str
    params: Dict[str, Any]
    why: str

    @property
    def declined(self) -> bool:
        return self.plan == DECLINE_PLAN

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


Resolver = Callable[[Any, str, Sequence[str]], Dict[str, Any]]


class RuleRouter:
    def __init__(self, routes: Sequence[Route], resolve: Optional[Resolver] = None):
        if not routes:
            raise ValueError("a router needs at least one route")
        self.routes, self.resolve = list(routes), resolve

    @classmethod
    def from_config(cls, cfg: Dict[str, Any], resolve: Optional[Resolver] = None) -> "RuleRouter":
        return cls([Route(**r) for r in cfg.get("routes") or []], resolve)

    def route(self, prompt: str, client: Any = None) -> RouteDecision:
        for r in self.routes:
            hit = r.matches(prompt)
            if hit is None:
                continue
            params = dict(r.params)
            if r.needs:
                if self.resolve is None:
                    return RouteDecision(DECLINE_PLAN, {"reason": f"no resolver for {list(r.needs)}"}, hit)
                try:
                    params.update(self.resolve(client, prompt, r.needs))
                except Declined as e:
                    return RouteDecision(DECLINE_PLAN, {"reason": str(e)}, f"{r.plan}: {e}")
            return RouteDecision(r.plan, params, f"matched {hit!r}")
        return RouteDecision(DECLINE_PLAN, {"reason": "the question doesn't match anything this agent can do"},
                             NO_ROUTE)
