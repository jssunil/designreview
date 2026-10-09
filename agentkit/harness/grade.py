"""
Grade saved runs. Files only -- no network, no agent, no LLM.

    python -m agentkit.harness.grade runs/batches/<batch_id>
    python -m agentkit.harness.grade runs/<run_id> [runs/<run_id> ...]

Each run gets runs/<run_id>/score.json; a batch also gets report.md beside
its manifest. Re-grading an old batch uses the current checks against the
same saved evidence. Task status: fail if any check failed or errored;
drift if the only mismatches are data that moved during the run; else pass.
Exit code 1 if any task failed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agentkit.harness.checks import CheckOutcome, RunBundle, check_run
from agentkit.config import default_pack
from agentkit.record.durable_io import dump_record, durable_write_text, load_record
from agentkit.registry import Registry, load_pack

SCORER_VERSION = "agentkit-grade-v1"


def overall_status(checks: List[CheckOutcome]) -> str:
    statuses = {c.status for c in checks}
    if statuses & {"fail", "error"}:
        return "fail"
    if "drift" in statuses:
        return "drift"
    return "pass"


def grade_run(run_dir: Path, registry: Optional[Registry] = None) -> Dict:
    run_dir = Path(run_dir)
    task = load_record(run_dir / "task.json") if (run_dir / "task.json").exists() else {}
    registry = registry or load_pack(task.get("pack") or default_pack())
    bundle = RunBundle.load(run_dir, read_only_tools=registry.read_only,
                            write_tools=registry.extras.get("write_tools", ()))
    checks = check_run(bundle, registry)
    score = {
        "scorer": SCORER_VERSION,
        "run_id": bundle.taskrun.get("run_id") or run_dir.name,
        "task_id": task.get("id"),
        "tenant": bundle.taskrun.get("tenant"),
        "status": overall_status(checks),
        "counts": dict(Counter(c.status for c in checks)),
        "checks": [c.to_dict() for c in checks],
    }
    dump_record(run_dir / "score.json", score)
    return score


def render_batch_report(batch_dir: Path, manifest: Dict, results: List[Tuple[Dict, Optional[Dict]]]) -> Path:
    totals = Counter((s["status"] if s else e.get("status", "missing")) for e, s in results)
    lines = [
        f"# Harness batch {manifest.get('batch_id')}",
        "",
        f"- Mode: {'write' if manifest.get('write') else 'dry run'}; LLM {'on' if not manifest.get('skip_llm') else 'off (template answers)'}",
        f"- Graded {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} by {SCORER_VERSION}",
        "- Totals: " + ", ".join(f"{k} {v}" for k, v in sorted(totals.items())),
        "",
        "| Task | Tenant | Status | Checks |",
        "|---|---|---|---|",
    ]
    for entry, score in results:
        if score is None:
            lines.append(f"| `{entry.get('task_id')}` | {entry.get('tenant')} | {entry.get('status', 'missing')} | "
                         f"{entry.get('reason', '')} |")
        else:
            counts = ", ".join(f"{k} {v}" for k, v in sorted(score["counts"].items()))
            lines.append(f"| `{entry.get('task_id')}` | {entry.get('tenant')} | **{score['status']}** | {counts} |")
    problems = [(e, s) for e, s in results if s and s["status"] != "pass"]
    if problems:
        lines += ["", "## Checks that did not pass", ""]
        for entry, score in problems:
            lines += [f"### `{entry.get('task_id')}` / {entry.get('tenant')} ({score['status']})", ""]
            for c in score["checks"]:
                if c["status"] not in ("pass", "skip"):
                    lines.append(f"- **{c['status']}** `{c['check']}` / {c['name']}: {c['detail']}")
            lines.append("")
    path = batch_dir / "report.md"
    durable_write_text(path, "\n".join(lines) + "\n")
    return path


def grade_batch(batch_dir: Path) -> Tuple[Path, List[Tuple[Dict, Optional[Dict]]]]:
    manifest = load_record(batch_dir / "manifest.json")
    runs_root = batch_dir.parent.parent  # runs/batches/<id> -> runs/
    results: List[Tuple[Dict, Optional[Dict]]] = []
    for entry in manifest.get("runs", []):
        run_dir = runs_root / entry["run_id"]
        if entry.get("status") != "ran":
            results.append((entry, None))
        elif not (run_dir / "taskrun.json").exists():
            results.append(({**entry, "status": "fail", "reason": "no taskrun.json was written"}, None))
        else:
            results.append((entry, grade_run(run_dir)))
    return render_batch_report(batch_dir, manifest, results), results


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Grade saved harness runs (files only).")
    ap.add_argument("paths", nargs="+", type=Path, help="a batch dir, or run dirs")
    args = ap.parse_args(argv)
    failed = False
    for path in args.paths:
        if (path / "manifest.json").exists():
            report, results = grade_batch(path)
            for entry, score in results:
                status = score["status"] if score else entry.get("status", "missing")
                failed |= status == "fail"
                print(f"  {status:8} {entry.get('task_id')} [{entry.get('tenant')}]")
            print(f"[report: {report}]")
        elif (path / "taskrun.json").exists():
            score = grade_run(path)
            failed |= score["status"] == "fail"
            print(f"  {score['status']:8} {score['task_id']}  [{path / 'score.json'}]")
        else:
            print(f"  skipped  {path}: no manifest.json or taskrun.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
