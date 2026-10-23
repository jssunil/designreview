"""
Replay: continue a finished run from its saved graph with one node's result
replaced, then rebuild the finding and the answer and grade the new run.

    # one replay: a pack-registered fault, or any NodeResult you write yourself
    python -m agentkit.harness.replay runs/<run_id> --fault gate_reports_ready --sim packs/<pack>/fixtures
    python -m agentkit.harness.replay runs/<run_id> --node release_gate --result my_result.json --sim ...

    # end-to-end calibration of a whole batch (what CI runs)
    python -m agentkit.harness.replay --calibrate runs/batches/<batch_id> --sim packs/<pack>/fixtures

How a replay works (agentkit.graph.engine.GraphStore.fork):
  1. load runs/<run_id>/graph.json; replace the chosen node's result
  2. everything downstream of it is undone (plan nodes back to pending, nodes
     the planner added removed -- its rules decide again)
  3. the plan's executor re-runs those nodes against the platform: a captured
     fixture with --sim (offline, clock frozen to the capture time), else the
     live platform (reads only: a replay is always a dry run)
  4. the pack builds the finding and the answer again (template answer
     unless --llm), and the new run folder is graded like any other

The new folder (default runs/<run_id>--<label>, or <batch>/replays/... when
calibrating) holds the usual run files, with the original task.json and
ground-truth files copied in, a journal that is the original calls followed
by the replayed ones, and replay.json saying what was replaced.

Calibration. mutants.py edits a finished record, so it tests the checks on
their own. A replay fault (registry.replay_fault) replaces what one node
returned, so it tests the whole path node -> finding -> answer -> check.
For every passing run of the batch (first repeat only):
  - a control replay at the plan's first node with its OWN result: everything
    re-runs, and the run must still pass (the replay itself is faithful)
  - each applicable fault: the fault's target check must fail
Writes <batch>/replay_calibration.json; exit 1 on a missed fault or a control
that did not pass.
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from agentkit.agents import answer_with_pack, plan_executor, record_steps
from agentkit.agents.llm_planner import plan_for_route
from agentkit.clock import frozen_now
from agentkit.graph import GraphStore, NodeResult, RunEnv
from agentkit.harness.checks import specs_for
from agentkit.harness.grade import grade_run
from agentkit.harness.run_one import GRAPH_FILE, plan_and_params
from agentkit.harness.tasks import TaskDef
from agentkit.record import TaskRun, dump_record, load_record
from agentkit.record.durable_io import read_jsonl
from agentkit.registry import Registry, load_pack
from agentkit.telemetry import Tracer, export_run, span, use_tracer
from agentkit.transport.journal import JOURNAL_FILE, JournaledSeatClient

COPIED_FILES = ("task.json", "ground_truth_before.json", "ground_truth_after.json")
REPLAY_FILE = "replay.json"
CALIBRATION_FILE = "replay_calibration.json"


@contextlib.contextmanager
def platform_for(tenant: str, registry: Registry, sim: Optional[Path]) -> Iterator[Any]:
    """The client a replay reads through: the tenant's captured fixture (clock
    frozen to its capture time, like an offline batch) or the live seat."""
    if sim is not None:
        from agentkit.harness.batch import SimSession

        session = SimSession(Path(sim), [], registry.read_only)
        platform = session.reader_for(tenant)
        platform.armed = True
        with frozen_now(platform.fixture.captured_at):
            yield platform
        return
    from agentkit.transport import SeatRpcClient

    client = SeatRpcClient.for_tenant(tenant, extra_read_only=registry.read_only)
    client.initialize()
    yield client


def replay_run(run_dir: Path, update: Dict[str, NodeResult], out_dir: Path, *, client: Any,
               registry: Optional[Registry] = None, gateway: Any = None, label: str = "") -> Dict[str, Any]:
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    task = TaskDef.from_dict(load_record(run_dir / "task.json"))
    original = load_record(run_dir / "taskrun.json")
    registry = registry or load_pack(task.pack)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} already exists; pick another --out")
    out_dir.mkdir(parents=True)
    for name in COPIED_FILES:
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, out_dir / name)
    # The journal: what the original run called, then what the replay calls.
    if (run_dir / JOURNAL_FILE).exists():
        shutil.copy2(run_dir / JOURNAL_FILE, out_dir / JOURNAL_FILE)
    else:
        (out_dir / JOURNAL_FILE).touch()
    last_seq = max((e.get("seq") or 0 for e in read_jsonl(out_dir / JOURNAL_FILE)), default=0)

    run_id = out_dir.name
    tenant = original.get("tenant") or ""
    run = TaskRun(task_id=task.id, prompt=task.prompt, run_id=run_id, tenant=tenant, dry_run=True,
                  harness_task=task.id, pack=task.pack)
    run.open_in(out_dir)
    tracer = Tracer(run_id)
    try:
        with use_tracer(tracer), span("task", task.id, tenant=tenant, dry_run=True, pack=task.pack, plan=task.plan,
                                      replay_of=original.get("run_id"), replaced=sorted(update)) as root:
            try:
                plan_name, params, run.route = plan_and_params(task, registry, client, original.get("route"))
                plan = plan_for_route(registry, plan_name, run.route)
                fork = GraphStore.load(run_dir / GRAPH_FILE, registry).fork(
                    update, checkpoint_path=out_dir / GRAPH_FILE, run_id=run_id)
                journaled = JournaledSeatClient(client, out_dir / JOURNAL_FILE,
                                                extra_read_only=tuple(registry.read_only), start_seq=last_seq + 1)
                env = RunEnv(client=journaled, run_id=run_id, params=params, dry_run=True,
                             tenant=tenant, gateway=gateway)
                plan_executor(env, registry, plan, checkpoint_path=out_dir / GRAPH_FILE,
                              retry_backoff_s=0).continue_from(fork)
                record_steps(run, fork)
                answer_with_pack(run, fork, env, registry, task.prompt)
                if gateway is None:
                    run.warnings.append("LLM skipped (replay): template answer")
                run.warnings.append(f"replay of {original.get('run_id')}: replaced {', '.join(sorted(update))}"
                                    + (f" ({label})" if label else ""))
                run.finish(out_dir)
            except Exception as e:  # recorded, never swallowed silently
                run.finish(out_dir, error=f"{type(e).__name__}: {e}")
            root.set(ended=run.ended, ok=run.ended == "done", outcome=(run.finding or {}).get("outcome"))
    finally:
        try:
            export_run(tracer, out_dir)
        except Exception:  # telemetry is never graded
            pass
    # Graders need to know where the replay's own calls start (the journal also holds the original's).
    dump_record(out_dir / REPLAY_FILE, {"replay_of": original.get("run_id"), "replay_start_seq": last_seq + 1})
    score = grade_run(out_dir, registry)
    original_score = load_record(run_dir / "score.json") if (run_dir / "score.json").exists() else {}
    meta = {"replay_of": original.get("run_id"), "replay_start_seq": last_seq + 1, "source_dir": str(run_dir),
            "label": label,
            "replaced": {k: v.to_dict() for k, v in update.items()}, "ended": run.ended, "error": run.error,
            "status": score["status"], "original_status": original_score.get("status")}
    dump_record(out_dir / REPLAY_FILE, meta)
    return {**meta, "score": score}


def _failed_checks(score: Dict[str, Any]) -> List[str]:
    return sorted({c["check"] for c in score.get("checks") or [] if c.get("status") == "fail"})


def _first_repeat_runs(batch_dir: Path) -> List[Path]:
    manifest = load_record(batch_dir / "manifest.json")
    runs_root = batch_dir.parent.parent
    return [runs_root / e["run_id"] for e in manifest.get("runs") or []
            if e.get("status") == "ran" and (e.get("repeat_index") or 1) == 1]


def calibrate_replays(batch_dir: Path, sim: Optional[Path], registry: Optional[Registry] = None) -> Dict[str, Any]:
    batch_dir = Path(batch_dir)
    out_root = batch_dir / "replays"
    faults: Dict[str, Dict[str, Any]] = {}
    controls = {"ran": 0, "passed": 0, "failed_on": []}
    skipped: List[str] = []
    regs: Dict[str, Registry] = {}
    for run_dir in _first_repeat_runs(batch_dir):
        task = load_record(run_dir / "task.json")
        reg = registry or regs.setdefault(task["pack"], load_pack(task["pack"]))
        score = load_record(run_dir / "score.json") if (run_dir / "score.json").exists() else grade_run(
            run_dir, reg, write=False)
        if score.get("status") != "pass":
            skipped.append(run_dir.name)  # only a passing run can show a fault being caught
            continue
        in_task = {s["name"] for s in specs_for(task)}
        nodes = {n["id"]: n for n in load_record(run_dir / GRAPH_FILE)["nodes"]}
        tenant = load_record(run_dir / "taskrun.json").get("tenant") or ""

        first = next((n for n in nodes.values() if n.get("added_by") == "plan" and n.get("result")), None)
        if first is not None:
            with platform_for(tenant, reg, sim) as client:
                res = replay_run(run_dir, {first["id"]: NodeResult.from_dict(first["result"])},
                                 out_root / f"{run_dir.name}--control", client=client, registry=reg,
                                 label="control: same result")
            controls["ran"] += 1
            if res["status"] == "pass":
                controls["passed"] += 1
            else:
                controls["failed_on"].append({"run": run_dir.name, "status": res["status"],
                                              "failed_checks": _failed_checks(res["score"])})

        for fault in reg.replay_faults.values():
            row = faults.setdefault(fault.name, {"node": fault.node, "target": fault.target, "applied": 0,
                                                 "caught": 0, "missed_on": []})
            node = nodes.get(fault.node)
            if fault.target not in in_task or node is None or not node.get("result"):
                continue
            changed = fault.apply(NodeResult.from_dict(node["result"]))
            if changed is None:
                continue
            row["applied"] += 1
            with platform_for(tenant, reg, sim) as client:
                res = replay_run(run_dir, {fault.node: changed}, out_root / f"{run_dir.name}--{fault.name}",
                                 client=client, registry=reg, label=f"fault {fault.name}")
            if fault.target in _failed_checks(res["score"]):
                row["caught"] += 1
            else:
                row["missed_on"].append({"run": run_dir.name, "status": res["status"]})
    missed = sorted(n for n, r in faults.items() if r["caught"] < r["applied"])
    ok = not missed and controls["passed"] == controls["ran"]
    return {"faults": faults, "missed": missed, "controls": controls, "skipped_runs_not_passing": skipped,
            "ok": ok}


def render(report: Dict[str, Any]) -> str:
    lines = [f"{'replay fault':26} {'node':16} {'target check':28} caught/applied"]
    for name, r in sorted(report["faults"].items()):
        flag = "  MISSED" if r["caught"] < r["applied"] else ""
        lines.append(f"{name:26} {r['node']:16} {r['target']:28} {r['caught']}/{r['applied']}{flag}")
    c = report["controls"]
    lines += ["", f"controls (same result re-run): {c['passed']}/{c['ran']} still pass"
              + (f"; NOT on {c['failed_on']}" if c["failed_on"] else ""),
              f"missed: {report['missed'] or 'none'}"]
    if report["skipped_runs_not_passing"]:
        lines.append(f"skipped (run did not pass): {report['skipped_runs_not_passing']}")
    lines.append("REPLAY CALIBRATION " + ("OK" if report["ok"] else "FAILED"))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Re-run a finished run with one node's result replaced, and grade it.")
    ap.add_argument("run_dir", nargs="?", type=Path, help="runs/<run_id> to replay")
    ap.add_argument("--calibrate", type=Path, metavar="BATCH_DIR",
                    help="replay every registered fault on every passing run of a batch")
    ap.add_argument("--fault", help="a fault the pack registered (registry.replay_fault)")
    ap.add_argument("--node", help="node whose result to replace (with --result)")
    ap.add_argument("--result", type=Path, help="JSON NodeResult: {verdict, data, reason, error_kind}")
    ap.add_argument("--sim", type=Path, help="captured fixture file or per-tenant folder (offline); default: live")
    ap.add_argument("--out", type=Path, help="new run folder (default: runs/<run_id>--<label>)")
    ap.add_argument("--llm", action="store_true", help="word the answer with the LLM (default: template)")
    args = ap.parse_args(argv)

    if args.calibrate:
        report = calibrate_replays(args.calibrate, args.sim)
        dump_record(args.calibrate / CALIBRATION_FILE, report)
        print(render(report))
        return 0 if report["ok"] else 1

    if args.run_dir is None or not (args.fault or (args.node and args.result)):
        ap.error("give a run dir and either --fault NAME or --node ID --result FILE (or --calibrate BATCH_DIR)")
    task = load_record(args.run_dir / "task.json")
    registry = load_pack(task["pack"])
    if args.fault:
        fault = registry.replay_faults.get(args.fault)
        if fault is None:
            ap.error(f"unknown fault {args.fault!r}; the pack has {sorted(registry.replay_faults)}")
        node = next((n for n in load_record(args.run_dir / GRAPH_FILE)["nodes"] if n["id"] == fault.node), None)
        changed = fault.apply(NodeResult.from_dict(node["result"])) if node and node.get("result") else None
        if changed is None:
            print(f"fault {args.fault!r} does not apply to this run (node {fault.node!r})")
            return 1
        update, label, target = {fault.node: changed}, args.fault, fault.target
    else:
        update, label, target = {args.node: NodeResult.from_dict(load_record(args.result))}, f"{args.node}-edited", None
    out = args.out or args.run_dir.parent / f"{args.run_dir.name}--{label}"
    gateway = None
    if args.llm:
        from agentkit.llm import LLMGateway

        gateway = LLMGateway()
    tenant = load_record(args.run_dir / "taskrun.json").get("tenant") or ""
    with platform_for(tenant, registry, args.sim) as client:
        res = replay_run(args.run_dir, update, out, client=client, registry=registry, gateway=gateway, label=label)
    print(f"[{out.name}] ended={res['ended']} status={res['status']} (original: {res['original_status']})")
    failed = _failed_checks(res["score"])
    print(f"failed checks: {', '.join(failed) or 'none'}")
    if target:
        print(f"target check {target}: {'FAILED as expected' if target in failed else 'did NOT fail'}")
        return 0 if target in failed else 1
    return 0 if res["ended"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
