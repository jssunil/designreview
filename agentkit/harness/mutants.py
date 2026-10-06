"""
Verifier self-test ("calibration"): does the harness actually catch a
misbehaving agent?

Takes runs that PASS, breaks one known thing in an in-memory copy of each
(a mutant), re-runs every check, and requires the mutant's TARGET check to
fail. A mutant that still passes means the harness is broken, not the agent.
Nothing is written to the platform; nothing in the run folder is changed.

Reported:
  caught       target check failed on every run it applied to
  MISSED       it applied somewhere and the target check did not fail
  UNEXERCISED  a check that no mutant hit on any run (a check nobody has
               shown can fail) -- also a calibration failure

    python -m agentkit.harness.mutants runs/batches/<batch_id>   # or run dirs; default: newest batch
Exit code 1 on any MISSED or UNEXERCISED.

Built-in mutants cover the built-in checks; a pack registers the rest with
`@registry.mutant(name, target=<check name>)`.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentkit.harness.checks import BUILTIN_CHECKS, RunBundle, check_run, specs_for
from agentkit.harness.grade import overall_status
from agentkit.config import default_pack
from agentkit.record.durable_io import dump_record, load_record
from agentkit.registry import MutantDef, Registry, load_pack


# ---------------------------------------------------------------- built-in mutants

def _crashed(b: RunBundle) -> RunBundle:
    b.taskrun["ended"] = "running"  # the agent died after writing its first record
    return b


def _sneaky_write(b: RunBundle) -> Optional[RunBundle]:
    # A write no pack allowlists: forbidden in a dry run, and outside the write
    # allowlist in a write run -- tool_calls_policy must catch it either way.
    b.journal.append({"seq": 999, "tool": "Record.create", "args": {"title": "x"}, "ok": True})
    return b


def _no_answer(b: RunBundle) -> RunBundle:
    b.taskrun["claimed_answer"] = ""
    return b


BUILTIN_MUTANTS = [
    MutantDef("crashed_run", "run_completed", _crashed),
    MutantDef("sneaky_write", "tool_calls_policy", _sneaky_write),
    MutantDef("no_answer", "answer_present", _no_answer),
]


# ---------------------------------------------------------------- calibration

def _run_dirs(paths: List[Path]) -> List[Path]:
    dirs: List[Path] = []
    for p in paths:
        p = Path(p)
        if (p / "manifest.json").exists():
            runs_root = p.parent.parent
            dirs += [runs_root / e["run_id"] for e in load_record(p / "manifest.json").get("runs", [])
                     if e.get("status") == "ran"]
        elif (p / "taskrun.json").exists():
            dirs.append(p)
    return dirs


def calibrate(paths: List[Path], registry: Optional[Registry] = None) -> Dict[str, Any]:
    results: Dict[str, Dict[str, Any]] = {}
    exercised: Dict[str, bool] = {}
    skipped_runs: List[str] = []
    regs: Dict[str, Registry] = {}
    for run_dir in _run_dirs(paths):
        task = load_record(run_dir / "task.json") if (run_dir / "task.json").exists() else {}
        reg = registry or regs.setdefault(task.get("pack", ""), load_pack(task.get("pack") or default_pack()))
        base = RunBundle.load(run_dir, read_only_tools=reg.read_only, write_tools=reg.extras.get("write_tools", ()))
        if overall_status(check_run(base, reg)) != "pass":
            skipped_runs.append(run_dir.name)  # only passing runs can prove a check fails
            continue
        in_task = {s["name"] for s in specs_for(base.task)}
        for check in in_task:
            exercised.setdefault(check, False)
        for m in BUILTIN_MUTANTS + list(reg.mutants.values()):
            row = results.setdefault(m.name, {"target": m.target, "applied": 0, "caught": 0, "missed_on": []})
            if m.target not in in_task:
                continue
            mutated = m.apply(copy.deepcopy(base))
            if mutated is None:
                continue
            row["applied"] += 1
            hit = any(c.check == m.target and c.status == "fail" for c in check_run(mutated, reg))
            if hit:
                row["caught"] += 1
                exercised[m.target] = True
            else:
                row["missed_on"].append(run_dir.name)
    missed = sorted(n for n, r in results.items() if r["applied"] and r["caught"] < r["applied"])
    unexercised = sorted(c for c, hit in exercised.items() if not hit)
    return {"mutants": results, "missed": missed, "unexercised_checks": unexercised,
            "skipped_runs_not_passing": skipped_runs, "ok": not missed and not unexercised}


def render(report: Dict[str, Any]) -> str:
    lines = [f"{'mutant':34} {'target check':28} caught/applied"]
    for name, r in sorted(report["mutants"].items()):
        flag = "" if not r["applied"] else ("  MISSED" if r["caught"] < r["applied"] else "")
        lines.append(f"{name:34} {r['target']:28} {r['caught']}/{r['applied']}{flag}")
    lines.append("")
    lines.append(f"missed: {report['missed'] or 'none'}")
    lines.append(f"unexercised checks: {report['unexercised_checks'] or 'none'}")
    if report["skipped_runs_not_passing"]:
        lines.append(f"skipped (run did not pass, can't calibrate on it): {report['skipped_runs_not_passing']}")
    lines.append("CALIBRATION " + ("OK" if report["ok"] else "FAILED"))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    from agentkit.harness.batch import RUNS_DIR

    ap = argparse.ArgumentParser(description="Prove every check can fail: mutate passing runs, expect failures.")
    ap.add_argument("paths", nargs="*", type=Path, help="batch dir(s) or run dir(s); default: newest batch")
    args = ap.parse_args(argv)
    paths = args.paths or sorted((RUNS_DIR / "batches").glob("*"))[-1:]
    if not paths:
        print("no batches to calibrate on")
        return 1
    report = calibrate(paths)
    for p in paths:
        if (Path(p) / "manifest.json").exists():
            dump_record(Path(p) / "calibration.json", report)
    print(render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
