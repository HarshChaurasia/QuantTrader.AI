"""Content-hash deduplication with SQLite TTL.

Same article fetched from two sources (e.g. Reuters RSS + Yahoo News re-syndication)
collapses to one event. Cache survives process restarts so we don't re-emit on
startup.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from ea.config import REPO_ROOT
from ea.logging import logger

DEFAULT_PATH = REPO_ROOT / "data" / "news_dedupe.sqlite"
DEFAULT_TTL_HOURS = 72


class DedupeCache:
    """SQLite-backed seen-id cache. Thread-safe via a lock."""

    def __init__(self, path: Path | str | None = None, ttl_hours: int = DEFAULT_TTL_HOURS):
        self.path = Path(path) if path else DEFAULT_PATH
        self.ttl = timedelta(hours=ttl_hours)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS seen (
                    id TEXT PRIMARY KEY,
                    first_seen_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def seen(self, item_id: str) -> bool:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT 1 FROM seen WHERE id = ?", (item_id,)).fetchone()
        return row is not None

    def mark(self, item_id: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR IGNORE INTO seen(id) VALUES (?)", (item_id,))

    def filter_new(self, ids: list[str]) -> list[str]:
        """Return only ids not yet seen; mark them as seen."""
        if not ids:
            return []
        new_ids: list[str] = []
        with self._lock, self._connect() as c:
            for item_id in ids:
                row = c.execute("SELECT 1 FROM seen WHERE id = ?", (item_id,)).fetchone()
                if row is None:
                    new_ids.append(item_id)
                    c.execute("INSERT OR IGNORE INTO seen(id) VALUES (?)", (item_id,))
        return new_ids

    def purge_expired(self) -> int:
        cutoff = (datetime.now(timezone.utc) - self.ttl).isoformat()
        with self._lock, self._connect() as c:
            cur = c.execute("DELETE FROM seen WHERE first_seen_ts < ?", (cutoff,))
            n = cur.rowcount
        if n:
            logger.debug("dedupe: purged {} expired entries", n)
        return n

    def size(self) -> int:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT COUNT(*) FROM seen").fetchone()
        return int(row[0]) if row else 0
