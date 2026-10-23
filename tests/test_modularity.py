"""
Architecture guards: the framework stays domain-free and no agent framework
sneaks in.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [x] No file under agentkit/ mentions a design-review name: "designreview", "DesignFile",
      "DesignVersion", "release_readiness", a Battery Tray id ... (agentkit must stay domain-free)
  [x] No file under agentkit/ imports from packs/
  [x] requirements.txt names no agent or workflow framework (keep your banned-package list in the test;
      the course brief names the frameworks that are not allowed)
  [x] No .py file under agentkit/ or packs/ imports any of those packages
  [ ] TODO: A second, tiny pack (actions + one plan + one task, written in tmp_path) runs through
      agentkit.harness.run_one.run_task with ZERO changes to agentkit/ -- proof the framework is reusable
  [x] No config/plan/task file is YAML any more (config/, packs/)
  [ ] TODO: The generic modules added since 2026-10-14 name no design-review word either: agentkit/agents/router.py,
      agentkit/answer/quarantine.py, agentkit/harness/goldens.py, agentkit/harness/pareto.py,
      agentkit/retrieval/quality.py
More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: no file under agentkit/ reads a pack file by path ("packs/" in a string literal, parsed with ast)
  [ ] TODO: every pack folder under packs/ has the files the loader needs (actions, plans/, tasks/)
  [ ] TODO: tests/ has no file that imports from tests_local/ (AI-written tests stay out of the hand-written suite)
Hint: walk Path("agentkit").rglob("*.py") and read the text; parse imports with the `ast` module
rather than grepping, so comments and docstrings don't cause false alarms.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTKIT = ROOT / "agentkit"
BANNED = {
    "langchain", "langchain_core", "langchain_community", "langgraph", "langsmith", "llama_index",
    "crewai", "autogen", "autogen_agentchat", "semantic_kernel", "haystack", "dspy", "pydantic_ai",
    "smolagents", "agno",
}
DOMAIN_WORDS = [
    "designreview", "DesignFile", "DesignVersion", "release_readiness", "Battery Tray",
    "39b69109-b56a-4bf4-af48-1ecf2b18f8a6",
]


def py_files(*dirs):
    for d in dirs:
        yield from (p for p in d.rglob("*.py") if "__pycache__" not in p.parts)


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


@pytest.mark.parametrize("word", DOMAIN_WORDS)
def test_agentkit_names_no_design_review_word(word):
    hits = [
        p.relative_to(ROOT).as_posix()
        for p in py_files(AGENTKIT)
        if re.search(re.escape(word), p.read_text(encoding="utf-8"), re.I)
    ]
    assert hits == []


def test_agentkit_never_imports_from_packs():
    hits = [
        (p.relative_to(ROOT).as_posix(), m)
        for p in py_files(AGENTKIT)
        for m in imported_modules(p)
        if m.split(".")[0] == "packs"
    ]
    assert hits == []


def test_no_agent_framework_in_requirements_or_imports():
    names = {
        re.split(r"[<>=!~\[; ]", line.strip(), maxsplit=1)[0].lower().replace("-", "_")
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert names & BANNED == set()
    hits = [
        (p.relative_to(ROOT).as_posix(), m)
        for p in py_files(AGENTKIT, ROOT / "packs")
        for m in imported_modules(p)
        if m.split(".")[0] in BANNED
    ]
    assert hits == []


def test_no_yaml_config_plan_or_task_files():
    yaml = [
        p.relative_to(ROOT).as_posix()
        for d in (ROOT / "config", ROOT / "packs")
        for p in d.rglob("*")
        if p.suffix in (".yaml", ".yml")
    ]
    assert yaml == []

