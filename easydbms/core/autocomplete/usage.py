"""How often each suggestion was accepted, per connection (stored in ``app.db``)."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

from ..storage import AppDatabase


class UsageStore:
    """Counts accepted suggestions. Reads come from memory, so ranking never waits for SQLite."""

    def __init__(self, db: AppDatabase) -> None:
        self._db = db
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, int]] = {}

    def counts(self, connection_id: str) -> Mapping[str, int]:
        with self._lock:
            cached = self._cache.get(connection_id)
            if cached is not None:
                return cached
        rows = self._db.execute(
            "SELECT key, uses FROM completion_usage WHERE connection_id = ?", (connection_id,)
        )
        loaded = {str(key): int(uses) for key, uses in rows}
        with self._lock:
            return self._cache.setdefault(connection_id, loaded)

    def record(self, connection_id: str, key: str) -> None:
        """One more acceptance of ``key`` (e.g. ``column:public.orders.status``)."""
        if not key:
            return
        self.counts(connection_id)  # make sure the counts of this connection are loaded
        with self._lock:
            bucket = self._cache[connection_id]
            bucket[key] = bucket.get(key, 0) + 1
        self._db.execute(  # counted by SQLite itself, so concurrent writers cannot overwrite
            "INSERT INTO completion_usage (connection_id, key, uses, last_used) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(connection_id, key) DO UPDATE SET uses = uses + 1, "
            "last_used = excluded.last_used",
            (connection_id, key, time.time()),
        )

    def forget(self, connection_id: str) -> None:
        with self._lock:
            self._cache.pop(connection_id, None)
        self._db.execute("DELETE FROM completion_usage WHERE connection_id = ?", (connection_id,))

    def prune(self, keep: set[str]) -> None:
        """Drop the counts of connections that no longer exist."""
        with self._lock:
            for connection_id in [i for i in self._cache if i not in keep]:
                del self._cache[connection_id]
        rows = self._db.execute("SELECT DISTINCT connection_id FROM completion_usage")
        for (connection_id,) in rows:
            if connection_id not in keep:
                self._db.execute(
                    "DELETE FROM completion_usage WHERE connection_id = ?", (connection_id,)
                )
