"""
Undo what write runs left on the platform, using the pack's own clean-up.

    python -m agentkit.harness.cleanup --tenant <tenant> [--pack packs.<name>] [--dry-run]
    python -m agentkit.harness.cleanup --run-dir runs/<run_id>

A batch started with --write already cleans up after each write run (after
the "after" ground truth is captured). This command is for --keep-writes
batches and for anything a crashed run left behind. The pack decides what
"ours" means -- agentkit only calls registry.extras["cleanup"](reader,
run_dir=..., tenant=..., dry_run=...) with the harness's own login.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from agentkit.config import default_pack, default_tenant, load_env
from agentkit.harness.ground_truth import GroundTruthReader
from agentkit.record import load_record
from agentkit.registry import load_pack


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Undo what write runs filed on the platform.")
    ap.add_argument("--pack", default=None, help="default: the run's pack, else config/project.toml default_pack")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--run-dir", type=Path, default=None, help="only what this run filed")
    ap.add_argument("--dry-run", action="store_true", help="list what would be undone, change nothing")
    args = ap.parse_args(argv)
    load_env()
    taskrun = load_record(args.run_dir / "taskrun.json") if args.run_dir else {}
    pack = args.pack or taskrun.get("pack") or default_pack()
    tenant = args.tenant or taskrun.get("tenant") or default_tenant()
    registry = load_pack(pack)
    cleanup = registry.extras.get("cleanup")
    if cleanup is None:
        print(f"{pack} registers no cleanup")
        return 1
    report = cleanup(GroundTruthReader.for_tenant(tenant), run_dir=args.run_dir, tenant=tenant, dry_run=args.dry_run)
    print(json.dumps(report, indent=1, default=str))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
