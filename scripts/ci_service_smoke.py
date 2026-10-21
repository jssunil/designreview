"""
Service smoke check: start the agent service on the captured fixtures and ask it questions over
HTTP, as a caller would.

    python scripts/ci_service_smoke.py [--sim packs/designreview/fixtures] [--tasks packs/designreview/tasks]

Starts `python -m agentkit.service --http 127.0.0.1:<free port> --sim ... --skip-llm` as its own
process (offline, template answers, a temporary runs folder) and checks:
  - /healthz answers, and /v1/playbooks lists the live playbooks;
  - each routed task (plan = "route") asked through POST /v1/ask gets the plan its `routed_as`
    verifier expects, or a refusal when the task expects one, and ended = "done";
  - the same question as Server-Sent Events gives run_start, node events and then the answer;
  - /v1/runs/<run_id> gives the record of that run; other methods get 405, unknown paths 404;
  - MCP on POST /mcp, with our own MCP client (agentkit.transport.seat_rpc): initialize, tools/list
    names the live playbooks and "ask", tools/call answers the first routed question.
Exit 1 if any check fails (all problems are printed).
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class LoopbackWire:
    """A Wire for our MCP client that reaches 127.0.0.1 only. The real UrllibWire refuses every call in
    offline mode (AGENTKIT_OFFLINE=1, as in CI), and this check must not weaken that guard."""

    def send(self, method: str, url: str, headers: Dict[str, str], body: Optional[bytes], *, safe_to_repeat: bool):
        from agentkit.transport.wire import WireReply, read_body, read_error_body

        if not url.startswith("http://127.0.0.1:"):
            raise ValueError(f"LoopbackWire: {url} is not on 127.0.0.1")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        # read_body: the same framing tolerance as UrllibWire (a filter on some computers marks
        # responses on "/mcp" paths as chunked when they are not).
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return WireReply(resp.status, read_body(resp), dict(resp.headers))
        except urllib.error.HTTPError as e:
            return WireReply(e.code, read_error_body(e), dict(e.headers))


def mcp_check(base: str, task: Dict[str, Any]) -> List[str]:
    from agentkit.config import TenantLogin
    from agentkit.transport.seat_rpc import SeatCallFailure, SeatRpcClient, SeatSession

    client = SeatRpcClient(SeatSession(TenantLogin(name="service", base_url=base, email=""), LoopbackWire(),
                                       token="unused"))
    try:
        client.initialize()
        names = sorted(client.seat_catalog())
        args = {"question": task["prompt"], **({"tenant": task["tenant"]} if task["tenant"] else {})}
        payload = client.invoke_tool("ask", args)
    except SeatCallFailure as e:
        return [f"MCP: {e}"]
    problems = []
    if "ask" not in names or not any(n.startswith("pb_") for n in names):
        problems.append(f"MCP tools/list: {names}")
    got = (payload.get("route") or {}).get("plan") if isinstance(payload, dict) else None
    if task["plan"] and got != task["plan"]:
        problems.append(f"MCP tools/call {task['id']}: routed to {got!r}, expected {task['plan']!r}")
    if not problems:
        print(f"  MCP: tools {names}; tools/call {task['id']} -> {got}")
    return problems


def _call(base: str, path: str, method: str = "GET", payload: Any = None,
          accept: str = "application/json") -> Tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, method=method, data=data,
                                 headers={"Content-Type": "application/json", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(base: str, proc: subprocess.Popen, seconds: float = 30.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            if _call(base, "/healthz")[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def routed_tasks(tasks_dir: Path) -> List[Dict[str, Any]]:
    """[{id, prompt, tenant, plan or None (a refusal)}] for each task the router routes."""
    out = []
    for path in sorted(tasks_dir.glob("*.toml")):
        task = tomllib.loads(path.read_text(encoding="utf-8"))
        if task.get("plan") != "route":
            continue
        routed = [v for v in task.get("verifiers", []) if isinstance(v, dict) and v.get("name") == "routed_as"]
        plan = (routed[0].get("params") or {}).get("plan") if routed else None
        out.append({"id": task["id"], "prompt": task["prompt"], "tenant": (task.get("tenants") or [None])[0],
                    "plan": plan})
    return out


def _events(raw: bytes) -> List[Dict[str, Any]]:
    events = []
    for block in raw.decode("utf-8").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if "event" in fields:
            events.append({"event": fields["event"], "data": json.loads(fields.get("data") or "null")})
    return events


def smoke(sim: Path, tasks_dir: Path, runs_dir: Path) -> List[str]:
    problems: List[str] = []
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen([sys.executable, "-m", "agentkit.service", "--http", f"127.0.0.1:{port}", "--sim", str(sim),
                             "--skip-llm", "--runs-dir", str(runs_dir)], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not _wait_until_up(base, proc):
            out = proc.stdout.read() if proc.poll() is not None and proc.stdout else ""
            return [f"the service did not start on {base}: {out[-2000:]}"]
        status, raw = _call(base, "/v1/playbooks")
        books = json.loads(raw).get("playbooks", []) if status == 200 else []
        if not books:
            problems.append(f"/v1/playbooks: {status}, no live playbook")
        tasks = routed_tasks(tasks_dir)
        if not tasks:
            problems.append(f"no routed task in {tasks_dir}")
        first_run = None
        for task in tasks:
            payload = {"question": task["prompt"], **({"tenant": task["tenant"]} if task["tenant"] else {})}
            status, raw = _call(base, "/v1/ask", "POST", payload)
            if status != 200:
                problems.append(f"{task['id']}: POST /v1/ask -> {status} {raw[:200]!r}")
                continue
            out = json.loads(raw)
            got = (out.get("route") or {}).get("plan")
            if out.get("ended") != "done":
                problems.append(f"{task['id']}: ended={out.get('ended')} error={out.get('error')}")
            if task["plan"] and got != task["plan"]:
                problems.append(f"{task['id']}: routed to {got!r}, expected {task['plan']!r}")
            if not task["plan"] and out.get("outcome") != "declined":
                problems.append(f"{task['id']}: expected a refusal, got outcome={out.get('outcome')!r}")
            if not out.get("answer"):
                problems.append(f"{task['id']}: no answer text")
            first_run = first_run or out.get("run_id")
            print(f"  {task['id']}: {got or '-'} / {out.get('outcome')} / {out.get('answer_source')}")
        if tasks:
            status, raw = _call(base, "/v1/ask", "POST", {"question": tasks[0]["prompt"],
                                                         **({"tenant": tasks[0]["tenant"]} if tasks[0]["tenant"] else {})},
                                accept="text/event-stream")
            names = [e["event"] for e in _events(raw)] if status == 200 else []
            if not names or names[0] != "run_start" or names[-1] != "answer" or "node_end" not in names:
                problems.append(f"SSE: status {status}, events {names[:6]}...{names[-2:]}")
            else:
                print(f"  SSE: {len(names)} events, {names.count('node_end')} nodes, then the answer")
        if tasks:
            problems += mcp_check(base, tasks[0])
        if first_run:
            status, raw = _call(base, f"/v1/runs/{first_run}")
            if status != 200 or json.loads(raw).get("run_id") != first_run:
                problems.append(f"/v1/runs/{first_run}: {status}")
        for method, path, code in (("GET", "/v1/ask", 405), ("POST", "/healthz", 405), ("GET", "/nope", 404),
                                   ("GET", "/v1/runs/..%2f..%2fx", 404)):
            status, _ = _call(base, path, method, {} if method == "POST" else None)
            if status != code:
                problems.append(f"{method} {path}: {status}, expected {code}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sim", type=Path, default=ROOT / "packs" / "designreview" / "fixtures")
    ap.add_argument("--tasks", type=Path, default=ROOT / "packs" / "designreview" / "tasks")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="service-smoke-") as tmp:
        problems = smoke(args.sim, args.tasks, Path(tmp))
    for p in problems:
        print(f"PROBLEM: {p}")
    print("Service smoke OK" if not problems else f"Service smoke FAILED: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
