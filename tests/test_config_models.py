"""
Offline tests for config / plan / task files (TOML, validated by pydantic).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Use cases to cover:
  [ ] The shipped config/tenants.toml, config/routing.toml, every plan and every task file load
  [ ] read_config reads .toml and .json; a .yaml file or broken TOML raises ConfigError naming the file
  [ ] tenants: a misspelt key (pasword_env) is rejected; the password value never appears in repr()
  [ ] routing: rpm = 0 / unknown provider in a tier / duplicate provider / misspelt key -> ConfigError
  [ ] plans: an unknown node key ("tool = ...") is rejected at LOAD time, with the field path in the message
  [ ] plans: requires = "sometimes" is rejected; a rule with no condition, or that adds nothing, is rejected
  [ ] tasks: empty prompt, empty tenants list, unknown key, unknown verifier, missing plan -> rejected
  [ ] tasks: two files with the same id -> rejected
  [ ] A task survives task.json round-trip (TaskDef.model_validate(task.to_dict()) == task)
Hint: write small TOML/JSON files into tmp_path and load them with the real loaders.
"""

import pytest

from agentkit.config import ConfigError, load_tenants, read_config
from agentkit.graph import load_plan
from agentkit.harness.tasks import TaskDef, load_tasks
from agentkit.llm.gateway import load_routing

# Write your tests below.
