"""Plain data returned by database clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ConnectionFailed


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The outcome of one statement."""

    #: Column names of a row-returning statement; empty for commands (INSERT, DDL, ...).
    columns: tuple[str, ...]
    #: Rows as the driver returned them (Decimal, datetime, bytes, ...), at most ``max_rows``.
    rows: tuple[tuple[Any, ...], ...]
    #: Rows affected by a command; ``None`` for row-returning statements and for DDL.
    rowcount: int | None
    #: ``True`` when more rows existed than ``max_rows``.
    truncated: bool
    #: Wall-clock seconds spent in the driver.
    duration: float

    @property
    def returns_rows(self) -> bool:
        return bool(self.columns)


@dataclass(frozen=True, slots=True)
class BoundStatement:
    """A statement with bound parameters, as :meth:`DatabaseClient.apply` runs it."""

    #: SQL with the driver's placeholders (``%s`` for PostgreSQL / MySQL, ``?`` for SQLite).
    sql: str
    params: tuple[Any, ...] = ()
    #: How many rows it must affect. Fewer than that on an ``UPDATE`` / ``DELETE`` means somebody
    #: else changed or removed the row; more means the key was not unique. ``None``: no check.
    expect_rows: int | None = 1


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The outcome of running a list of statements in one transaction."""

    ok: bool
    #: Rows affected by each statement that ran (all of them when ``ok``).
    affected: tuple[int, ...]
    duration: float
    #: Index of the statement that failed; ``None`` when everything ran but the commit failed.
    failed: int | None = None
    error: str = ""
    code: str | None = None
    #: The failing statement matched no row: the row was changed or deleted since it was loaded.
    conflict: bool = False
    #: Rows the failing statement affected when that was not the expected number (else ``None``).
    matched: int | None = None


@dataclass(frozen=True, slots=True)
class CheckStep:
    """One line of the "Test connection" report."""

    name: str
    ok: bool
    detail: str
    duration: float


@dataclass(frozen=True, slots=True)
class ConnectionCheck:
    """Result of :meth:`DatabaseClient.test`: every step that ran, and why it stopped."""

    steps: tuple[CheckStep, ...]
    server_version: str | None
    failure: ConnectionFailed | None

    @property
    def ok(self) -> bool:
        return self.failure is None
