"""Read a database's structure with a handful of catalog queries (not one per table)."""

from __future__ import annotations

from ...db import DatabaseClient
from ...dialects import DialectId
from ..model import DatabaseSchema
from . import mysql, postgres, sqlite


def introspect(client: DatabaseClient) -> DatabaseSchema:
    """Snapshot of every table, view, column, key and index the connection can see."""
    dialect = client.dialect.id
    if dialect is DialectId.POSTGRESQL:
        return postgres.load(client)
    if dialect is DialectId.MYSQL:
        return mysql.load(client)
    return sqlite.load(client)
