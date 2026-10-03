"""Schema introspection: what tables, columns, keys and indexes a connection has."""

from .introspect import introspect
from .model import (
    Column,
    DatabaseSchema,
    ForeignKey,
    Index,
    Table,
    TableKey,
    TableKind,
)

__all__ = [
    "Column",
    "DatabaseSchema",
    "ForeignKey",
    "Index",
    "Table",
    "TableKey",
    "TableKind",
    "introspect",
]
