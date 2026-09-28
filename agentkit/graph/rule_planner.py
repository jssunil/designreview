"""
RulePlanner: interleaved planning from data instead of Python branches.

A rule fires at most once per run, when the node it watches finishes:

    - name: follow_up_when_blocked
      when:
        node: release_gate           # node id to watch
        verdict: resolved            # optional, default: resolved
        nonempty: [result.blockers, result.reason_codes]   # any path non-empty
        # or: equals: {path: result.ready, value: false}
        # or: truthy: result.ready  /  falsy: result.ready
      add:
        - id: failed_checklists
          action: list_failed_checklists
          args: {file_id: "{file_id}"}
          after: [release_gate]
      link: [[failed_checklists, answer]]   # optional: make an unstarted node wait

Paths are dotted keys into the watched node's result data. Placeholders are
filled from the run parameters, as in plan files.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agentkit.graph.engine import GraphPatch, GraphStore, TaskSpec
from agentkit.graph.node_result import RESOLVED
from agentkit.graph.plan_loader import fill, node_from_dict

_MISSING = object()


def dig(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return _MISSING
    return cur


def _nonempty(v: Any) -> bool:
    return v is not _MISSING and v not in (None, "", [], {})


def condition_holds(when: Dict[str, Any], data: Any) -> bool:
    if "nonempty" in when:
        paths = when["nonempty"] if isinstance(when["nonempty"], list) else [when["nonempty"]]
        if not any(_nonempty(dig(data, p)) for p in paths):
            return False
    if "equals" in when:
        if dig(data, when["equals"]["path"]) != when["equals"]["value"]:
            return False
    if "truthy" in when:
        v = dig(data, when["truthy"])
        if v is _MISSING or not v:
            return False
    if "falsy" in when:
        v = dig(data, when["falsy"])
        if v is _MISSING or v:
            return False
    return True


class RulePlanner:
    def __init__(self, rules: List[Dict[str, Any]], params: Dict[str, Any]):
        self.rules = rules
        self.params = params
        self.fired: List[str] = []

    def on_outcome(self, store: GraphStore, spec: TaskSpec) -> Optional[GraphPatch]:
        patch = GraphPatch()
        for i, rule in enumerate(self.rules):
            name = rule.get("name") or f"rule_{i}"
            when = rule.get("when") or {}
            if name in self.fired or when.get("node") != spec.id or spec.result is None:
                continue
            if spec.result.verdict != when.get("verdict", RESOLVED):
                continue
            if not condition_holds(when, spec.result.data):
                continue
            self.fired.append(name)
            patch.add += [node_from_dict(n, self.params, added_by="planner") for n in rule.get("add") or []]
            patch.link += [tuple(fill(pair, self.params)) for pair in rule.get("link") or []]
        return patch if (patch.add or patch.link) else None
