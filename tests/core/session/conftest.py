from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from easydbms.core.connections import FileConnection, ServerConnection
from easydbms.core.db import ConnectionFailed, DatabaseClient, DbError, QueryError
from easydbms.core.db.base import RawResult
from easydbms.core.session import ManagerEvent, Session, SessionState


class ScriptedClient(DatabaseClient):
    """A client whose connect() can be gated and made to fail, and that records its password."""

    created: list[ScriptedClient] = []  # noqa: RUF012 - shared test registry, reset per test

    def __init__(
        self, config: ServerConnection | FileConnection, password: str | None = None
    ) -> None:
        super().__init__(config, password)
        self.password = password
        self.gate = threading.Event()
        self.gate.set()
        self.fail_with: ConnectionFailed | None = None
        self.closed = False
        ScriptedClient.created.append(self)

    def _open(self) -> Any:
        self.gate.wait(timeout=10)
        if self.fail_with is not None:
            raise self.fail_with
        return object()

    def _close(self, raw: Any) -> None:
        self.closed = True

    def _server_version(self, raw: Any) -> str:
        return "16.0"

    def _run(self, raw: Any, sql: str, max_rows: int | None) -> RawResult:
        return (), [], None, False

    def _cancel(self, raw: Any) -> None: ...

    def _translate_connect_error(self, error: Exception) -> ConnectionFailed:
        return ConnectionFailed(str(error))

    def _translate_query_error(self, raw: Any, error: Exception) -> DbError:
        return QueryError(str(error))


@pytest.fixture(autouse=True)
def _reset_scripted_clients() -> None:
    ScriptedClient.created = []


class Events:
    """Thread-safe recorder usable as a manager/session listener."""

    def __init__(self) -> None:
        self.items: list[Any] = []
        self._lock = threading.Lock()

    def __call__(self, event: ManagerEvent) -> None:
        with self._lock:
            self.items.append(event)

    def states(self) -> list[str]:
        with self._lock:
            return [str(e.state.value) for e in self.items if hasattr(e, "state")]


def sqlite_file(tmp_path: Path, name: str = "db.sqlite") -> Path:
    path = tmp_path / name
    sqlite3.connect(path).close()
    return path


def file_config(path: Path | str, name: str = "file", **extra: Any) -> FileConnection:
    return FileConnection.model_validate({"name": name, "path": str(path), **extra})


def server_config(name: str = "srv", **extra: Any) -> ServerConnection:
    data = {"name": name, "dialect": "postgresql", "host": "db.invalid", "user": "u"} | extra
    return ServerConnection.model_validate(data)


Factory = Callable[[ServerConnection | FileConnection, str | None], DatabaseClient]


def state_of(session: Session) -> SessionState:
    """Reads the state through a call so mypy does not narrow it across transitions."""
    return session.state
