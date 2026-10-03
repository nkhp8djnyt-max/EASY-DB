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
