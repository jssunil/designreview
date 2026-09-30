"""
Run viewer: browse harness batches and runs in a browser. Read-only.

    python -m agentkit.viewer [--runs-dir runs] [--port 8765] [--host 127.0.0.1]

Serves a static page (index.html + app.js + style.css, no framework, no build)
and a small JSON API over the run folders the harness writes:

  GET /api/batches              every batch, newest first, with status counts
  GET /api/runs                 every run folder (batch or not), newest first
  GET /api/batches/<batch_id>   manifest, report.md, calibration, one row per run
  GET /api/runs/<run_id>        task, run record, score, graph, tool journal,
                                ground truth before/after (+ what changed), ledger, stdout

Safety: GET only (other methods get 405); ids must match a strict pattern and
resolve inside the runs folder (no path traversal); binds to 127.0.0.1 by
default; responses carry a strict Content-Security-Policy and nosniff. The
page inserts every value as text, never as HTML -- run data contains
platform text, which must not become markup.

`ViewerApp.handle(path)` holds all the logic and returns (status, content
type, body), so it is testable without a socket.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from agentkit.record.durable_io import read_jsonl

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
MAX_TEXT = 200_000  # cap on stdout / report text returned
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
RUN_FILES = {"task": "task.json", "taskrun": "taskrun.json", "score": "score.json", "graph": "graph.json",
             "before": "ground_truth_before.json", "after": "ground_truth_after.json", "ledger": "llm_ledger.json"}

Response = Tuple[int, str, bytes]


def _json(status: int, obj: Any) -> Response:
    # <, > and & are \u-escaped too: the JSON stays identical to a parser, and no
    # response body can ever contain a literal tag, whatever the run data holds.
    text = json.dumps(obj, default=str).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return status, "application/json; charset=utf-8", text.encode("utf-8")


def _error(status: int, message: str) -> Response:
    return _json(status, {"error": message})


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (ValueError, OSError):
        return {"_unreadable": path.name}


def _text(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n... (truncated)"


def ground_truth_changes(before: Any, after: Any) -> List[Dict[str, Any]]:
    """One row per observation key: did the platform's answer change during the run?"""
    b = (before or {}).get("observations") or {}
    a = (after or {}).get("observations") or {}
    rows = []
    for key in sorted(set(b) | set(a)):
        rows.append({"key": key, "changed": b.get(key) != a.get(key),
                     "before_error": (b.get(key) or {}).get("error_kind"),
                     "after_error": (a.get(key) or {}).get("error_kind")})
    return rows


class ViewerApp:
    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir).resolve()
        self.batches_dir = self.runs_dir / "batches"

    # ---- path safety -------------------------------------------------------

    def _child(self, parent: Path, ident: str) -> Optional[Path]:
        """parent/ident only if ident is a plain id and the result stays inside parent."""
        if not ID_RE.match(ident) or ".." in ident:
            return None
        path = (parent / ident).resolve()
        if path.parent != parent.resolve() or not path.is_dir():
            return None
        return path

    # ---- data ----------------------------------------------------------------

    def batches(self) -> List[Dict[str, Any]]:
        if not self.batches_dir.is_dir():
            return []
        out = []
        for d in sorted((p for p in self.batches_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
            manifest = _load(d / "manifest.json") or {}
            counts: Dict[str, int] = {}
            for entry in manifest.get("runs", []):
                score = _load(self.runs_dir / str(entry.get("run_id", "")) / "score.json") if ID_RE.match(
                    str(entry.get("run_id", ""))) else None
                status = (score or {}).get("status") or ("not graded" if entry.get("status") == "ran" else entry.get("status"))
                counts[status] = counts.get(status, 0) + 1
            calibration = _load(d / "calibration.json")
            out.append({"batch_id": d.name, "started_at": manifest.get("started_at"),
                        "finished_at": manifest.get("finished_at"), "skip_llm": manifest.get("skip_llm"),
                        "write": manifest.get("write"), "runs": len(manifest.get("runs", [])), "status_counts": counts,
                        "calibration_ok": None if calibration is None else bool(calibration.get("ok"))})
        return out

    def runs(self) -> List[Dict[str, Any]]:
        """Every run folder (including ones made outside a batch, e.g. capstone_agent.py --task), newest first."""
        if not self.runs_dir.is_dir():
            return []
        out = []
        for d in sorted((p for p in self.runs_dir.iterdir() if p.is_dir() and p.name != "batches"
                         and ID_RE.match(p.name) and (p / "taskrun.json").exists()), key=lambda p: p.name, reverse=True):
            taskrun = _load(d / "taskrun.json") or {}
            score = _load(d / "score.json") or {}
            out.append({"run_id": d.name, "task_id": taskrun.get("task_id"), "tenant": taskrun.get("tenant"),
                        "ended": taskrun.get("ended"), "started_at": taskrun.get("started_at"),
                        "score_status": score.get("status")})
        return out

    def batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        d = self._child(self.batches_dir, batch_id)
        if d is None:
            return None
        manifest = _load(d / "manifest.json") or {}
        rows = []
        for entry in manifest.get("runs", []):
            run_id = str(entry.get("run_id", ""))
            score = _load(self.runs_dir / run_id / "score.json") if ID_RE.match(run_id) else None
            failing = [c for c in (score or {}).get("checks", []) if c.get("status") not in ("pass", "skip")]
            rows.append({**entry, "score_status": (score or {}).get("status"), "counts": (score or {}).get("counts"),
                         "failing": failing})
        return {"batch_id": d.name, "manifest": manifest, "runs": rows, "report": _text(d / "report.md"),
                "calibration": _load(d / "calibration.json")}

    def run(self, run_id: str) -> Optional[Dict[str, Any]]:
        d = self._child(self.runs_dir, run_id)
        if d is None or d.resolve() == self.batches_dir.resolve():
            return None
        data: Dict[str, Any] = {name: _load(d / fname) for name, fname in RUN_FILES.items()}
        data["run_id"] = d.name
        data["journal"] = read_jsonl(d / "tool_journal.jsonl")
        data["stdout"] = _text(d / "stdout.txt")
        data["ground_truth_changes"] = ground_truth_changes(data["before"], data["after"])
        return data

    # ---- routing ---------------------------------------------------------------

    def handle(self, raw_path: str) -> Response:
        path = unquote(urlsplit(raw_path).path)
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/batches":
            return _json(200, self.batches())
        if path == "/api/runs":
            return _json(200, self.runs())
        m =re.match(r"^/api/(batches|runs)/([^/]+)$", path)
        if m:
            kind, ident = m.groups()
            data = self.batch(ident) if kind == "batches" else self.run(ident)
            return _json(200, data) if data is not None else _error(404, f"no such {kind[:-1]}: {ident[:80]}")
        return _error(404, "not found")

    def _static(self, name: str) -> Response:
        if name not in STATIC_FILES:  # a fixed whitelist: nothing else under static/ or elsewhere is served
            return _error(404, "not found")
        return 200, STATIC_FILES[name], (STATIC_DIR / name).read_bytes()


def make_handler(app: ViewerApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentkit-viewer"

        def _send(self, status: int, ctype: str, body: bytes, head_only: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            if status == 405:
                self.send_header("Allow", "GET, HEAD")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._send(*app.handle(self.path))

        def do_HEAD(self) -> None:  # noqa: N802
            self._send(*app.handle(self.path), head_only=True)

        def _read_only(self) -> None:
            self._send(*_error(405, "the viewer is read-only"))

        do_POST = do_PUT = do_PATCH = do_DELETE = _read_only  # noqa: N815

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

    return Handler


def make_server(runs_dir: Path, host: str = "127.0.0.1", port: int = 8765, verbose: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(ViewerApp(runs_dir)))
    server.verbose = verbose  # type: ignore[attr-defined]
    return server


def main(argv: Optional[List[str]] = None) -> int:
    from agentkit.harness.batch import RUNS_DIR

    ap = argparse.ArgumentParser(description="Browse harness batches and runs (read-only).")
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (this machine only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: serving run data on {args.host} -- anyone who can reach it can read it")
    server = make_server(args.runs_dir, args.host, args.port, args.verbose)
    print(f"Run viewer on http://{args.host}:{server.server_address[1]}/  (runs: {args.runs_dir})  Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
