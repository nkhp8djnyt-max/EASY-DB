"""Errors raised by database clients, independent of the underlying driver."""

from __future__ import annotations


class DbError(Exception):
    """Base class. ``code`` is the driver's SQLSTATE / error number when there is one."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ConnectionFailed(DbError):
    """Could not establish a connection."""


class DnsError(ConnectionFailed):
    """The host name does not resolve."""


class PortClosed(ConnectionFailed):
    """The host is reachable but nothing accepts connections on that port (or it is filtered)."""


class ConnectTimeout(ConnectionFailed):
    """The server did not answer in time."""


class AuthFailed(ConnectionFailed):
    """Wrong user or password, or the user may not use that database."""


class DatabaseNotFound(ConnectionFailed):
    """The server is fine but has no database with that name."""


class SslError(ConnectionFailed):
    """TLS negotiation or certificate verification failed."""


class SslFileError(SslError):
    """A TLS certificate or key file is missing, unreadable, expired or does not match."""


class InvalidConnection(ConnectionFailed):
    """The connection settings cannot be used (undefined ``${VAR}``, unknown service ...)."""

    def __init__(self, message: str, cause: Exception) -> None:
        super().__init__(message)
        self.cause = cause


class DatabaseFileError(ConnectionFailed):
    """A database file is missing, unreadable or not a database."""


class InvalidOptionError(ConnectionFailed):
    """A driver option in the connection is unknown or malformed."""


class ConnectionLost(DbError):
    """The connection broke while in use; the client is now disconnected."""


class NotConnectedError(DbError):
    """The client was used before ``connect()`` (or after ``disconnect()``)."""


class QueryError(DbError):
    """The server rejected a statement. ``position`` is a 1-based character offset when known."""

    def __init__(
        self, message: str, *, code: str | None = None, position: int | None = None
    ) -> None:
        super().__init__(message, code=code)
        self.position = position


class QueryCancelled(QueryError):
    """The statement was cancelled by :meth:`DatabaseClient.cancel`."""


class ReadOnlyViolation(QueryError):
    """A write was attempted on a read-only connection."""
