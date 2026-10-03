"""SQLite client on the standard library driver."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ...connections import MEMORY_DATABASE, FileConnection
from ..base import DatabaseClient, RawResult
from ..errors import (
    ConnectionFailed,
    DatabaseFileError,
    DbError,
    QueryCancelled,
    QueryError,
    ReadOnlyViolation,
)


class SqliteClient(DatabaseClient):
    _login_step_name = "Open"

    def __init__(self, config: FileConnection, password: str | None = None) -> None:
        super().__init__(config, password)
        self._file_config = config

    @property
    def _resolved_path(self) -> Path | None:
        if self._file_config.path == MEMORY_DATABASE:
            return None
        return Path(self._file_config.path).expanduser().resolve()

    def _check_file(self) -> str:
        path = self._resolved_path
        if path is None:
            return MEMORY_DATABASE
        if path.is_dir():
            raise DatabaseFileError(f"'{path}' is a directory, not a database file")
        if not path.exists():
            if self._file_config.create_if_missing and not self._file_config.read_only:
                if not path.parent.is_dir():
                    raise DatabaseFileError(f"the folder '{path.parent}' does not exist")
                return str(path)
            raise DatabaseFileError(f"the file '{path}' does not exist")
        return f"{path} · {_human_size(path.stat().st_size)}"

    def _network_steps(self) -> list[tuple[str, Callable[[], str]]]:
        return [("File", self._check_file)]

    def _open(self) -> Any:
        self._check_file()
        path = self._resolved_path
        if path is None:
            raw = sqlite3.connect(MEMORY_DATABASE, isolation_level=None, check_same_thread=False)
        elif self._file_config.read_only:
            raw = sqlite3.connect(
                f"file:{quote(path.as_posix())}?mode=ro",
                uri=True,
                isolation_level=None,
                check_same_thread=False,
            )
        else:
            raw = sqlite3.connect(
                str(path), isolation_level=None, check_same_thread=False, timeout=5.0
            )
        try:
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute("SELECT count(*) FROM sqlite_master").fetchall()  # reads the header
        except sqlite3.Error:
            raw.close()
            raise
        return raw

    def _close(self, raw: Any) -> None:
        raw.close()

    def _server_version(self, raw: Any) -> str:
        return str(sqlite3.sqlite_version)

    def _cancel(self, raw: Any) -> None:
        raw.interrupt()

    def _run(self, raw: Any, sql: str, max_rows: int | None) -> RawResult:
        cursor = raw.execute(sql)
        try:
            if cursor.description is None:
                affected = cursor.rowcount
                return (), [], affected if affected >= 0 else None, False
            columns = tuple(column[0] for column in cursor.description)
            if max_rows is None:
                return columns, cursor.fetchall(), None, False
            rows = cursor.fetchmany(max_rows + 1)
            return columns, rows[:max_rows], None, len(rows) > max_rows
        finally:
            cursor.close()

    # ------------------------------------------------------------------ errors

    def _translate_connect_error(self, error: Exception) -> ConnectionFailed:
        message = str(error)
        if isinstance(error, sqlite3.DatabaseError) and "not a database" in message:
            return DatabaseFileError("the file is not a SQLite database")
        if "unable to open" in message:
            return DatabaseFileError(f"cannot open the database file: {message}")
        return ConnectionFailed(message)

    def _translate_query_error(self, raw: Any, error: Exception) -> DbError:
        message = str(error)
        if isinstance(error, sqlite3.OperationalError):
            if message == "interrupted":
                return QueryCancelled(message)
            if "readonly database" in message:
                return ReadOnlyViolation(message)
        code = getattr(error, "sqlite_errorname", None)
        return QueryError(message, code=code)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # unreachable; keeps the type checker content
