"""PostgreSQL client on psycopg 3."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import psycopg
from psycopg import errors as pg_errors

from ...connections import ServerConnection, SslMode
from ...dialects import leading_keyword
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
from ..runtime import ConnectRuntime
from ..tls import check_files, file_steps, postgres_params

#: Statements that return rows; only these are streamed when a row limit is set.
_ROW_KEYWORDS = frozenset({"SELECT", "WITH", "VALUES", "TABLE", "SHOW", "EXPLAIN", "("})
_AUTH_STATES = {"28000", "28P01"}
_NO_DATABASE_STATE = "3D000"
_READ_ONLY_STATE = "25006"


class PostgresClient(DatabaseClient):
    def __init__(
        self,
        config: ServerConnection,
        password: str | None = None,
        runtime: ConnectRuntime | None = None,
    ) -> None:
        super().__init__(config, password, runtime)
        self._server_config = config

    def _connect_kwargs(self) -> dict[str, Any]:
        config = self._server_config
        kwargs: dict[str, Any] = {
            "host": config.host,
            "port": config.effective_port,
            "connect_timeout": int(DEFAULT_TIMEOUT),
            "application_name": "EasyDBMS",
        }
        if config.user:
            kwargs["user"] = config.user
        if config.database:
            kwargs["dbname"] = config.database
        if self._password:
            kwargs["password"] = self._password
        kwargs.update(config.options)  # libpq keywords: options, application_name, ...
        kwargs.update(postgres_params(config.ssl, self._runtime.ssl_key_password))
        route = self._runtime.route
        if route is not None:  # an SSH tunnel: the name stays (certificate check), the socket moves
            kwargs["hostaddr"] = route.host
            kwargs["port"] = route.port
        return kwargs

    def _open(self) -> Any:
        if self._server_config.ssl.mode is not SslMode.DISABLE:
            check_files(self._server_config.ssl, self._runtime.ssl_key_password)
        return psycopg.connect(autocommit=True, **self._connect_kwargs())

    def _tls_file_steps(self) -> list[tuple[str, Callable[[], str]]]:
        return file_steps(self._server_config.ssl, self._runtime.ssl_key_password)

    def _tls_summary(self, raw: Any) -> str | None:
        if not raw.pgconn.ssl_in_use:
            return "not encrypted"
        try:
            _, rows, _, _ = self._run(
                raw, "SELECT version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()", 1
            )
        except Exception:  # an old server, or a view this user may not read
            return "encrypted"
        if rows and rows[0][0]:
            return f"{rows[0][0]} · {rows[0][1]}"
        return "encrypted"

    def _close(self, raw: Any) -> None:
        raw.close()

    def _server_version(self, raw: Any) -> str:
        return str(raw.info.parameter_status("server_version") or raw.info.server_version)

    def _cancel(self, raw: Any) -> None:
        raw.cancel()

    def _network_steps(self) -> list[tuple[str, Callable[[], str]]]:
        if self._runtime.route is not None:
            return []  # the SSH steps already proved the way to the server
        timeout = float(self._server_config.options.get("connect_timeout", DEFAULT_TIMEOUT))
        return network_steps(self._server_config.host, self._server_config.effective_port, timeout)

    def _run(self, raw: Any, sql: str, max_rows: int | None) -> RawResult:
        if max_rows is not None and leading_keyword(sql, self.dialect) in _ROW_KEYWORDS:
            return self._run_streaming(raw, sql, max_rows)
        with raw.cursor() as cursor:
            cursor.execute(sql)
            if cursor.description is None:
                return (), [], cursor.rowcount if cursor.rowcount >= 0 else None, False
            columns = tuple(column.name for column in cursor.description)
            if max_rows is None:
                return columns, cursor.fetchall(), None, False
            rows = cursor.fetchmany(max_rows + 1)
            return columns, rows[:max_rows], None, len(rows) > max_rows

    def _run_bound(self, raw: Any, sql: str, params: Sequence[Any]) -> int:
        with raw.cursor() as cursor:
            cursor.execute(sql, params)
            return int(cursor.rowcount)

    def _run_streaming(self, raw: Any, sql: str, max_rows: int) -> RawResult:
        """Read at most ``max_rows`` rows without letting libpq buffer the whole result.

        Once more rows exist than wanted, the query is cancelled so the server stops producing.
        A statement that turns out to return nothing (e.g. ``WITH ... INSERT``) is reported as a
        command.
        """
        with raw.cursor() as cursor:
            stream = cursor.stream(sql)
            rows: list[tuple[Any, ...]] = []
            try:
                for row in stream:
                    rows.append(row)
                    if len(rows) > max_rows:
                        break
            except psycopg.ProgrammingError as error:
                if "didn't produce a result" in str(error):
                    return (), [], None, False
                raise
            truncated = len(rows) > max_rows
            if truncated:
                raw.cancel()
            stream.close()
            columns = tuple(column.name for column in cursor.description or ())
            return columns, rows[:max_rows], None, truncated

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
