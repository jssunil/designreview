"""
Crash-safe file writes for run evidence.

A reader (or a crash mid-write) must never see half a JSON file: every write
goes to a sibling temp file, is fsync'd, then swapped in with os.replace.
The swap is retried briefly because on Windows OneDrive/antivirus can hold
the target open for a moment.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

REPLACE_ATTEMPTS = 5


def durable_write_text(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2 * (attempt + 1))
    return path  # pragma: no cover


def dump_record(path: Path, obj: Any) -> Path:
    return durable_write_text(path, json.dumps(obj, indent=2, default=str))


def load_record(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class JsonlAppender:
    """Append-only JSON-lines log. Each line is flushed and fsync'd so a
    crash loses at most the line being written. Thread-safe."""

    def __init__(self, path: Path):
        import threading

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, entry: dict) -> None:
        line = json.dumps(entry, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())


def read_jsonl(path: Path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"_unparseable": line[:200]})  # a torn last line from a crash
    return out
