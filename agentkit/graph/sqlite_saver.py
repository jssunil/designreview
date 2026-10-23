"""
SqliteSaver: checkpoint history in one SQLite file, for many runs and many processes.

The same CheckpointSaver protocol as MemorySaver and JsonlSaver (put, get, list(before, limit,
filter)), plus a thread id: the run the checkpoints belong to. One file can hold the history of every
run of a service; another process can open the same file and list, inspect or fork any run.

    base = SqliteSaver(path)                 # all runs: get/list see every thread
    saver = base.for_thread(run_id)          # one run: put/get/list stay in that thread
    SqliteSaver(path).threads()              # [{thread_id, checkpoints, first_ts, last_ts}]

Without a thread id, put() files a checkpoint under its metadata "run_id" (GraphStore.snapshot sets
it). SQLite runs in WAL mode with a busy timeout, so readers never block the writer and two processes
can write the same file. Standard library only (sqlite3).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentkit.graph.checkpoint import Checkpoint, _matches

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    id        TEXT NOT NULL,
    parent_id TEXT,
    ts        TEXT NOT NULL,
    metadata  TEXT NOT NULL,
    graph     TEXT NOT NULL,
    PRIMARY KEY (thread_id, id)
);
CREATE INDEX IF NOT EXISTS checkpoints_by_id ON checkpoints (id);
"""
NO_THREAD = ""


class _Db:
    """One connection shared by the savers of one file in this process (a lock serialises it)."""

    def __init__(self, path: Path, timeout_s: float):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=timeout_s, check_same_thread=False,
                                    isolation_level=None)  # autocommit; each put is one statement
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
            self.conn.executescript(SCHEMA)


class SqliteSaver:
    def __init__(self, path: Path, thread_id: Optional[str] = None, *, timeout_s: float = 10.0,
                 _db: Optional[_Db] = None):
        self._db = _db or _Db(path, timeout_s)
        self.path = self._db.path
        self.thread_id = thread_id

    def for_thread(self, thread_id: str) -> "SqliteSaver":
        """A saver for one run on the same file and connection."""
        if not thread_id:
            raise ValueError("thread_id must not be empty")
        return SqliteSaver(self.path, thread_id, _db=self._db)

    def close(self) -> None:
        with self._db.lock:
            self._db.conn.close()

    # ---- the CheckpointSaver protocol ---------------------------------------------

    def put(self, checkpoint: Checkpoint) -> None:
        thread = self.thread_id or str(checkpoint.metadata.get("run_id") or NO_THREAD)
        row = (thread, checkpoint.id, checkpoint.parent_id, checkpoint.ts,
               json.dumps(checkpoint.metadata, default=str, ensure_ascii=False),
               json.dumps(checkpoint.graph, default=str, ensure_ascii=False))
        with self._db.lock:
            self._db.conn.execute("INSERT OR REPLACE INTO checkpoints (thread_id, id, parent_id, ts, metadata, graph) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", row)

    def get(self, checkpoint_id: Optional[str] = None) -> Optional[Checkpoint]:
        if checkpoint_id is not None:
            rows = self._select("id = ?", [checkpoint_id], limit=1)
        else:
            rows = self._select("", [], limit=1)
        return rows[0] if rows else None

    def list(self, *, before: Optional[str] = None, limit: Optional[int] = None,
             filter: Optional[Dict[str, Any]] = None) -> List[Checkpoint]:
        where, args = ("id < ?", [before]) if before is not None else ("", [])
        out = [cp for cp in self._select(where, args, limit=None if filter else limit) if _matches(cp, None, filter)]
        return out[:limit] if limit is not None else out

    # ---- across runs ------------------------------------------------------------

    def threads(self) -> List[Dict[str, Any]]:
        """Every run in the file, newest last checkpoint first."""
        with self._db.lock:
            rows = self._db.conn.execute(
                "SELECT thread_id, COUNT(*), MIN(ts), MAX(ts), MAX(id) FROM checkpoints GROUP BY thread_id "
                "ORDER BY MAX(id) DESC").fetchall()
        return [{"thread_id": t, "checkpoints": n, "first_ts": first, "last_ts": last} for t, n, first, last, _ in rows]

    def _select(self, where: str, args: List[Any], *, limit: Optional[int]) -> List[Checkpoint]:
        clauses, params = ([where] if where else []), list(args)
        if self.thread_id is not None:
            clauses.append("thread_id = ?")
            params.append(self.thread_id)
        sql = "SELECT id, parent_id, ts, metadata, graph FROM checkpoints"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._db.lock:
            rows = self._db.conn.execute(sql, params).fetchall()
        return [Checkpoint(id=i, parent_id=p, ts=ts, metadata=json.loads(m), graph=json.loads(g))
                for i, p, ts, m, g in rows]
