"""Query editor tabs, persisted per connection in ``app.db``."""

from __future__ import annotations

from dataclasses import dataclass

from ..storage import AppDatabase


@dataclass(frozen=True, slots=True)
class TabState:
    title: str
    sql: str
    dialect: str


class QueryTabStore:
    def __init__(self, db: AppDatabase) -> None:
        self._db = db

    def load(self, connection_id: str) -> list[TabState]:
        rows = self._db.execute(
            "SELECT title, sql, dialect FROM query_tabs WHERE connection_id = ? ORDER BY position",
            (connection_id,),
        )
        return [TabState(*row) for row in rows]

    def save(self, connection_id: str, tabs: list[TabState]) -> None:
        """Replace the saved tabs of one connection."""
        statements: list[tuple[str, tuple[object, ...]]] = [
            ("DELETE FROM query_tabs WHERE connection_id = ?", (connection_id,))
        ]
        statements += [
            (
                "INSERT INTO query_tabs (connection_id, position, title, sql, dialect) "
                "VALUES (?, ?, ?, ?, ?)",
                (connection_id, position, tab.title, tab.sql, tab.dialect),
            )
            for position, tab in enumerate(tabs)
        ]
        self._db.run_atomically(statements)

    def forget(self, connection_id: str) -> None:
        self._db.execute("DELETE FROM query_tabs WHERE connection_id = ?", (connection_id,))

    def active_index(self, connection_id: str) -> int:
        value = self._db.get_state(f"tabs/{connection_id}/active", 0)
        return value if isinstance(value, int) else 0

    def set_active_index(self, connection_id: str, index: int) -> None:
        self._db.set_state(f"tabs/{connection_id}/active", index)
