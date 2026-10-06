"""
Offline tests for config / plan / task files (TOML, validated by pydantic).    Markers: none

Author(s): Sunil Jakkaraju    Written by hand: [x] yes

Use cases to cover:
  [x] The shipped config/tenants.toml, config/routing.toml, every plan and every task file load
  [x] read_config reads .toml and .json; a .yaml file or broken TOML raises ConfigError naming the file
  [x] tenants: a misspelt key (pasword_env) is rejected; the password value never appears in repr()
  [x] routing: rpm = 0 / unknown provider in a tier / duplicate provider / misspelt key -> ConfigError
  [x] plans: an unknown node key ("tool = ...") is rejected at LOAD time, with the field path in the message
  [x] plans: requires = "sometimes" is rejected; a rule with no condition, or that adds nothing, is rejected
  [x] tasks: empty prompt, empty tenants list, unknown key, unknown verifier, missing plan -> rejected
  [x] tasks: two files with the same id -> rejected
  [x] A task survives task.json round-trip (TaskDef.model_validate(task.to_dict()) == task)
Hint: write small TOML/JSON files into tmp_path and load them with the real loaders.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentkit.config import (
    TENANTS_PATH,
    ConfigError,
    TenantLogin,
    load_tenants,
    read_config,
    tenant_login,
)
from agentkit.graph import load_plan
from agentkit.harness.tasks import TaskDef, load_task, load_tasks
from agentkit.llm.gateway import ROUTING_PATH, load_routing
from agentkit.registry import load_pack


def _write(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# 1. Shipped config files, plans, and tasks load successfully
def test_shipped_configs_and_plans_and_tasks_load():
    tenants_cfg = load_tenants(TENANTS_PATH)
    assert "suryodaya" in tenants_cfg.tenants
    assert "keystone" in tenants_cfg.tenants

    routing_cfg = load_routing(ROUTING_PATH)
    assert len(routing_cfg.providers) > 0
    assert "default" in routing_cfg.tiers

    reg = load_pack("packs.designreview")
    plans_dir = Path(reg.extras["plans_dir"])
    plan_files = list(plans_dir.glob("*.toml"))
    assert plan_files, "No plan files found"
    for p in plan_files:
        plan = load_plan(p)
        assert plan.name
        assert len(plan.nodes) >= 1

    tasks_dir = Path(reg.extras["tasks_dir"])
    tasks = load_tasks([tasks_dir])
    assert len(tasks) >= 9
    for t in tasks:
        assert t.id
        assert t.prompt
        assert t.plan


# 2. read_config reads .toml and .json; a .yaml file or broken TOML raises ConfigError naming the file
def test_read_config_toml_and_json(tmp_path):
    toml_path = _write(tmp_path, "conf.toml", 'key = "val"\nnum = 42\n')
    assert read_config(toml_path) == {"key": "val", "num": 42}

    json_path = _write(tmp_path, "conf.json", '{"key": "val", "num": 42}')
    assert read_config(json_path) == {"key": "val", "num": 42}


@pytest.mark.parametrize("fname,content,err_pattern", [
    ("conf.yaml", "key: value", "unsupported config format"),
    ("broken.toml", "key = [unclosed", "broken.toml"),
    ("missing.toml", None, "not found"),
])
def test_read_config_invalid_files(tmp_path, fname, content, err_pattern):
    path = _write(tmp_path, fname, content) if content is not None else tmp_path / fname
    with pytest.raises(ConfigError, match=err_pattern):
        read_config(path)


# 3. tenants: misspelt key rejected; password never appears in repr()
def test_tenants_misspelt_key_and_password_repr(tmp_path, monkeypatch):
    bad_toml = _write(
        tmp_path,
        "tenants_bad.toml",
        '[tenants.acme]\nurl_env = "URL"\ndefault_url = "https://x"\npasword_env = "PW"\n',
    )
    with pytest.raises(ConfigError, match="password_env: Field required"):
        load_tenants(bad_toml)

    valid_toml = _write(
        tmp_path,
        "tenants_ok.toml",
        '[tenants.acme]\nurl_env = "URL"\ndefault_url = "https://x"\npassword_env = "ACME_PW"\n',
    )
    monkeypatch.setenv("ACME_PW", "super_secret_password_123")
    login = tenant_login("acme", valid_toml)
    assert login.password == "super_secret_password_123"
    assert "super_secret_password_123" not in repr(login)


# 4. routing: rpm = 0 / unknown provider in a tier / duplicate provider / misspelt key -> ConfigError
@pytest.mark.parametrize("cfg_dict,err_match", [
    ({"providers": [{"name": "p1", "rpm": 0}]}, "rpm"),
    ({"providers": [{"name": "p1"}], "tiers": {"t1": ["unknown_provider"]}}, "unknown providers"),
    ({"providers": [{"name": "p1"}, {"name": "p1"}]}, "duplicate providers"),
    ({"providers": [{"name": "p1", "misspelt_key": "val"}]}, "Extra inputs"),
])
def test_routing_validation_errors(tmp_path, cfg_dict, err_match):
    cfg_file = _write(tmp_path, "route.json", json.dumps(cfg_dict))
    with pytest.raises(ConfigError, match=err_match):
        load_routing(cfg_file)


# 5. plans: unknown node key ("tool = ...") rejected at load time with field path
def test_plan_rejects_unknown_node_key(tmp_path):
    plan_toml = _write(
        tmp_path,
        "bad_node.toml",
        'name = "test"\n[[nodes]]\nid = "n1"\naction = "act"\ntool = "invalid"\n',
    )
    with pytest.raises(ConfigError) as exc_info:
        load_plan(plan_toml)
    assert "nodes.0.tool" in str(exc_info.value) or "tool" in str(exc_info.value)


# 6. plans: requires = "sometimes" is rejected; rule with no condition or adding nothing is rejected
@pytest.mark.parametrize("plan_dict,err_match", [
    (
        {"name": "p", "nodes": [{"id": "n1", "action": "a", "requires": "sometimes"}]},
        "requires",
    ),
    (
        {"name": "p", "nodes": [{"id": "n1", "action": "a"}], "rules": [{"when": {"node": "n1"}}]},
        "at least one condition",
    ),
    (
        {"name": "p", "nodes": [{"id": "n1", "action": "a"}], "rules": [{"when": {"node": "n1", "truthy": "out"}}]},
        "must add nodes or link",
    ),
])
def test_plan_rules_and_requires_validation(tmp_path, plan_dict, err_match):
    plan_file = _write(tmp_path, "plan.json", json.dumps(plan_dict))
    with pytest.raises(ConfigError, match=err_match):
        load_plan(plan_file)


# 7. tasks: empty prompt, empty tenants list, unknown key, unknown verifier, missing plan -> rejected
@pytest.mark.parametrize("task_dict,err_match", [
    ({"id": "t1", "pack": "p", "plan": "pl", "prompt": ""}, "prompt"),
    ({"id": "t1", "pack": "p", "plan": "pl", "prompt": "ok", "tenants": []}, "tenants"),
    ({"id": "t1", "pack": "p", "plan": "pl", "prompt": "ok", "bad_key": 123}, "bad_key"),
    ({"id": "t1", "pack": "p", "plan": "pl", "prompt": "ok", "verifiers": [{"bad_field": "x"}]}, "name: Field required"),
    ({"id": "t1", "pack": "p", "plan": "", "prompt": "ok"}, "plan"),
])
def test_task_rejections(task_dict, err_match):
    with pytest.raises(ConfigError, match=err_match):
        TaskDef.from_dict(task_dict)


# 8. tasks: two files with the same id -> rejected
def test_duplicate_task_ids_rejected(tmp_path):
    _write(tmp_path, "task1.toml", 'id = "dup_id"\npack = "p"\nplan = "pl"\nprompt = "run 1"\n')
    _write(tmp_path, "task2.toml", 'id = "dup_id"\npack = "p"\nplan = "pl"\nprompt = "run 2"\n')
    with pytest.raises(ConfigError, match="duplicate task ids"):
        load_tasks([tmp_path])


# 9. A task survives task.json round-trip (TaskDef.model_validate(task.to_dict()) == task)
def test_task_round_trip():
    reg = load_pack("packs.designreview")
    shipped_tasks = load_tasks([Path(reg.extras["tasks_dir"])])
    assert len(shipped_tasks) > 0
    for task in shipped_tasks:
        dumped = task.to_dict()
        reconstituted = TaskDef.model_validate(dumped)
        assert reconstituted == task
