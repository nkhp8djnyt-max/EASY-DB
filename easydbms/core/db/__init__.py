"""Database access: a uniform client over PostgreSQL, MySQL/MariaDB and SQLite."""

from .base import DatabaseClient
from .errors import (
    AuthFailed,
    ConnectionFailed,
    ConnectionLost,
    ConnectTimeout,
    DatabaseFileError,
    DatabaseNotFound,
    DbError,
    DnsError,
    InvalidConnection,
    InvalidOptionError,
    NotConnectedError,
    PortClosed,
    QueryCancelled,
    QueryError,
    ReadOnlyViolation,
    SslError,
    SslFileError,
)
from .factory import create_client
from .runtime import ConnectRuntime, Route
from .types import ApplyResult, BoundStatement, CheckStep, ConnectionCheck, QueryResult

__all__ = [
    "ApplyResult",
    "AuthFailed",
    "BoundStatement",
    "CheckStep",
    "ConnectRuntime",
    "ConnectTimeout",
    "ConnectionCheck",
    "ConnectionFailed",
    "ConnectionLost",
    "DatabaseClient",
    "DatabaseFileError",
    "DatabaseNotFound",
    "DbError",
    "DnsError",
    "InvalidConnection",
    "InvalidOptionError",
    "NotConnectedError",
    "PortClosed",
    "QueryCancelled",
    "QueryError",
    "QueryResult",
    "ReadOnlyViolation",
    "Route",
    "SslError",
    "SslFileError",
    "create_client",
]
