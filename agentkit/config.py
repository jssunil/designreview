"""
Project paths, .env loading, config-file reading and tenant credentials.

Config files are TOML (read with the standard library's tomllib) or JSON.
Tenant *shape* (which env variables hold a tenant's URL and password) comes
from config/tenants.toml, validated by a pydantic model, so adding a tenant
is a config edit, not a code edit. Values are only ever read from the
environment.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
TENANTS_PATH = CONFIG_DIR / "tenants.toml"
PROJECT_PATH = CONFIG_DIR / "project.toml"

_env_loaded = False


class ConfigError(ValueError):
    """A config/plan/task file that is missing, unreadable or invalid."""


def load_env(path: Optional[Path] = None) -> None:
    """Load PROJECT_ROOT/.env once, without overriding variables already set
    in the process. Falls back to a tiny parser if python-dotenv is absent."""
    global _env_loaded
    if _env_loaded and path is None:
        return
    env_path = path or PROJECT_ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=env_path, override=False)
        except ImportError:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())
    if path is None:
        _env_loaded = True


def read_config(path: Path) -> Dict[str, Any]:
    """Read a .toml or .json file into a dict. A missing file is an error."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        if path.suffix == ".toml":
            with open(path, "rb") as f:
                return tomllib.load(f)
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, ValueError) as e:
        raise ConfigError(f"{path}: {e}") from e
    raise ConfigError(f"{path}: unsupported config format {path.suffix!r} (use .toml or .json)")


def validation_message(e: ValidationError, source: str) -> str:
    """One readable line per problem: 'source: field.path: message'."""
    parts = []
    for err in e.errors():
        where = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{where}: {err['msg']}")
    return f"{source}: " + "; ".join(parts)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantSpec(_Strict):
    label: str = ""
    url_env: str
    default_url: str
    password_env: str


class TenantsConfig(_Strict):
    email_env: str = "AS_EMAIL"
    default_email: str = ""
    tenants: Dict[str, TenantSpec]


def load_tenants(path: Path = TENANTS_PATH) -> TenantsConfig:
    try:
        return TenantsConfig.model_validate(read_config(path))
    except ValidationError as e:
        raise ConfigError(validation_message(e, str(path))) from None


def default_pack(path: Path = PROJECT_PATH) -> str:
    """The pack to use when none is named (config/project.toml: default_pack)."""
    pack = read_config(path).get("default_pack") if Path(path).exists() else None
    if not pack:
        raise ConfigError(f"no default_pack in {path}; pass --pack explicitly")
    return str(pack)


def default_tenant(path: Path = PROJECT_PATH) -> str:
    """The tenant to use when none is named (config/project.toml: default_tenant)."""
    tenant = read_config(path).get("default_tenant") if Path(path).exists() else None
    if not tenant:
        raise ConfigError(f"no default_tenant in {path}; name the tenant explicitly")
    return str(tenant)


@dataclass(frozen=True)
class TenantLogin:
    name: str
    base_url: str
    email: str
    password: str = ""

    def __repr__(self) -> str:  # never print the password
        return f"TenantLogin(name={self.name!r}, base_url={self.base_url!r}, email={self.email!r})"


def tenant_login(name: str, tenants_path: Path = TENANTS_PATH) -> TenantLogin:
    """Resolve one tenant's URL + credentials. Raises ValueError for an
    unknown tenant or a missing password (naming the variable to set)."""
    token = os.getenv("AGENTSWITCH_TOKEN")
    base_url = os.getenv("AGENTSWITCH_BASE_URL")
    active_instance = os.getenv("AGENTSWITCH_INSTANCE", "").lower()
    if token and base_url and (not active_instance or active_instance == name.lower()):
        instance = active_instance or name.lower()
        email = os.getenv("AS_EMAIL", "team21@theschoolofai.in")
        return TenantLogin(name=instance, base_url=base_url, email=email, password="")
    load_env()
    cfg = load_tenants(tenants_path)
    key = name.lower()
    if key not in cfg.tenants:
        raise ValueError(f"Unknown environment '{name}'. Choose one of: {', '.join(sorted(cfg.tenants))}.")
    t = cfg.tenants[key]
    password = os.getenv(t.password_env, "")
    if not password:
        raise ValueError(f"Missing password for '{key}'. Please ensure {t.password_env} is set in your .env file.")
    return TenantLogin(name=key, base_url=os.getenv(t.url_env, "") or t.default_url,
                       email=os.getenv(cfg.email_env, "") or cfg.default_email, password=password)
