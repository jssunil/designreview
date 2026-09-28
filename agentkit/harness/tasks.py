"""
Task files: what the harness runs, as data (TOML, validated by pydantic).

    id = "t01_example"
    pack = "packs.<name>"
    plan = "my_plan"                        # a file in the pack's plans dir
    prompt = "The question the agent answers"
    params = { record_id = "..." }          # plan parameters (prompt is added automatically)
    tenants = ["<tenant>"]                  # default: config/project.toml default_tenant

    [[verifiers]]
    name = "<check registered by the pack>"
    params = { record_id = "..." }

Every task also gets the built-in checks (run_completed, tool_calls_policy,
answer_present). Unknown keys, unknown verifier names, missing plans and
duplicate ids are errors at load time, not surprises at grading time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agentkit.config import ConfigError, default_tenant, read_config, validation_message
from agentkit.registry import Registry

TASK_SUFFIX = ".toml"


class VerifierSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    params: Dict[str, Any] = Field(default_factory=dict)


class TaskDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    pack: str = Field(min_length=1)
    plan: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)
    tenants: List[str] = Field(default_factory=lambda: [default_tenant()], min_length=1)
    verifiers: List[VerifierSpec] = Field(default_factory=list)
    description: str = ""
    source: str = ""

    @field_validator("verifiers", mode="before")
    @classmethod
    def _names_as_shorthand(cls, v: Any) -> Any:
        # allow `verifiers = ["answer_audited"]` as shorthand for [{name = ...}]
        return [{"name": x} if isinstance(x, str) else x for x in (v or [])]

    def plan_params(self) -> Dict[str, Any]:
        return {**self.params, "prompt": self.prompt}

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: Dict[str, Any], source: str = "") -> "TaskDef":
        try:
            return cls.model_validate({**d, "source": source or d.get("source", "")})
        except ValidationError as e:
            raise ConfigError(validation_message(e, source or f"task {d.get('id')!r}")) from None


def load_task(path: Path) -> TaskDef:
    return TaskDef.from_dict(read_config(Path(path)), source=str(path))


def load_tasks(paths: Iterable[Path]) -> List[TaskDef]:
    files: List[Path] = []
    for p in paths:
        p = Path(p)
        files += sorted(p.glob(f"*{TASK_SUFFIX}")) if p.is_dir() else [p]
    tasks = [load_task(f) for f in files]
    ids = [t.id for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ConfigError(f"duplicate task ids {dupes}")
    return tasks


def plan_path(registry: Registry, plan: str) -> Optional[Path]:
    plans_dir = registry.extras.get("plans_dir")
    return Path(plans_dir) / f"{plan}.toml" if plans_dir is not None else None


def validate_task(task: TaskDef, registry: Registry, builtin: Iterable[str]) -> None:
    path = plan_path(registry, task.plan)
    if path is not None and not path.exists():
        raise ConfigError(f"task {task.id}: plan {task.plan!r} not found ({path})")
    known = set(registry.checks) | set(builtin)
    for v in task.verifiers:
        if v.name not in known:
            raise ConfigError(f"task {task.id}: unknown verifier {v.name!r}")
