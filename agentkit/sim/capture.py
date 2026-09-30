"""
Capture a platform fixture for offline runs.

    python -m agentkit.sim.capture [--pack packs.<name>] [--tenant <tenant>] [--out <file.json>]

Runs every task of the pack that targets the tenant once against the LIVE tenant (template answers,
no LLM; always read-only) through recording clients, plus each task's
ground-truth probes, and saves every exchange to one fixture file. Replay
it with `python -m agentkit.harness.batch --sim <file.json> --grade`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from agentkit.config import default_pack, default_tenant
from agentkit.harness.tasks import load_tasks
from agentkit.registry import load_pack
from agentkit.sim.fixture import capture_fixture


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Record a platform fixture for offline (SimPlatform) runs.")
    ap.add_argument("--pack", default=None, help="default: config/project.toml default_pack")
    ap.add_argument("--tenant", default=None, help="default: config/project.toml default_tenant")
    ap.add_argument("--out", type=Path, default=None, help="default: <pack>/fixtures/<tenant>.json")
    args = ap.parse_args(argv)
    registry = load_pack(args.pack or default_pack())
    args.tenant = args.tenant or default_tenant()
    # only the tasks that run on this tenant: a fixture holds one tenant's platform
    tasks = [t for t in load_tasks([Path(registry.extras["tasks_dir"])]) if args.tenant in t.tenants]
    if not tasks:
        print(f"no task runs on tenant {args.tenant!r}; nothing to capture")
        return 1
    out = args.out or Path(registry.extras["fixtures_dir"]) / f"{args.tenant}.json"
    fixture = capture_fixture(tasks, args.tenant, note=f"{len(tasks)} task(s) of {args.pack or default_pack()}")
    fixture.save(out)
    by = {}
    for c in fixture.calls:
        by[c.channel] = by.get(c.channel, 0) + 1
    print(f"captured {len(fixture.calls)} exchange(s) {by} and {len(fixture.tools)} tools -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
