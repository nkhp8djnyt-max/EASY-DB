"""Every statement the user ran, newest first, kept in ``app.db``.

Running the same statement again right after itself does not add a line: the entry is refreshed
and its run counter goes up. Each connection keeps at most ``limit`` entries; the oldest go first.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum

from ..storage import AppDatabase

#: Entries kept per connection.
DEFAULT_LIMIT = 5000

_COLUMNS = "id, connection_id, sql, dialect, executed_at, duration, outcome, row_count, error, runs"


class HistoryOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    connection_id: str
    sql: str
    dialect: str
    #: Seconds since the epoch.
    executed_at: float
    duration: float
    outcome: HistoryOutcome
    #: Rows returned (or affected); ``None`` when the statement failed or has no count.
    row_count: int | None
    error: str
    #: How many times in a row this statement was run.
    runs: int

    @property
    def first_line(self) -> str:
        for line in self.sql.splitlines():
            if line.strip():
                return line.strip()
        return ""


def _entry(row: tuple[object, ...]) -> HistoryEntry:
    (id_, connection_id, sql, dialect, at, duration, outcome, rows, error, runs) = row
    return HistoryEntry(
        int(str(id_)),
        str(connection_id),
        str(sql),
        str(dialect),
        float(str(at)),
        float(str(duration)),
        HistoryOutcome(str(outcome)),
        None if rows is None else int(str(rows)),
        str(error),
        int(str(runs)),
    )


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class HistoryStore:
    def __init__(self, db: AppDatabase, limit: int = DEFAULT_LIMIT) -> None:
        if limit < 1:
            raise ValueError("the history must keep at least one entry")
        self._db = db
        self._limit = limit

    def record(
        self,
        connection_id: str,
        sql: str,
        dialect: str,
        *,
        duration: float,
        outcome: HistoryOutcome,
        row_count: int | None = None,
        error: str = "",
        at: float | None = None,
    ) -> None:
        """Remember one run; blank statements are ignored."""
        text = sql.strip()
        if not text:
            return
        when = time.time() if at is None else at
        last = self._db.execute(
            "SELECT id, sql FROM query_history WHERE connection_id = ? ORDER BY id DESC LIMIT 1",
            (connection_id,),
        )
        if last and last[0][1] == text:
            self._db.execute(
                "UPDATE query_history SET executed_at = ?, duration = ?, outcome = ?, "
                "row_count = ?, error = ?, dialect = ?, runs = runs + 1 WHERE id = ?",
                (when, duration, outcome.value, row_count, error, dialect, last[0][0]),
            )
            return
        self._db.run_atomically(
            [
                (
                    "INSERT INTO query_history (connection_id, sql, dialect, executed_at, "
                    "duration, outcome, row_count, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (connection_id, text, dialect, when, duration, outcome.value, row_count, error),
                ),
                (
                    "DELETE FROM query_history WHERE connection_id = ? AND id NOT IN "
                    "(SELECT id FROM query_history WHERE connection_id = ? "
                    "ORDER BY id DESC LIMIT ?)",
                    (connection_id, connection_id, self._limit),
                ),
            ]
        )

    def recent(
        self,
        connection_id: str | None = None,
        *,
        search: str = "",
        errors_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[HistoryEntry]:
        """Newest first; ``connection_id=None`` looks across all connections."""
        where: list[str] = []
        params: list[object] = []
        if connection_id is not None:
            where.append("connection_id = ?")
            params.append(connection_id)
        if search.strip():
            where.append("sql LIKE ? ESCAPE '\\'")
            params.append(_like(search.strip()))
        if errors_only:
            where.append("outcome = 'error'")
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM query_history{clause} "
            "ORDER BY executed_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [_entry(row) for row in rows]

    def count(self, connection_id: str | None = None) -> int:
        if connection_id is None:
            rows = self._db.execute("SELECT COUNT(*) FROM query_history")
        else:
            rows = self._db.execute(
                "SELECT COUNT(*) FROM query_history WHERE connection_id = ?", (connection_id,)
            )
        return int(rows[0][0])

    def delete(self, entry_id: int) -> None:
        self._db.execute("DELETE FROM query_history WHERE id = ?", (entry_id,))

    def clear(self, connection_id: str) -> None:
        self._db.execute("DELETE FROM query_history WHERE connection_id = ?", (connection_id,))

    forget = clear
