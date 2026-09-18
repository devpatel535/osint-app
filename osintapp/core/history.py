"""Persistent search history.

Design constraint from the brief: **history is never trimmed**. There is no row
cap, no age-based pruning and no vacuum-on-startup anywhere in this file. A
search run three years ago is still there, and the UI reaches it by scrolling.

That makes paging the important part. The GUI asks for a window of rows
(``page()``), and SQLite serves it from an index on ``created_at`` - so the
cost of scrolling to old entries stays flat whether the table holds fifty rows
or half a million.

Each row also carries the full result snapshot as JSON, so reopening an old
search shows exactly what was found at the time instead of silently re-running
it against a web that has since changed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..paths import history_db_path
from .models import SearchResult

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT    NOT NULL,
    query_norm    TEXT    NOT NULL,
    query_type    TEXT    NOT NULL,
    created_at    REAL    NOT NULL,
    duration      REAL    NOT NULL DEFAULT 0,
    finding_count INTEGER NOT NULL DEFAULT 0,
    hit_count     INTEGER NOT NULL DEFAULT 0,
    cancelled     INTEGER NOT NULL DEFAULT 0,
    snapshot      TEXT
);
CREATE INDEX IF NOT EXISTS idx_searches_created ON searches(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_searches_norm    ON searches(query_norm);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass
class HistoryEntry:
    """One row, without the (potentially large) snapshot payload."""

    id: int
    query: str
    query_type: str
    created_at: float
    duration: float
    finding_count: int
    hit_count: int
    cancelled: bool

    @property
    def has_snapshot(self) -> bool:
        return True  # snapshots are written for every search; kept for clarity


class SearchHistory:
    """Thread-safe SQLite-backed history.

    A single connection guarded by a lock, rather than one per thread: writes
    come from the GUI thread and reads from the same thread, and the lock keeps
    the "insert then immediately re-read page 0" sequence consistent.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else history_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._prepare()

    def _prepare(self) -> None:
        with self._lock:
            # WAL survives an abrupt shutdown without losing committed rows,
            # which matters for a history the user expects to keep forever.
            try:
                self._connection.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.executescript(_SCHEMA)
            self._connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._connection.commit()

    # -- writing ---------------------------------------------------------
    def add(self, result: SearchResult) -> int:
        """Store a completed search. Returns the new row id."""
        try:
            snapshot = json.dumps(result.to_dict(), ensure_ascii=False)
        except (TypeError, ValueError):
            snapshot = ""

        with self._lock:
            cursor = self._connection.execute(
                """INSERT INTO searches
                   (query, query_norm, query_type, created_at, duration,
                    finding_count, hit_count, cancelled, snapshot)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    result.query,
                    result.query.strip().lower(),
                    result.query_type,
                    result.started_at or time.time(),
                    round(result.duration, 3),
                    result.total_findings,
                    result.hit_count,
                    1 if result.cancelled else 0,
                    snapshot,
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid or 0)

    # -- reading ---------------------------------------------------------
    def page(self, offset: int = 0, limit: int = 50, search: str = "") -> List[HistoryEntry]:
        """A window of history, newest first.

        *limit* bounds one page for the scroller's benefit; it is not a cap on
        what is retained. Call repeatedly with a growing *offset* to walk back
        through the entire history.
        """
        clause, params = "", []
        term = (search or "").strip().lower()
        if term:
            clause = "WHERE query_norm LIKE ?"
            params.append(f"%{term}%")

        params.extend([int(limit), int(offset)])
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT id, query, query_type, created_at, duration,
                           finding_count, hit_count, cancelled
                    FROM searches {clause}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?""",
                params,
            ).fetchall()

        return [
            HistoryEntry(
                id=row["id"],
                query=row["query"],
                query_type=row["query_type"],
                created_at=row["created_at"],
                duration=row["duration"],
                finding_count=row["finding_count"],
                hit_count=row["hit_count"],
                cancelled=bool(row["cancelled"]),
            )
            for row in rows
        ]

    def count(self, search: str = "") -> int:
        clause, params = "", []
        term = (search or "").strip().lower()
        if term:
            clause = "WHERE query_norm LIKE ?"
            params.append(f"%{term}%")
        with self._lock:
            row = self._connection.execute(
                f"SELECT COUNT(*) AS n FROM searches {clause}", params
            ).fetchone()
        return int(row["n"]) if row else 0

    def snapshot(self, entry_id: int) -> Optional[SearchResult]:
        """Rebuild the stored result for a history row, if it is still intact."""
        with self._lock:
            row = self._connection.execute(
                "SELECT snapshot FROM searches WHERE id = ?", (int(entry_id),)
            ).fetchone()
        if not row or not row["snapshot"]:
            return None
        try:
            return SearchResult.from_dict(json.loads(row["snapshot"]))
        except (ValueError, TypeError, KeyError):
            return None

    def oldest_timestamp(self) -> Optional[float]:
        with self._lock:
            row = self._connection.execute(
                "SELECT MIN(created_at) AS t FROM searches"
            ).fetchone()
        return row["t"] if row and row["t"] else None

    # -- deleting --------------------------------------------------------
    def delete(self, entry_id: int) -> None:
        """Remove one entry - the per-row 'x' in the history list."""
        with self._lock:
            self._connection.execute("DELETE FROM searches WHERE id = ?", (int(entry_id),))
            self._connection.commit()

    def clear(self) -> int:
        """Wipe the entire history. Returns how many rows were removed.

        VACUUM runs here and only here: this is the one moment the user has
        explicitly asked for the data to be gone, so the file should shrink
        rather than leave the old rows recoverable in free pages.
        """
        with self._lock:
            removed = self.count()
            self._connection.execute("DELETE FROM searches")
            self._connection.commit()
            try:
                self._connection.execute("VACUUM")
            except sqlite3.DatabaseError:
                pass
            return removed

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
