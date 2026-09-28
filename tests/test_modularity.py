"""
Architecture guards: the framework stays domain-free and no agent framework
sneaks in.    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] No file under agentkit/ mentions a design-review name: "designreview", "DesignFile",
      "DesignVersion", "release_readiness", a Battery Tray id ... (agentkit must stay domain-free)
  [ ] No file under agentkit/ imports from packs/
  [ ] requirements.txt names none of: langchain, langgraph, llama-index / llama_index, crewai, autogen,
      semantic-kernel, haystack, dspy, pydantic-ai, smolagents, agno, n8n, flowise, dify
  [ ] No .py file under agentkit/ or packs/ imports any of those packages
  [ ] A second, tiny pack (actions + one plan + one task, written in tmp_path) runs through
      agentkit.harness.run_one.run_task with ZERO changes to agentkit/ -- proof the framework is reusable
  [ ] No config/plan/task file is YAML any more (config/, packs/)
Hint: walk Path("agentkit").rglob("*.py") and read the text; parse imports with the `ast` module
rather than grepping, so comments and docstrings don't cause false alarms.
"""

import ast
from pathlib import Path

import pytest

# Write your tests below.
