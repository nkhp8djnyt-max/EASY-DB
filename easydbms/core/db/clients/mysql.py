"""MySQL / MariaDB client on PyMySQL."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

import pymysql
from pymysql.cursors import SSCursor

from ...connections import ServerConnection
from ..base import DatabaseClient, RawResult
from ..diagnostics import DEFAULT_TIMEOUT, network_steps
from ..errors import (
    AuthFailed,
    ConnectionFailed,
    ConnectionLost,
    ConnectTimeout,
    DatabaseNotFound,
    DbError,
    DnsError,
    InvalidOptionError,
    PortClosed,
    QueryCancelled,
    QueryError,
    ReadOnlyViolation,
    SslError,
)

_QUERY_INTERRUPTED = 1317
_READ_ONLY = {1792, 1836}
_ACCESS_DENIED = {1044, 1045, 1698}  # 1698: unix_socket / plugin based rejection
_UNKNOWN_DATABASE = 1049
_CONNECTION_LOST = {2006, 2013, 2055}

_INT_OPTIONS = {"connect_timeout", "read_timeout", "write_timeout"}
_BOOL_OPTIONS = {"ssl_disabled", "ssl_verify_cert", "ssl_verify_identity"}
_STR_OPTIONS = {"charset", "init_command", "ssl_ca", "ssl_cert", "ssl_key"}


def _convert_options(options: dict[str, str]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for key, value in options.items():
        if key in _INT_OPTIONS:
            try:
                converted[key] = int(value)
            except ValueError:
                raise InvalidOptionError(
                    f"option {key} must be a whole number, got '{value}'"
                ) from None
        elif key in _BOOL_OPTIONS:
            if value.lower() not in {"1", "0", "true", "false", "yes", "no"}:
                raise InvalidOptionError(f"option {key} must be true or false, got '{value}'")
            converted[key] = value.lower() in {"1", "true", "yes"}
        elif key in _STR_OPTIONS:
            converted[key] = value
        else:
            allowed = ", ".join(sorted(_INT_OPTIONS | _BOOL_OPTIONS | _STR_OPTIONS))
            raise InvalidOptionError(f"unsupported MySQL option '{key}' (supported: {allowed})")
    return converted


class MySqlClient(DatabaseClient):
    def __init__(self, config: ServerConnection, password: str | None = None) -> None:
        super().__init__(config, password)
        self._server_config = config

    def _connect_kwargs(self) -> dict[str, Any]:
        config = self._server_config
        kwargs: dict[str, Any] = {
            "charset": "utf8mb4",
            "connect_timeout": int(DEFAULT_TIMEOUT),
            "program_name": "EasyDBMS",
        }
        if config.host.startswith("/"):
            kwargs["unix_socket"] = config.host
        else:
            kwargs["host"] = config.host
            kwargs["port"] = config.effective_port
        if config.user:
            kwargs["user"] = config.user
        if config.database:
            kwargs["database"] = config.database
        if self._password:
            kwargs["password"] = self._password
        kwargs.update(_convert_options(config.options))
        return kwargs

    def _open(self) -> Any:
        return pymysql.connect(autocommit=True, **self._connect_kwargs())

    def _close(self, raw: Any) -> None:
        raw.close()

    def _server_version(self, raw: Any) -> str:
        return str(raw.get_server_info())

    def _network_steps(self) -> list[tuple[str, Callable[[], str]]]:
        raw_timeout = self._server_config.options.get("connect_timeout", str(DEFAULT_TIMEOUT))
        try:
            timeout = float(raw_timeout)
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        return network_steps(self._server_config.host, self._server_config.effective_port, timeout)

    def _cancel(self, raw: Any) -> None:
        """``KILL QUERY`` from a second connection: MySQL has no in-band cancel."""
        control = pymysql.connect(autocommit=True, **self._connect_kwargs())
        try:
            control.cursor().execute(f"KILL QUERY {int(raw.thread_id())}")
        finally:
            control.close()

    def _run(self, raw: Any, sql: str, max_rows: int | None) -> RawResult:
        # The unbuffered cursor avoids materialising a huge result just to keep a few rows.
        cursor = raw.cursor(SSCursor if max_rows is not None else pymysql.cursors.Cursor)
        try:
            cursor.execute(sql)
            if cursor.description is None:
                affected = cursor.rowcount
                return (), [], affected if affected >= 0 else None, False
            columns = tuple(column[0] for column in cursor.description)
            if max_rows is None:
                return columns, list(cursor.fetchall()), None, False
            rows = list(cursor.fetchmany(max_rows + 1))
            truncated = len(rows) > max_rows
            if truncated:
                self._abandon_rest(raw)
            return columns, rows[:max_rows], None, truncated
        finally:
            with contextlib.suppress(pymysql.err.Error):  # a killed query reports the interruption
                cursor.close()

    def _abandon_rest(self, raw: Any) -> None:
        """Stop the server producing rows nobody will read, so closing the cursor is quick."""
        with contextlib.suppress(Exception):
            self._cancel(raw)

    # ------------------------------------------------------------------ errors

    def _translate_connect_error(self, error: Exception) -> ConnectionFailed:
        code = _code(error)
        message = _message(error)
        lowered = message.lower()
        if code in _ACCESS_DENIED or "access denied" in lowered:
            return AuthFailed(message, code=str(code) if code else None)
        if isinstance(error, FileNotFoundError):  # raised by the ssl module for ssl_ca/cert/key
            return SslError("a TLS certificate or key file was not found")
        if code == _UNKNOWN_DATABASE:
            return DatabaseNotFound(message, code=str(code))
        if code == 2026 or "ssl" in lowered or "certificate" in lowered:
            return SslError(message, code=str(code) if code else None)
        if code == 2005 or "name or service not known" in lowered or "getaddrinfo" in lowered:
            return DnsError(message)
        if "timed out" in lowered or "timeout" in lowered:
            return ConnectTimeout(message)
        if code == 2003 or "refused" in lowered:
            return PortClosed(message)
        return ConnectionFailed(message, code=str(code) if code else None)

    def _translate_query_error(self, raw: Any, error: Exception) -> DbError:
        code = _code(error)
        message = _message(error)
        if code == _QUERY_INTERRUPTED:
            return QueryCancelled(message, code=str(code))
        if (
            code in _CONNECTION_LOST
            or not raw.open
            or isinstance(error, pymysql.err.InterfaceError)
        ):
            return ConnectionLost(message)
        if code in _READ_ONLY:
            return ReadOnlyViolation(message, code=str(code))
        if isinstance(error, pymysql.err.Error):
            return QueryError(message, code=str(code) if code else None)
        return QueryError(message)


def _code(error: Exception) -> int | None:
    args: tuple[Any, ...] = error.args
    return args[0] if args and isinstance(args[0], int) else None


def _message(error: Exception) -> str:
    args: tuple[Any, ...] = error.args
    if len(args) >= 2 and isinstance(args[1], str):
        return args[1]
    return str(error)
