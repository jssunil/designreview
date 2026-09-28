"""
The plug-in point between agentkit (domain-free) and a pack (domain).

A pack is a Python module exposing `register(registry)`. It fills a
Registry with:
- actions      -- the ONLY operations a graph node may perform (allowlist)
- read_only    -- tool names that are reads even though they don't end in
                  .list/.get (e.g. computed endpoints)
- probes         -- ground-truth readers: probe(reader, arg) -> observation
- checks         -- verifiers (CheckDef: what to observe + a pure check)
- mutants        -- verifier self-test faults
- anything else under `extras` (templates, claim rules, ...)

`load_pack("packs.<name>")` or `load_pack("packs.<name>:register")`
imports the module and returns a fresh Registry, so two packs (or two
tests) never share state.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Set


@dataclass(frozen=True)
class ActionSpec:
    name: str
    fn: Callable[..., Any]  # fn(env, args, results) -> NodeResult | value
    description: str = ""
    writes: bool = False
    retries: int = 0  # extra attempts on a FLAKY transport error (never for writes)


@dataclass(frozen=True)
class CheckDef:
    """A verifier: `observes(params) -> [probe keys]` it needs captured before
    and after the run, and `check(bundle, params) -> [CheckOutcome]`, a pure
    function over the saved run folder."""
    name: str
    check: Callable[..., Any]
    observes: Callable[[Dict[str, Any]], Any] = lambda params: []


@dataclass(frozen=True)
class MutantDef:
    """A verifier self-test fault: `apply(bundle) -> bundle | None` breaks one
    thing in a copy of a passing run; the check named `target` must then fail.
    Returning None means "not applicable to this run"."""
    name: str
    target: str
    apply: Callable[..., Any]


@dataclass
class Registry:
    name: str = ""
    actions: Dict[str, ActionSpec] = field(default_factory=dict)
    read_only: Set[str] = field(default_factory=set)
    probes: Dict[str, Callable[..., Any]] = field(default_factory=dict)
    checks: Dict[str, Callable[..., Any]] = field(default_factory=dict)
    mutants: Dict[str, Callable[..., Any]] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)

    # ---- decorators -----------------------------------------------------

    def action(self, name: str, *, description: str = "", writes: bool = False,
               retries: int = 0) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            if name in self.actions:
                raise ValueError(f"action {name!r} registered twice")
            self.actions[name] = ActionSpec(name, fn, description or (fn.__doc__ or "").strip().split("\n")[0],
                                            writes=writes, retries=0 if writes else retries)
            return fn
        return deco

    def probe(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._into(self.probes, name)

    def check(self, name: str, observes: Optional[Callable[[Dict[str, Any]], Any]] = None
              ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            if name in self.checks:
                raise ValueError(f"{name!r} registered twice")
            self.checks[name] = CheckDef(name, fn, observes or (lambda params: []))
            return fn
        return deco

    def mutant(self, name: str, target: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            if name in self.mutants:
                raise ValueError(f"{name!r} registered twice")
            self.mutants[name] = MutantDef(name, target, fn)
            return fn
        return deco

    @staticmethod
    def _into(table: Dict[str, Any], name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            if name in table:
                raise ValueError(f"{name!r} registered twice")
            table[name] = fn
            return fn
        return deco

    def declare_read_only(self, tools: Iterable[str]) -> None:
        self.read_only.update(tools)

    def get_action(self, name: str) -> ActionSpec:
        try:
            return self.actions[name]
        except KeyError:
            raise KeyError(f"{name!r} is not a registered action") from None


def load_pack(ref: str, registry: Optional[Registry] = None) -> Registry:
    module_name, _, func_name = ref.partition(":")
    module = importlib.import_module(module_name)
    register = getattr(module, func_name or "register")
    reg = registry or Registry(name=module_name)
    register(reg)
    return reg
