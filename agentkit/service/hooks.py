"""
Signed webhooks: another system starts a question when something happens (a new version, a nightly
schedule). POST /v1/hooks/<name> on the service.

config/hooks.toml names each hook and the question it asks:

    [[hooks]]
    name = "release_check"
    question = "Is {part} ready for release?"   # {field}s come from the JSON body
    tenant = "acme"                              # optional; default: the service's default tenant
    # secret_env = "AGENTKIT_HOOK_RELEASE_CHECK_SECRET"   (the default name)

A request must carry
    X-Agentkit-Timestamp: <Unix seconds>
    X-Agentkit-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + body, with the hook's secret>

and is refused (401) when the signature is wrong or the timestamp is more than 5 minutes away; the
same signature twice is refused (409). A hook whose secret is not set is off (404, as unknown).
The body fields fill the question template only: each must be a short plain value (letters, digits,
spaces and . _ : / # -), so a field can not add instructions or structure. The run is the same dry
run as POST /v1/ask; the answer is 202 with the run id, and the result is at GET /v1/runs/<id>.

    python -m agentkit.service.hooks send http://127.0.0.1:8780 release_check '{"part": "PN-1234"}'
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import string
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentkit.config import CONFIG_DIR, ConfigError, read_config, validation_message

HOOKS_PATH = CONFIG_DIR / "hooks.toml"
TIMESTAMP_HEADER = "X-Agentkit-Timestamp"
SIGNATURE_HEADER = "X-Agentkit-Signature"
MAX_SKEW_S = 300
VALUE_RE = re.compile(r"^[A-Za-z0-9 ._:/#-]{1,120}$")
NAME_RE = r"^[a-z][a-z0-9_]{0,63}$"


class HookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_RE)
    question: str = Field(min_length=1, max_length=2000)
    tenant: Optional[str] = None
    secret_env: Optional[str] = None

    @property
    def fields(self) -> List[str]:
        return [f for _, f, _, _ in string.Formatter().parse(self.question) if f]

    @property
    def secret_name(self) -> str:
        return self.secret_env or f"AGENTKIT_HOOK_{self.name.upper()}_SECRET"


class HooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hooks: List[HookSpec] = Field(default_factory=list)


def load_hooks(path: Path = HOOKS_PATH) -> Dict[str, HookSpec]:
    if not Path(path).exists():
        return {}
    try:
        cfg = HooksConfig.model_validate(read_config(Path(path)))
    except ValidationError as e:
        raise ConfigError(validation_message(e, str(path))) from None
    for hook in cfg.hooks:
        if any(not f.isidentifier() for f in hook.fields):
            raise ConfigError(f"{path}: hook {hook.name!r}: template fields must be plain names")
    return {h.name: h for h in cfg.hooks}


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body,
                                hashlib.sha256).hexdigest()


class HookRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class HookGate:
    """Checks one delivery: hook known and on, fresh timestamp, right signature, not seen before;
    then makes the question from the body."""

    def __init__(self, hooks: Dict[str, HookSpec], env: Optional[Mapping[str, str]] = None,
                 clock=time.time, remember: int = 1024):
        self.hooks = hooks
        self.env = env if env is not None else os.environ
        self.clock = clock
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._remember = remember
        self._lock = threading.Lock()

    def question(self, name: str, headers: Mapping[str, str], body: bytes) -> Tuple[HookSpec, str]:
        hook = self.hooks.get(name)
        secret = self.env.get(hook.secret_name) if hook else None
        if hook is None or not secret:
            raise HookRefused(404, "no such hook")
        stamp = _header(headers, TIMESTAMP_HEADER)
        given = _header(headers, SIGNATURE_HEADER)
        if not stamp.isdigit() or abs(self.clock() - int(stamp)) > MAX_SKEW_S:
            raise HookRefused(401, "missing or stale timestamp")
        if not hmac.compare_digest(given.encode("utf-8"), signature(secret, stamp, body).encode("utf-8")):
            raise HookRefused(401, "bad signature")
        self._first_delivery(given)
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise HookRefused(400, "the body must be a JSON object") from None
        if not isinstance(data, dict):
            raise HookRefused(400, "the body must be a JSON object")
        values: Dict[str, str] = {}
        for field in hook.fields:
            value = data.get(field)
            if isinstance(value, bool) or not isinstance(value, (str, int)) or not VALUE_RE.match(str(value)):
                raise HookRefused(400, f"field {field!r}: a short plain value is required")
            values[field] = str(value).strip()
        return hook, hook.question.format(**values)

    def _first_delivery(self, sig: str) -> None:
        now = self.clock()
        with self._lock:
            for old, seen_at in list(self._seen.items()):
                if now - seen_at > 2 * MAX_SKEW_S:
                    del self._seen[old]
            if sig in self._seen:
                raise HookRefused(409, "duplicate delivery")
            self._seen[sig] = now
            while len(self._seen) > self._remember:
                self._seen.popitem(last=False)


def _header(headers: Mapping[str, str], name: str) -> str:
    return (headers.get(name) or headers.get(name.lower()) or "").strip()


# ---- sender (cron, scripts, tests) ------------------------------------------------------

def send(base_url: str, name: str, payload: Dict[str, Any], secret: str, timeout: float = 30.0) -> Tuple[int, Any]:
    body = json.dumps(payload).encode("utf-8")
    stamp = str(int(time.time()))
    req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/hooks/{name}", data=body, method="POST",
                                 headers={"Content-Type": "application/json", TIMESTAMP_HEADER: stamp,
                                          SIGNATURE_HEADER: signature(secret, stamp, body)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Send one signed delivery to a service hook.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("base_url")
    s.add_argument("name")
    s.add_argument("payload", help="a JSON object")
    s.add_argument("--secret-env", default=None, help="default AGENTKIT_HOOK_<NAME>_SECRET")
    args = ap.parse_args(argv)
    env_name = args.secret_env or f"AGENTKIT_HOOK_{args.name.upper()}_SECRET"
    secret = os.environ.get(env_name)
    if not secret:
        print(f"error: set {env_name}", file=sys.stderr)
        return 2
    status, data = send(args.base_url, args.name, json.loads(args.payload), secret)
    print(status, json.dumps(data))
    return 0 if status == 202 else 1


if __name__ == "__main__":
    sys.exit(main())
