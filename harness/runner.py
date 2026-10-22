"""AgentSwitch official harness runner for Team 21 (Design Review seat).

Runs against the platform environment provided by the runner:
  AGENTSWITCH_BASE_URL: Target instance base URL (e.g. POST /api/mcp)
  AGENTSWITCH_TOKEN: Seat Bearer token
  AGENTSWITCH_INSTANCE: suryodaya or keystone
  OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL: Platform LLM endpoint

Outputs results.json conformant to the Release 8.1 harness specification:
  {"tasks": [{"id": "...", "title": "...", "passed": true, "score": 1.0, "evidence": "..."}],
   "summary": "..."}

Two parts, both in results.json:
  1. platform smoke checks (MCP reachable, our saved link, a direct read, the release gate, a clean refusal,
     the platform LLM's tool loop);
  2. our real agent (harness/agent_tasks.py): this instance's design-review tasks, run through agentkit as dry
     runs with the platform LLM, graded by our own verifiers against the platform's data -- one entry per
     task and one per check that applied. A failed check is passed=false with its detail as evidence.

    python -m harness.runner                                  # server: the runner injects the environment
    python -m harness.runner --instance keystone              # local, against the live platform
    python -m harness.runner --instance suryodaya --sim packs/designreview/fixtures --out runs/r.json
                                                              # offline: captured fixtures, agent tasks only
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import ssl

import httpx

# TLS against the operating system's trust store (like agentkit): on a machine behind a TLS-inspecting
# proxy only the OS store has the issuing CA. On the server the base URL is plain http, so nothing changes.
_TLS = ssl.create_default_context()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import argparse

# --- Environment Initialization (server-first, with local fallback) ---
# agentkit's load_env: like python-dotenv (never overrides the process), and it also tells the agent's
# task subprocesses which values came from .env, so a copied server variable is not mistaken for an
# injected one (see agentkit/config.py).
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from agentkit.config import load_env
    load_env()
except Exception:  # the runner must still start
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

parser = argparse.ArgumentParser(description="AgentSwitch Harness Runner")
parser.add_argument("--instance", choices=["suryodaya", "keystone"], default=None,
                    help="Target instance (defaults to AGENTSWITCH_INSTANCE or suryodaya)")
parser.add_argument("--sim", type=Path, default=None,
                    help="offline: replay these captured fixtures and run only the agent tasks (no login, no network)")
parser.add_argument("--fault", action="append", default=[],
                    help="with --sim: inject an agentkit.sim fault, e.g. fail_always:DesignVersion.list:flaky")
parser.add_argument("--out", default="results.json", help="where to write results.json (relative to the repo root)")
cli_args, _ = parser.parse_known_args()

if cli_args.instance:
    INSTANCE = cli_args.instance.lower()
else:
    INSTANCE = os.environ.get("AGENTSWITCH_INSTANCE", "suryodaya").lower()

# Resolve BASE and TOKEN based on instance.
# A token counts only when it was given to this PROCESS (the official runner injects it). A token that is
# only a copy in .env is ignored: those copies expire, and a stale one made every call 401 (2026-10-16).
# Without an injected token, the local run logs in with the tenant's password (as_client, below).
try:
    from agentkit.config import from_process
except Exception:  # agentkit not importable: fall back to the environment as is
    def from_process(name: str) -> Optional[str]:
        return os.environ.get(name)

if INSTANCE == "keystone" and from_process("AS_KEYSTONE_HARNESS_TOKEN"):
    BASE = (from_process("AS_KEYSTONE_HARNESS_BASE_URL") or "https://class.agentswitch.theschoolofai.in").rstrip("/")
    TOKEN = from_process("AS_KEYSTONE_HARNESS_TOKEN") or ""
else:
    BASE = (from_process("AGENTSWITCH_BASE_URL") or "https://agentswitch.theschoolofai.in").rstrip("/")
    TOKEN = from_process("AGENTSWITCH_TOKEN") or ""

# Server environment override (runner sets AGENTSWITCH_BASE_URL & AGENTSWITCH_TOKEN directly)
if from_process("AGENTSWITCH_TOKEN") and from_process("AGENTSWITCH_BASE_URL") and not cli_args.instance:
    BASE = from_process("AGENTSWITCH_BASE_URL").rstrip("/")
    TOKEN = from_process("AGENTSWITCH_TOKEN")

if not TOKEN and not cli_args.sim:
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from as_client import AgentSwitchClient
        client = AgentSwitchClient(INSTANCE)
        TOKEN = client.login()
        BASE = client.base_url
        print(f"[local-login] Authenticated {INSTANCE} via as_client: base_url={BASE}")
    except Exception as e:
        print(f"[local-login] Warning: Could not authenticate {INSTANCE}: {e}")

AUTH = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
MODEL = os.environ.get("OPENAI_MODEL", "agentswitch-default")

tasks: List[Dict[str, Any]] = []
GATE: Dict[str, Any] = {}  # the release gate's verdict read by check 5, for check 7


def record(task_id: str, title: str, passed: bool, evidence: Any, score: Optional[float] = None) -> None:
    task_entry = {
        "id": f"{INSTANCE}:{task_id}",
        "title": title,
        "passed": bool(passed),
        "score": (1.0 if passed else 0.0) if score is None else float(score),
        "evidence": str(evidence)[:500],
    }
    tasks.append(task_entry)
    status_str = "PASS" if passed else "FAIL"
    print(f"[{status_str}] {task_entry['id']}: {str(evidence)[:200]}", flush=True)


def mcp(method: str, params: Optional[Dict[str, Any]] = None, rid: int = 1) -> Dict[str, Any]:
    url = f"{BASE}/api/mcp"
    payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    resp = httpx.post(url, headers=AUTH, json=payload, timeout=120, verify=_TLS)
    resp.raise_for_status()
    return resp.json()


def call_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    body = mcp("tools/call", {"name": name, "arguments": arguments or {}})
    result = body.get("result") or {}
    content_list = result.get("content") or []
    text = "".join(c.get("text", "") for c in content_list if isinstance(c, dict) and c.get("type") == "text")
    if not text and "structuredContent" in result:
        text = json.dumps(result["structuredContent"])
    return text or json.dumps(body)[:2000]


def save_results(file_path: str = "results.json") -> None:
    passed_count = sum(1 for t in tasks if t.get("passed"))
    agent = [t for t in tasks if t["id"].startswith(f"{INSTANCE}:task:")]
    summary = f"{INSTANCE}: {passed_count}/{len(tasks)} checks passed"
    if agent:
        summary += f" (agent tasks: {sum(1 for t in agent if t['passed'])}/{len(agent)} passed)"
    result_data = {
        "tasks": tasks,
        "summary": summary,
    }
    out_path = PROJECT_ROOT / file_path
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2)
    print(f"\n[results] Wrote {len(tasks)} tasks to {out_path}: {summary}\n", flush=True)


def full_catalog(max_pages: int = 50) -> List[Dict[str, Any]]:
    """Every tool of the seat: tools/list, page by page (nextCursor)."""
    tools: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(max_pages):
        result = mcp("tools/list", {"cursor": cursor} if cursor else {}).get("result") or {}
        tools += result.get("tools") or []
        cursor = result.get("nextCursor")
        if not cursor:
            break
    return tools


def catalog_checks() -> None:
    """Two checks on the seat's tools against what our agent depends on (the pack's required tools):
    present with the arguments we send, and compatible with our captured fixture (each argument we send keeps
    its name and type; no new required argument). Other schema changes -- new optional filters, new fields --
    are information in the evidence, not a failure: they do not touch our agent."""
    try:
        from agentkit.registry import load_pack
        from agentkit.transport.catalog import ToolCatalog

        registry = load_pack("packs.designreview")
        required = registry.extras.get("required_tools") or {}
        live = ToolCatalog.from_tools(full_catalog())
    except Exception as exc:
        for name, title in (("required_tools_present", "The seat offers every tool our agent needs"),
                            ("required_tools_compatible", "Our agent's tool arguments are as in our captured data")):
            record(name, title, False, repr(exc))
        return
    problems = live.check_requirements(required)
    record("required_tools_present", "The seat offers every tool our agent needs", not problems,
           "; ".join(problems) if problems else f"all {len(required)} required tools offered, "
           f"with the arguments we send ({len(live)} tools in the seat)")
    try:
        fixture = json.loads((PROJECT_ROOT / "packs" / "designreview" / "fixtures" / f"{INSTANCE}.json")
                             .read_text(encoding="utf-8"))
        was = ToolCatalog.from_tools(fixture.get("tool_defs") or [])
        breaking, drifted = [], []
        for tool, sent in sorted(required.items()):
            if tool not in live or tool not in was:
                continue  # a missing tool is check required_tools_present's finding
            old, new = was.schema(tool), live.schema(tool)
            if old != new:
                drifted.append(tool)
            new_props = new.get("properties") or {}
            for arg in sent:
                if arg not in new_props:
                    breaking.append(f"{tool}.{arg} is gone")
                elif _types((old.get("properties") or {}).get(arg)) - _types(new_props[arg]):
                    breaking.append(f"{tool}.{arg} no longer accepts {sorted(_types((old.get('properties') or {}).get(arg)) - _types(new_props[arg]))}")
            for arg in sorted(set(new.get("required") or []) - set(old.get("required") or []) - set(sent)):
                breaking.append(f"{tool} now requires {arg}, which we do not send")
        info = (f"{len(drifted)} of our tools have other schema changes since the capture "
                f"({fixture.get('captured_at', '?')[:10]}; e.g. new optional filters): {drifted}" if drifted
                else "no schema change since the capture")
        record("required_tools_compatible", "Our agent's tool arguments are as in our captured data", not breaking,
               ("; ".join(breaking) + " -- " + info) if breaking else
               f"every argument we send keeps its name and type, and no new required argument; {info}")
    except Exception as exc:
        record("required_tools_compatible", "Our agent's tool arguments are as in our captured data", False, repr(exc))


def _types(prop: Any) -> set:
    """The JSON types a property schema accepts (its "type" and any "anyOf" / "oneOf" branch types)."""
    if not isinstance(prop, dict):
        return set()
    out = set()
    kind = prop.get("type")
    out |= set(kind) if isinstance(kind, list) else ({kind} if kind else set())
    for branch in (prop.get("anyOf") or []) + (prop.get("oneOf") or []):
        out |= _types(branch)
    return out


def run_checks() -> None:
    print(f"=== Starting AgentSwitch Harness Run for Instance: {INSTANCE} ===")
    print(f"Base URL: {BASE}")
    print(f"Model: {MODEL}")

    # 1. MCP is reachable with the seat token
    try:
        tool_list_resp = mcp("tools/list")
        tools = (tool_list_resp.get("result") or {}).get("tools") or []
        record("mcp_tools_list", "MCP tools/list works with the seat token", len(tools) > 0, f"{len(tools)} tools available")
    except Exception as exc:
        record("mcp_tools_list", "MCP tools/list works with the seat token", False, repr(exc))

    # 1b. Our agent's tools: offered, with the arguments we send, and unchanged since our captured data
    catalog_checks()

    # 2. A direct tool call: read our own saved harness link
    expected_url = ""
    try:
        text = call_tool("TeamHarness.get_mine", {})
        found = re.search(r"https://github\.com/[\w.-]+/[\w.-]+", text)
        expected_url = found.group(0) if found else ""
        record("mcp_tool_call", "TeamHarness.get_mine returns our saved link", bool(expected_url), text[:300])
    except Exception as exc:
        record("mcp_tool_call", "TeamHarness.get_mine returns our saved link", False, repr(exc))

    # 3. Model tool-calling check: agent loop uses tool and answers with the link
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            openai_client = OpenAI(http_client=httpx.Client(verify=_TLS, timeout=120))  # OPENAI_BASE_URL / _API_KEY
            tools_spec = [{
                "type": "function",
                "function": {
                    "name": "get_my_harness_link",
                    "description": "Return the GitHub link this team saved for its agent harness.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }]
            messages = [
                {"role": "system", "content": "You are a helpful agent. Use tools when needed. Answer briefly."},
                {"role": "user", "content": "What GitHub link did my team save for its harness? Reply with just the URL."},
            ]
            answer, used_tool = "", False
            for _ in range(4):
                reply = openai_client.chat.completions.create(model=MODEL, messages=messages, tools=tools_spec, max_tokens=512)
                msg = reply.choices[0].message
                if msg.tool_calls:
                    used_tool = True
                    messages.append({"role": "assistant", "content": msg.content or "",
                                     "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                    for tc in msg.tool_calls:
                        messages.append({"role": "tool", "tool_call_id": tc.id,
                                         "content": call_tool("TeamHarness.get_mine", {})})
                    continue
                answer = msg.content or ""
                break
            ok = used_tool and bool(expected_url) and expected_url.rstrip("/") in answer
            record("agent_answers_with_tool", "The agent uses the tool and answers with our link", ok,
                   f"used_tool={used_tool} answer={answer[:200]!r}")
        except Exception as exc:
            record("agent_answers_with_tool", "The agent uses the tool and answers with our link", False, repr(exc))
    else:
        record("agent_answers_with_tool", "The agent uses the tool and answers with our link", False,
               "OPENAI_API_KEY not provided in environment")

    # Domain configuration based on instance
    if INSTANCE == "keystone":
        target_file_id = "5f3c30f5-0af7-4cd8-bb54-576f701e136c"  # Hydraulic manifold mount
        part_name = "Hydraulic Manifold Mount"
    else:
        target_file_id = "39b69109-b56a-4bf4-af48-1ecf2b18f8a6"  # Bharat EV Battery Tray
        part_name = "Bharat EV Battery Tray Assembly"

    # 4. Domain check: DesignFile.get & DesignVersion.list
    try:
        file_text = call_tool("DesignFile.get", {"id": target_file_id})
        versions_text = call_tool("DesignVersion.list", {"file_id": target_file_id})
        has_file = target_file_id in file_text
        has_versions = "version_number" in versions_text or "commit_message" in versions_text
        ok = has_file and has_versions
        record("design_file_retrieval", f"DesignFile and DesignVersion retrieval for {part_name}",
               ok, f"file_retrieved={has_file} versions_retrieved={has_versions}")
    except Exception as exc:
        record("design_file_retrieval", f"DesignFile and DesignVersion retrieval for {part_name}", False, repr(exc))

    # 5. Domain check: Release Readiness gate evaluation
    try:
        gate_text = call_tool("endpoint.designreview.release_readiness", {"file_id": target_file_id})
        gate_data = json.loads(gate_text) if gate_text.startswith("{") else {}
        inner = gate_data.get("result", gate_data)
        GATE["ready"] = inner.get("ready")
        has_gate_status = "ready" in inner or "blockers" in inner
        record("release_readiness_gate", f"Release readiness verification for {part_name}",
               has_gate_status, f"ready={inner.get('ready')} critical_open={inner.get('critical_open')} blockers={len(inner.get('blockers', []))}")
    except Exception as exc:
        record("release_readiness_gate", f"Release readiness verification for {part_name}", False, repr(exc))

    # 6. Domain check: Refusal handling on non-existent file
    try:
        missing_resp = mcp("tools/call", {"name": "DesignFile.get", "arguments": {"id": "00000000-0000-0000-0000-000000000000"}})
        is_refusal = bool(missing_resp.get("error") or (missing_resp.get("result") or {}).get("isError"))
        record("refusal_unknown_entity", "Refusal and error propagation for non-existent design file",
               is_refusal, f"refused_cleanly={is_refusal} code={missing_resp.get('error', {}).get('code', 'result.isError')}")
    except Exception as exc:
        # an exception is not a refusal: the call never got a clear answer (admin feedback: exceptions fail)
        record("refusal_unknown_entity", "Refusal and error propagation for non-existent design file", False, repr(exc))

    # 7. Agent Domain Evaluation: Platform LLM answers release readiness inquiry using tool
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            openai_client = OpenAI(http_client=httpx.Client(verify=_TLS, timeout=120))
            domain_tools = [{
                "type": "function",
                "function": {
                    "name": "check_release_readiness",
                    "description": "Check if a CAD design file is ready for release, returning blockers and reasons.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_id": {"type": "string", "description": "The design file UUID"}
                        },
                        "required": ["file_id"]
                    },
                },
            }]
            domain_prompt = f"Check the release readiness of design file {target_file_id}. Is this part ready for release? Explain any blockers."
            messages = [
                {"role": "system", "content": "You are an engineering design review assistant. Use the check_release_readiness tool to inspect parts."},
                {"role": "user", "content": domain_prompt},
            ]
            eval_answer, tool_invoked = "", False
            for _ in range(4):
                reply = openai_client.chat.completions.create(model=MODEL, messages=messages, tools=domain_tools, max_tokens=512)
                msg = reply.choices[0].message
                if msg.tool_calls:
                    tool_invoked = True
                    messages.append({"role": "assistant", "content": msg.content or "",
                                     "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                    for tc in msg.tool_calls:
                        messages.append({"role": "tool", "tool_call_id": tc.id,
                                         "content": call_tool("endpoint.designreview.release_readiness", {"file_id": target_file_id})})
                    continue
                eval_answer = msg.content or ""
                break
            # Verify tool was called and model addressed release readiness
            # The answer's verdict must agree with the gate (check 5), read with our readiness parser,
            # which handles negation -- not keywords ("nothing is blocking, it is ready" contains "block").
            from packs.designreview.checks import says_not_ready, says_ready
            gate_ready = GATE.get("ready")
            said_ready, said_not = says_ready(eval_answer), says_not_ready(eval_answer)
            stated = "ready" if said_ready and not said_not else "not ready" if said_not and not said_ready \
                else "both" if said_ready else "none"
            verdict_ok = tool_invoked and isinstance(gate_ready, bool) and \
                stated == ("ready" if gate_ready else "not ready")
            record("agent_design_review_verdict", f"Agent evaluates release readiness for {part_name}",
                   verdict_ok, f"tool_invoked={tool_invoked} gate_ready={GATE.get('ready')} answer_says={stated} "
                   f"answer={eval_answer[:600]!r}")
        except Exception as exc:
            record("agent_design_review_verdict", f"Agent evaluates release readiness for {part_name}", False, repr(exc))
    else:
        record("agent_design_review_verdict", f"Agent evaluates release readiness for {part_name}", False,
               "OPENAI_API_KEY not provided in environment")


def run_agent_part() -> None:
    from harness.agent_tasks import MAX_ENTRIES, run_agent_tasks
    run_agent_tasks(INSTANCE, record, sim=cli_args.sim, faults=cli_args.fault, room=MAX_ENTRIES - len(tasks))


def main() -> int:
    try:
        if cli_args.sim:
            print(f"=== Offline run for {INSTANCE}: captured fixtures {cli_args.sim}; platform checks skipped ===")
        else:
            run_checks()
        run_agent_part()
    finally:
        save_results(cli_args.out)
    all_passed = all(t.get("passed") for t in tasks)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
