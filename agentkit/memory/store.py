"""
Memory of earlier answers (experimental, off by default): "what changed since the last review?"

After a run whose finding was answered and whose answer passed the claim audit, the pack's facts of
that finding are kept, by (tenant, key, kind) -- for example the record the question was about. The
next run about the same record gets a section that compares the last kept facts with the new
finding. The platform is always read again: memory only adds that section, it never replaces a read,
and the section is part of the finding, so the claim audit checks what the answer says about it.

Generic part (this module): FindingMemory (SQLite; one file, many tenants) and MemorySpec, the two
pack functions:
  facts(finding)          -> (key, kind, facts) to keep, or None (nothing to keep)
  compare(previous, facts) -> the finding section; previous = {run_id, ts, facts}
The pack registers a MemorySpec as registry.extras["memory"].
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    tenant  TEXT NOT NULL,
    key     TEXT NOT NULL,
    kind    TEXT NOT NULL,
    run_id  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    facts   TEXT NOT NULL,
    PRIMARY KEY (tenant, key, kind, run_id)
);
"""
SECTION = "previous_review"  # the finding key the comparison goes under


@dataclass(frozen=True)
class MemorySpec:
    facts: Callable[[Dict[str, Any]], Optional[Tuple[str, str, Dict[str, Any]]]]
    compare: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


class FindingMemory:
    def __init__(self, path: Path, *, timeout_s: float = 10.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=timeout_s, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
            self._conn.executescript(SCHEMA)

    def remember(self, tenant: str, key: str, kind: str, run_id: str, facts: Dict[str, Any],
                 ts: Optional[str] = None) -> None:
        ts = ts or dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO memory (tenant, key, kind, run_id, ts, facts) VALUES (?, ?, ?, ?, ?, ?)",
                               (tenant, key, kind, run_id, ts, json.dumps(facts, default=str, ensure_ascii=False)))

    def history(self, tenant: str, key: str, kind: str, *, exclude_run: Optional[str] = None,
                limit: int = 10) -> List[Dict[str, Any]]:
        """Kept facts for one record of one tenant, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, ts, facts FROM memory WHERE tenant = ? AND key = ? AND kind = ? AND run_id != ? "
                "ORDER BY ts DESC LIMIT ?", (tenant, key, kind, exclude_run or "", int(limit))).fetchall()
        return [{"run_id": r, "ts": ts, "facts": json.loads(f)} for r, ts, f in rows]

    def last(self, tenant: str, key: str, kind: str, *, exclude_run: Optional[str] = None) -> Optional[Dict[str, Any]]:
        rows = self.history(tenant, key, kind, exclude_run=exclude_run, limit=1)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
