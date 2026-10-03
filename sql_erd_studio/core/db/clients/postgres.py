"""PostgreSQL client on psycopg 3."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import errors as pg_errors

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

_AUTH_STATES = {"28000", "28P01"}
_NO_DATABASE_STATE = "3D000"
_READ_ONLY_STATE = "25006"


class PostgresClient(DatabaseClient):
    def __init__(self, config: ServerConnection, password: str | None = None) -> None:
        super().__init__(config, password)
        self._server_config = config

    def _connect_kwargs(self) -> dict[str, Any]:
        config = self._server_config
        kwargs: dict[str, Any] = {
            "host": config.host,
            "port": config.effective_port,
            "connect_timeout": int(DEFAULT_TIMEOUT),
            "application_name": "SQL ERD Studio",
        }
        if config.user:
            kwargs["user"] = config.user
        if config.database:
            kwargs["dbname"] = config.database
        if self._password:
            kwargs["password"] = self._password
        kwargs.update(config.options)  # libpq keywords: sslmode, options, ...
        return kwargs

    def _open(self) -> Any:
        return psycopg.connect(autocommit=True, **self._connect_kwargs())

    def _close(self, raw: Any) -> None:
        raw.close()

    def _server_version(self, raw: Any) -> str:
        return str(raw.info.parameter_status("server_version") or raw.info.server_version)

    def _cancel(self, raw: Any) -> None:
        raw.cancel()

    def _network_steps(self) -> list[tuple[str, Callable[[], str]]]:
        timeout = float(self._server_config.options.get("connect_timeout", DEFAULT_TIMEOUT))
        return network_steps(self._server_config.host, self._server_config.effective_port, timeout)

    def _run(self, raw: Any, sql: str, max_rows: int | None) -> RawResult:
        with raw.cursor() as cursor:
            cursor.execute(sql)
            if cursor.description is None:
                return (), [], cursor.rowcount if cursor.rowcount >= 0 else None, False
            columns = tuple(column.name for column in cursor.description)
            if max_rows is None:
                return columns, cursor.fetchall(), None, False
            rows = cursor.fetchmany(max_rows + 1)
            return columns, rows[:max_rows], None, len(rows) > max_rows

    # ------------------------------------------------------------------ errors

    def _translate_connect_error(self, error: Exception) -> ConnectionFailed:
        message = _clean_connect_message(_message(error))
        lowered = message.lower()
        code = getattr(error, "sqlstate", None)
        if isinstance(error, pg_errors.InvalidPassword) or code in _AUTH_STATES:
            return AuthFailed(message, code=code)
        if isinstance(error, pg_errors.InvalidCatalogName) or code == _NO_DATABASE_STATE:
            return DatabaseNotFound(message, code=code)
        if "invalid connection option" in lowered:
            return InvalidOptionError(message)
        if "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
            return SslError(message, code=code)
        if "password authentication failed" in lowered or "no password supplied" in lowered:
            return AuthFailed(message, code=code)
        if "does not exist" in lowered and "database" in lowered:
            return DatabaseNotFound(message, code=code)
        if "translate host name" in lowered or "name or service not known" in lowered:
            return DnsError(message)
        if "timeout" in lowered or "timed out" in lowered:
            return ConnectTimeout(message)
        if "refused" in lowered or "could not connect" in lowered:
            return PortClosed(message)
        return ConnectionFailed(message, code=code)

    def _translate_query_error(self, raw: Any, error: Exception) -> DbError:
        message = _message(error)
        if isinstance(error, pg_errors.QueryCanceled):
            return QueryCancelled(message, code=error.sqlstate)
        if isinstance(error, psycopg.Error):
            if raw.closed or raw.broken:
                return ConnectionLost(message)
            code = error.sqlstate
            if code == _READ_ONLY_STATE:
                return ReadOnlyViolation(message, code=code)
            position = error.diag.statement_position
            return QueryError(
                message, code=code, position=int(position) if position is not None else None
            )
        return QueryError(message)


def _clean_connect_message(message: str) -> str:
    """libpq retries ``sslmode=prefer`` without TLS and reports both attempts; keep one of each."""
    lines: list[str] = []
    for raw in message.splitlines():
        line = raw.strip().removeprefix("connection failed: ")
        if line and line not in lines:
            lines.append(line)
    return "\n".join(lines)


def _message(error: Exception) -> str:
    """The server's own wording (primary message, detail, hint) rather than psycopg's wrapper."""
    diag = getattr(error, "diag", None)
    if diag is None or not diag.message_primary:
        return str(error).strip()
    parts = [diag.message_primary]
    if diag.message_detail:
        parts.append(f"DETAIL: {diag.message_detail}")
    if diag.message_hint:
        parts.append(f"HINT: {diag.message_hint}")
    return "\n".join(parts)
