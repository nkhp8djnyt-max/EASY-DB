"""The introspected shape of a database: tables, columns, keys, indexes.

Plain immutable data: the ERD, table tabs and (stage 4) autocomplete all read the same objects, and
a snapshot can be handed between threads without locking. ``schema`` is the PostgreSQL schema, the
MySQL database, or ``main`` for SQLite; whether to show it is a UI decision
(:attr:`DatabaseSchema.qualify`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import NamedTuple

from ..dialects import Dialect, DialectId


class TableKind(StrEnum):
    TABLE = "table"
    VIEW = "view"
    MATERIALIZED_VIEW = "materialized_view"


class TableKey(NamedTuple):
    schema: str
    name: str

    def __str__(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    #: The declared type as the server spells it (``character varying(80)``, ``int(11)``, ``TEXT``).
    type: str
    nullable: bool = True
    default: str | None = None
    comment: str | None = None
    primary_key: bool = False


@dataclass(frozen=True, slots=True)
class ForeignKey:
    name: str | None
    columns: tuple[str, ...]
    ref_schema: str
    ref_table: str
    ref_columns: tuple[str, ...]
    on_delete: str | None = None
    on_update: str | None = None

    @property
    def target(self) -> TableKey:
        return TableKey(self.ref_schema, self.ref_table)


@dataclass(frozen=True, slots=True)
class Index:
    name: str
    columns: tuple[str, ...]
    unique: bool = False
    primary: bool = False
    #: ``False`` for partial and expression indexes: ``columns`` is then not unique everywhere.
    covers_all_rows: bool = True


@dataclass(frozen=True, slots=True)
class Table:
    schema: str
    name: str
    kind: TableKind = TableKind.TABLE
    columns: tuple[Column, ...] = ()
    #: Primary key column names in key order (empty when there is none).
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    indexes: tuple[Index, ...] = ()
    comment: str | None = None
    #: The planner's row estimate (``None`` when unknown); never an exact count.
    row_estimate: int | None = None

    @property
    def key(self) -> TableKey:
        return TableKey(self.schema, self.name)

    @property
    def is_view(self) -> bool:
        return self.kind is not TableKind.TABLE

    def column(self, name: str) -> Column | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    @property
    def foreign_key_columns(self) -> frozenset[str]:
        return frozenset(name for fk in self.foreign_keys for name in fk.columns)

    @property
    def unique_keys(self) -> tuple[tuple[str, ...], ...]:
        """Column sets that identify a row: the primary key and every full unique index."""
        keys: list[tuple[str, ...]] = []
        if self.primary_key:
            keys.append(self.primary_key)
        for index in self.indexes:
            if index.unique and index.covers_all_rows and index.columns not in keys:
                keys.append(index.columns)
        return tuple(keys)

    def is_unique(self, columns: tuple[str, ...]) -> bool:
        """Is some unique key made only of ``columns`` (so at most one row per value)?"""
        wanted = set(columns)
        return any(set(key) <= wanted for key in self.unique_keys)


@dataclass(frozen=True)
class DatabaseSchema:
    dialect: DialectId
    #: Schemas (PostgreSQL), databases (MySQL) or ``("main",)`` that were read.
    schemas: tuple[str, ...]
    default_schema: str
    tables: tuple[Table, ...]
    #: Seconds the introspection took.
    duration: float = 0.0
    _by_key: dict[TableKey, Table] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self._by_key:
            self._by_key.update({table.key: table for table in self.tables})

    @property
    def qualify(self) -> bool:
        """Should the UI write ``schema.table``? Only where schemas are a real second namespace."""
        return len(self.schemas) > 1

    def table(self, key: TableKey) -> Table | None:
        return self._by_key.get(key)

    def find(self, name: str) -> Table | None:
        """Look a table up by ``schema.name`` or a bare name (default schema first)."""
        if "." in name:
            schema, _, bare = name.partition(".")
            return self._by_key.get(TableKey(schema, bare))
        preferred = self._by_key.get(TableKey(self.default_schema, name))
        if preferred is not None:
            return preferred
        matches = [t for t in self.tables if t.name == name]
        return matches[0] if len(matches) == 1 else None

    def reference(self, table: Table, dialect: Dialect) -> str:
        """How to write ``table`` in SQL: bare in the default schema, ``schema.name`` elsewhere."""
        if table.schema == self.default_schema:
            return dialect.quote_ident_if_needed(table.name)
        return ".".join(dialect.quote_ident_if_needed(part) for part in (table.schema, table.name))

    def tables_in(self, schema: str | None) -> list[Table]:
        return [t for t in self.tables if schema is None or t.schema == schema]

    def search(self, text: str, schema: str | None = None) -> list[Table]:
        """Tables whose name, or one of whose column names, contains ``text`` (any case)."""
        needle = text.casefold().strip()
        found = []
        for table in self.tables_in(schema):
            if (
                not needle
                or needle in table.name.casefold()
                or any(needle in column.name.casefold() for column in table.columns)
            ):
                found.append(table)
        return found
