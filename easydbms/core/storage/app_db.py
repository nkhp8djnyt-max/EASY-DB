"""``app.db``: the application's own SQLite database, versioned with ``PRAGMA user_version``.

Each migration runs in its own transaction together with the version bump, so a crash can never
leave the schema half upgraded. A database written by a *newer* application is refused instead of
being modified blindly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


#: Append new migrations here; never edit or reorder released ones.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "key/value store for UI state (window geometry, splitter sizes, last connection)",
        ("CREATE TABLE ui_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",),
    ),
    Migration(
        2,
        "query editor tabs, restored per connection",
        (
            "CREATE TABLE query_tabs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " connection_id TEXT NOT NULL,"
            " position INTEGER NOT NULL,"
            " title TEXT NOT NULL,"
            " sql TEXT NOT NULL,"
            " dialect TEXT NOT NULL)",
            "CREATE INDEX query_tabs_connection ON query_tabs (connection_id, position)",
        ),
    ),
    Migration(
        3,
        "positions of table cards the user moved in the ERD, per connection and schema scope",
        (
            "CREATE TABLE erd_positions ("
            " connection_id TEXT NOT NULL,"
            " scope TEXT NOT NULL,"
            " schema TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " x REAL NOT NULL,"
            " y REAL NOT NULL,"
            " PRIMARY KEY (connection_id, scope, schema, name))",
        ),
    ),
    Migration(
        4,
        "how often each autocomplete suggestion was accepted, per connection",
        (
            "CREATE TABLE completion_usage ("
            " connection_id TEXT NOT NULL,"
            " key TEXT NOT NULL,"
            " uses INTEGER NOT NULL,"
            " last_used REAL NOT NULL,"
            " PRIMARY KEY (connection_id, key))",
        ),
    ),
    Migration(
        5,
        "columns the user chose to identify the rows of tables without a primary key",
        (
            "CREATE TABLE edit_keys ("
            " connection_id TEXT NOT NULL,"
            " schema TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " columns TEXT NOT NULL,"
            " PRIMARY KEY (connection_id, schema, name))",
        ),
    ),
)


class DatabaseTooNewError(RuntimeError):
    """``app.db`` was created by a newer version of the application."""


def validate_migrations(migrations: Sequence[Migration]) -> None:
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise ValueError(
                f"migrations must be numbered 1, 2, 3, ... without gaps; "
                f"found {migration.version} at position {expected}"
            )


class AppDatabase:
    def __init__(self, path: Path | str, migrations: Sequence[Migration] = MIGRATIONS) -> None:
        validate_migrations(migrations)
        self._lock = threading.RLock()
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate(migrations)
        except BaseException:
            self._conn.close()
            raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        """Run one statement with bound parameters; returns the rows (empty for commands)."""
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def run_atomically(self, statements: Sequence[tuple[str, Sequence[Any]]]) -> None:
        """Execute ``(sql, params)`` pairs in one transaction: all of them or none."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    self._conn.execute(sql, params)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # ------------------------------------------------------------------ ui_state

    def get_state(self, key: str, default: Any = None) -> Any:
        rows = self.execute("SELECT value FROM ui_state WHERE key = ?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0][0])
        except ValueError:
            return default

    def set_state(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO ui_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def delete_state(self, key: str) -> None:
        self.execute("DELETE FROM ui_state WHERE key = ?", (key,))

    # ------------------------------------------------------------------ internals

    def _migrate(self, migrations: Sequence[Migration]) -> None:
        with self._lock:
            current = self.schema_version
            latest = len(migrations)
            if current > latest:
                raise DatabaseTooNewError(
                    f"app.db has schema version {current}, this application understands up to "
                    f"{latest}; update the application"
                )
            for migration in migrations[current:]:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in migration.statements:
                        self._conn.execute(statement)
                    self._conn.execute(f"PRAGMA user_version = {int(migration.version)}")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("COMMIT")
