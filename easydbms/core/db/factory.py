"""Pick the client class for a connection."""

from __future__ import annotations

from ..connections import FileConnection, ServerConnection
from ..dialects import DialectId
from .base import DatabaseClient
from .clients.mysql import MySqlClient
from .clients.postgres import PostgresClient
from .clients.sqlite import SqliteClient
from .runtime import ConnectRuntime


def create_client(
    config: ServerConnection | FileConnection,
    password: str | None = None,
    runtime: ConnectRuntime | None = None,
) -> DatabaseClient:
    """A new, unconnected client; ``config`` must have its ``${ENV}`` placeholders resolved."""
    if isinstance(config, FileConnection):
        return SqliteClient(config, password)
    if config.dialect is DialectId.POSTGRESQL:
        return PostgresClient(config, password, runtime)
    return MySqlClient(config, password, runtime)
