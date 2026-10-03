"""The interface the UI talks to: one :class:`DatabaseClient` per open connection.

Clients hold a single dedicated driver connection in autocommit mode. ``execute`` is serialised,
while ``cancel`` may be called from any thread. User SQL always goes to the driver *without* a
parameter container: psycopg and PyMySQL treat ``%`` as a placeholder as soon as one is passed (even
an empty one), which would break every ``LIKE '%x%'`` typed into the editor.
"""

from __future__ import annotations

import contextlib
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Any

from ..connections import FileConnection, ServerConnection
from ..dialects import Dialect, ServerInfo
from .errors import (
    ConnectionFailed,
    ConnectionLost,
    DbError,
    NotConnectedError,
    QueryError,
)
from .types import ApplyResult, BoundStatement, CheckStep, ConnectionCheck, QueryResult

#: Seconds ``disconnect`` waits for a running statement to stop after cancelling it.
_DISCONNECT_WAIT = 5.0

#: What ``_run`` hands back: columns, rows, affected rows, truncated.
RawResult = tuple[tuple[str, ...], list[tuple[Any, ...]], int | None, bool]


class DatabaseClient(ABC):
    def __init__(
        self, config: ServerConnection | FileConnection, password: str | None = None
    ) -> None:
        self._config = config
        self._password = password
        self._raw: Any | None = None
        self._server_info: ServerInfo | None = None
        self._state_lock = threading.RLock()
        self._exec_lock = threading.Lock()
        self._busy = threading.Event()

    # ------------------------------------------------------------------ public API

    @property
    def config(self) -> ServerConnection | FileConnection:
        return self._config

    @property
    def dialect(self) -> Dialect:
        return self._config.dialect_impl

    @property
    def is_connected(self) -> bool:
        return self._raw is not None

    @property
    def server_info(self) -> ServerInfo | None:
        """Version and flavor of the connected server (``None`` until connected)."""
        return self._server_info

    def connect(self) -> None:
        """Open the connection (a no-op if already open). Raises :class:`ConnectionFailed`."""
        with self._state_lock:
            if self._raw is not None:
                return
            raw = self._open_checked()
            try:
                if self._config.read_only:
                    self._apply_read_only(raw)
                self._server_info = self.dialect.server_info(self._server_version(raw))
            except BaseException:
                self._close_quietly(raw)
                raise
            self._raw = raw

    def disconnect(self) -> None:
        """Close the connection; a statement that is running is cancelled and waited for first.

        Closing a driver connection under a running statement can crash the process (SQLite in
        particular), so the close happens only once ``execute`` has let go of it. If the statement
        refuses to stop for ``_DISCONNECT_WAIT`` seconds the connection is left to be released
        when that call returns.
        """
        with self._state_lock:
            raw, self._raw = self._raw, None
            self._server_info = None
        if raw is None:
            return
        if self._busy.is_set():
            with contextlib.suppress(Exception):
                self._cancel(raw)
        if self._exec_lock.acquire(timeout=_DISCONNECT_WAIT):
            try:
                self._close_quietly(raw)
            finally:
                self._exec_lock.release()

    def execute(self, sql: str, *, max_rows: int | None = None) -> QueryResult:
        """Run one statement and return at most ``max_rows`` rows.

        Raises :class:`QueryError` (or a subclass) for rejected statements, :class:`ConnectionLost`
        when the connection broke, :class:`NotConnectedError` before ``connect()``.

        The drivers buffer a result before handing it out, so for a table-sized SELECT page the SQL
        itself (``paginate``) instead of relying on ``max_rows`` to bound memory.
        """
        if max_rows is not None and max_rows < 0:
            raise ValueError("max_rows must not be negative")
        with self._exec_lock:
            raw = self._raw
            if raw is None:
                raise NotConnectedError("not connected")
            self._busy.set()
            started = time.perf_counter()
            try:
                columns, rows, rowcount, truncated = self._run(raw, sql, max_rows)
            except DbError:
                raise
            except Exception as error:
                translated = self._translate_query_error(raw, error)
                if isinstance(translated, ConnectionLost):
                    self._drop_lost_connection(raw)
                raise translated from error
            finally:
                self._busy.clear()
            return QueryResult(
                columns=columns,
                rows=tuple(rows),
                rowcount=rowcount,
                truncated=truncated,
                duration=time.perf_counter() - started,
            )

    def apply(self, statements: Sequence[BoundStatement]) -> ApplyResult:
        """Run ``statements`` in one transaction: every one of them, or none.

        Each statement is checked against its ``expect_rows``; a statement that fails, or that
        affects another number of rows than expected (an ``UPDATE`` whose row was changed by
        somebody else matches nothing), rolls everything back and is reported in the result with
        its index. A broken connection raises :class:`ConnectionLost` instead (the server rolls
        the transaction back by itself).
        """
        with self._exec_lock:
            raw = self._raw
            if raw is None:
                raise NotConnectedError("not connected")
            self._busy.set()
            try:
                return self._apply(raw, statements)
            finally:
                self._busy.clear()

    def _apply(self, raw: Any, statements: Sequence[BoundStatement]) -> ApplyResult:
        started = time.perf_counter()
        affected: list[int] = []

        def elapsed() -> float:
            return time.perf_counter() - started

        try:
            self._run(raw, self._begin_sql, None)
        except Exception as error:
            raise self._failure(raw, error) from error
        for index, statement in enumerate(statements):
            try:
                count = self._run_bound(raw, statement.sql, statement.params)
            except Exception as error:
                failure = self._failure(raw, error)
                self._rollback(raw)
                return ApplyResult(
                    False, tuple(affected), elapsed(), index, str(failure), failure.code
                )
            affected.append(count)
            expected = statement.expect_rows
            if expected is not None and count != expected:
                self._rollback(raw)
                return ApplyResult(
                    False, tuple(affected), elapsed(), index, conflict=count == 0, matched=count
                )
        try:
            self._run(raw, "COMMIT", None)
        except Exception as error:
            failure = self._failure(raw, error)
            self._rollback(raw)
            return ApplyResult(False, tuple(affected), elapsed(), None, str(failure), failure.code)
        return ApplyResult(True, tuple(affected), elapsed())

    def _failure(self, raw: Any, error: Exception) -> DbError:
        """The error to report for ``error``; a lost connection is dropped and raised."""
        translated = (
            error if isinstance(error, DbError) else self._translate_query_error(raw, error)
        )
        if isinstance(translated, ConnectionLost):
            self._drop_lost_connection(raw)
            raise translated from error
        return translated

    def _rollback(self, raw: Any) -> None:
        with contextlib.suppress(Exception):
            self._run(raw, "ROLLBACK", None)

    def cancel(self) -> bool:
        """Ask the server to abort the running statement. Safe to call from another thread.

        Returns ``False`` when nothing was running (or the request could not be sent).
        """
        raw = self._raw
        if raw is None or not self._busy.is_set():
            return False
        try:
            self._cancel(raw)
        except Exception:
            return False
        return True

    def test(self) -> ConnectionCheck:
        """Diagnose the connection step by step on a throw-away connection (self is untouched)."""
        steps: list[CheckStep] = []
        failure: ConnectionFailed | None = None
        version: str | None = None

        def run_step(name: str, action: Callable[[], str]) -> bool:
            nonlocal failure
            started = time.perf_counter()
            try:
                detail = action()
            except ConnectionFailed as error:
                steps.append(CheckStep(name, False, str(error), time.perf_counter() - started))
                failure = error
                return False
            steps.append(CheckStep(name, True, detail, time.perf_counter() - started))
            return True

        for name, action in self._network_steps():
            if not run_step(name, action):
                return ConnectionCheck(tuple(steps), None, failure)

        opened: list[Any] = []

        def login() -> str:
            raw = self._open_checked()
            opened.append(raw)
            return self._server_version(raw)

        try:
            if run_step(self._login_step_name, login):
                version = steps[-1].detail

                def probe() -> str:
                    self._run(opened[0], "SELECT 1", 1)
                    return "SELECT 1"

                try:
                    run_step("Query", probe)
                except Exception as error:  # a broken probe is a failed step, not a crash
                    steps.append(CheckStep("Query", False, str(error), 0.0))
                    failure = ConnectionFailed(str(error))
        finally:
            for raw in opened:
                self._close_quietly(raw)
        return ConnectionCheck(tuple(steps), version, failure)

    def __enter__(self) -> DatabaseClient:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.disconnect()

    # ------------------------------------------------------------------ driver hooks

    #: Name of the step that opens the connection in :meth:`test`.
    _login_step_name = "Login"

    @abstractmethod
    def _open(self) -> Any:
        """Open and return a driver connection in autocommit mode."""

    @abstractmethod
    def _close(self, raw: Any) -> None: ...

    @abstractmethod
    def _server_version(self, raw: Any) -> str: ...

    @abstractmethod
    def _run(self, raw: Any, sql: str, max_rows: int | None) -> RawResult: ...

    @abstractmethod
    def _run_bound(self, raw: Any, sql: str, params: Sequence[Any]) -> int:
        """Run one statement with bound parameters; return the rows it affected."""

    #: Statement that opens a transaction on the (autocommit) connection.
    _begin_sql = "BEGIN"

    @abstractmethod
    def _cancel(self, raw: Any) -> None: ...

    @abstractmethod
    def _translate_connect_error(self, error: Exception) -> ConnectionFailed: ...

    @abstractmethod
    def _translate_query_error(self, raw: Any, error: Exception) -> DbError:
        """Map a driver exception to :class:`QueryError`, :class:`ConnectionLost`, ..."""

    def _network_steps(self) -> list[tuple[str, Callable[[], str]]]:
        """Checks to run before logging in (DNS, TCP); each raises :class:`ConnectionFailed`."""
        return []

    # ------------------------------------------------------------------ helpers

    def _open_checked(self) -> Any:
        try:
            return self._open()
        except ConnectionFailed:
            raise
        except Exception as error:
            raise self._translate_connect_error(error) from error

    def _apply_read_only(self, raw: Any) -> None:
        for statement in self.dialect.read_only_statements(True):
            try:
                self._run(raw, statement, None)
            except Exception as error:
                translated = self._translate_query_error(raw, error)
                raise QueryError(
                    f"could not switch the session to read-only: {translated}"
                ) from error

    def _drop_lost_connection(self, raw: Any) -> None:
        with self._state_lock:
            if self._raw is raw:
                self._raw = None
                self._server_info = None
        self._close_quietly(raw)

    def _close_quietly(self, raw: Any) -> None:
        with contextlib.suppress(Exception):
            self._close(raw)
