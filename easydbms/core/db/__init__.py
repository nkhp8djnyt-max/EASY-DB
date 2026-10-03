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
    InvalidOptionError,
    NotConnectedError,
    PortClosed,
    QueryCancelled,
    QueryError,
    ReadOnlyViolation,
    SslError,
)
from .factory import create_client
from .types import ApplyResult, BoundStatement, CheckStep, ConnectionCheck, QueryResult

__all__ = [
    "ApplyResult",
    "AuthFailed",
    "BoundStatement",
    "CheckStep",
    "ConnectTimeout",
    "ConnectionCheck",
    "ConnectionFailed",
    "ConnectionLost",
    "DatabaseClient",
    "DatabaseFileError",
    "DatabaseNotFound",
    "DbError",
    "DnsError",
    "InvalidOptionError",
    "NotConnectedError",
    "PortClosed",
    "QueryCancelled",
    "QueryError",
    "QueryResult",
    "ReadOnlyViolation",
    "SslError",
    "create_client",
]
