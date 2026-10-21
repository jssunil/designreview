"""python -m agentkit.service --http 127.0.0.1:8780 [--sim DIR] [--skip-llm] [--pack P] [--runs-dir D]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from agentkit.service.core import AgentService


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Serve the agent: one question in, one audited answer out (dry run).")
    ap.add_argument("--http", default="127.0.0.1:8780", metavar="HOST:PORT",
                    help="default 127.0.0.1:8780; another host needs AGENTKIT_SERVICE_TOKEN")
    ap.add_argument("--pack", default=None, help="default: config/project.toml default_pack")
    ap.add_argument("--runs-dir", type=Path, default=None)
    ap.add_argument("--sim", type=Path, default=None, help="replay captured fixtures (a file or a folder of <tenant>.json)")
    ap.add_argument("--skip-llm", action="store_true", help="template answers only")
    ap.add_argument("--max-in-flight", type=int, default=4)
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args(argv)

    from agentkit.service.http import make_server

    host, _, port = args.http.rpartition(":")
    service = AgentService(args.pack, args.runs_dir, sim=args.sim, skip_llm=args.skip_llm,
                           max_in_flight=args.max_in_flight)
    try:
        server = make_server(service, host or "127.0.0.1", int(port), verbose=args.verbose)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Agent service on http://{server.server_address[0]}:{server.server_address[1]}/  "
          f"(pack {service.pack}, {'sim' if args.sim else 'live'}, llm {'off' if args.skip_llm else 'on'})  "
          "Ctrl+C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
