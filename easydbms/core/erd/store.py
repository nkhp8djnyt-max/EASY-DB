"""Positions of the cards a user dragged, kept in ``app.db`` per connection and schema scope.

Only moved cards are stored: everything else is placed by the layout, so a table added to the
database later simply appears where the layout puts it, and the cards the user arranged stay put.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..schema import TableKey
from ..storage import AppDatabase

#: Scope used when the diagram shows every schema at once.
ALL_SCHEMAS = "*"


class ErdLayoutStore:
    def __init__(self, db: AppDatabase) -> None:
        self._db = db

    def load(self, connection_id: str, scope: str) -> dict[TableKey, tuple[float, float]]:
        rows = self._db.execute(
            "SELECT schema, name, x, y FROM erd_positions WHERE connection_id = ? AND scope = ?",
            (connection_id, scope),
        )
        return {TableKey(schema, name): (float(x), float(y)) for schema, name, x, y in rows}

    def save(
        self, connection_id: str, scope: str, positions: Mapping[TableKey, tuple[float, float]]
    ) -> None:
        """Insert or update ``positions`` in one transaction."""
        self._db.run_atomically(
            [
                (
                    "INSERT INTO erd_positions (connection_id, scope, schema, name, x, y) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(connection_id, scope, schema, name) "
                    "DO UPDATE SET x = excluded.x, y = excluded.y",
                    (connection_id, scope, key.schema, key.name, x, y),
                )
                for key, (x, y) in positions.items()
            ]
        )

    def discard(self, connection_id: str, scope: str, keys: Iterable[TableKey]) -> None:
        self._db.run_atomically(
            [
                (
                    "DELETE FROM erd_positions "
                    "WHERE connection_id = ? AND scope = ? AND schema = ? AND name = ?",
                    (connection_id, scope, key.schema, key.name),
                )
                for key in keys
            ]
        )

    def clear(self, connection_id: str, scope: str) -> None:
        """Forget every moved card of one scope ("reset layout")."""
        self._db.execute(
            "DELETE FROM erd_positions WHERE connection_id = ? AND scope = ?",
            (connection_id, scope),
        )

    def forget(self, connection_id: str) -> None:
        """Drop everything stored for a deleted connection."""
        self._db.execute("DELETE FROM erd_positions WHERE connection_id = ?", (connection_id,))
