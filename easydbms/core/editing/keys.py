"""The columns a user picked to identify the rows of a table that has no primary key."""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence

from ..schema import TableKey
from ..storage import AppDatabase


class EditKeyStore:
    """Remembered per connection and table in ``app.db``."""

    def __init__(self, db: AppDatabase) -> None:
        self._db = db

    def get(self, connection_id: str, table: TableKey) -> tuple[str, ...] | None:
        rows = self._db.execute(
            "SELECT columns FROM edit_keys WHERE connection_id = ? AND schema = ? AND name = ?",
            (connection_id, table.schema, table.name),
        )
        if not rows:
            return None
        try:
            columns = json.loads(rows[0][0])
        except ValueError:
            return None
        if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
            return None
        return tuple(columns) or None

    def set(self, connection_id: str, table: TableKey, columns: Sequence[str]) -> None:
        self._db.execute(
            "INSERT INTO edit_keys (connection_id, schema, name, columns) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(connection_id, schema, name) DO UPDATE SET columns = excluded.columns",
            (connection_id, table.schema, table.name, json.dumps(list(columns))),
        )

    def clear(self, connection_id: str, table: TableKey) -> None:
        self._db.execute(
            "DELETE FROM edit_keys WHERE connection_id = ? AND schema = ? AND name = ?",
            (connection_id, table.schema, table.name),
        )

    def forget(self, connection_id: str) -> None:
        """Drop everything stored for a connection that was deleted."""
        self._db.execute("DELETE FROM edit_keys WHERE connection_id = ?", (connection_id,))

    def prune(self, keep: Collection[str]) -> None:
        """Drop the choices of connections that no longer exist."""
        for (connection_id,) in self._db.execute("SELECT DISTINCT connection_id FROM edit_keys"):
            if connection_id not in keep:
                self.forget(connection_id)
