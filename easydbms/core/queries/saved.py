"""Queries the user chose to keep, optionally sorted into folders, in ``app.db``.

A saved query belongs either to one connection or (``connection_id == ""``) to all of them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..storage import AppDatabase

_COLUMNS = "id, name, folder, sql, dialect, connection_id, created_at, updated_at"


@dataclass(frozen=True, slots=True)
class SavedQuery:
    id: int
    name: str
    folder: str
    sql: str
    dialect: str
    #: ``""``: available on every connection.
    connection_id: str
    created_at: float
    updated_at: float


def _query(row: tuple[object, ...]) -> SavedQuery:
    (id_, name, folder, sql, dialect, connection_id, created, updated) = row
    return SavedQuery(
        int(str(id_)),
        str(name),
        str(folder),
        str(sql),
        str(dialect),
        str(connection_id),
        float(str(created)),
        float(str(updated)),
    )


def normalize_folder(folder: str) -> str:
    """``" Reports / Monthly "`` -> ``"Reports/Monthly"``; empty parts are dropped."""
    return "/".join(part.strip() for part in folder.replace("\\", "/").split("/") if part.strip())


class SavedQueryStore:
    def __init__(self, db: AppDatabase) -> None:
        self._db = db

    def add(
        self,
        name: str,
        sql: str,
        dialect: str,
        *,
        folder: str = "",
        connection_id: str = "",
        at: float | None = None,
    ) -> SavedQuery:
        title = name.strip()
        if not title:
            raise ValueError("a saved query needs a name")
        if not sql.strip():
            raise ValueError("there is nothing to save")
        now = time.time() if at is None else at
        self._db.execute(
            "INSERT INTO saved_queries (name, folder, sql, dialect, connection_id, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, normalize_folder(folder), sql, dialect, connection_id, now, now),
        )
        (row,) = self._db.execute(
            f"SELECT {_COLUMNS} FROM saved_queries WHERE id = last_insert_rowid()"
        )
        return _query(row)

    def get(self, query_id: int) -> SavedQuery | None:
        rows = self._db.execute(f"SELECT {_COLUMNS} FROM saved_queries WHERE id = ?", (query_id,))
        return _query(rows[0]) if rows else None

    def all(self, connection_id: str | None = None) -> list[SavedQuery]:
        """Folder by folder, then by name. With ``connection_id``: that connection's own queries
        and the ones for every connection."""
        if connection_id is None:
            rows = self._db.execute(f"SELECT {_COLUMNS} FROM saved_queries")
        else:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM saved_queries WHERE connection_id IN ('', ?)",
                (connection_id,),
            )
        found = [_query(row) for row in rows]
        return sorted(found, key=lambda q: (q.folder == "", q.folder.lower(), q.name.lower(), q.id))

    def folders(self, connection_id: str | None = None) -> list[str]:
        return sorted({q.folder for q in self.all(connection_id) if q.folder}, key=str.lower)

    def update(
        self,
        query_id: int,
        *,
        name: str | None = None,
        folder: str | None = None,
        sql: str | None = None,
        at: float | None = None,
    ) -> SavedQuery:
        current = self.get(query_id)
        if current is None:
            raise KeyError(query_id)
        title = current.name if name is None else name.strip()
        if not title:
            raise ValueError("a saved query needs a name")
        text = current.sql if sql is None else sql
        if not text.strip():
            raise ValueError("there is nothing to save")
        self._db.execute(
            "UPDATE saved_queries SET name = ?, folder = ?, sql = ?, updated_at = ? WHERE id = ?",
            (
                title,
                current.folder if folder is None else normalize_folder(folder),
                text,
                time.time() if at is None else at,
                query_id,
            ),
        )
        updated = self.get(query_id)
        assert updated is not None
        return updated

    def delete(self, query_id: int) -> None:
        self._db.execute("DELETE FROM saved_queries WHERE id = ?", (query_id,))

    def forget(self, connection_id: str) -> None:
        """Drop the queries that belonged to one deleted connection (shared ones stay)."""
        self._db.execute("DELETE FROM saved_queries WHERE connection_id = ?", (connection_id,))
