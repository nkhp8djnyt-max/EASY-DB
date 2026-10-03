"""What a result grid may edit: one table, the columns that map to it, and how rows are identified.

Only a result that clearly is "rows of one table" can be edited: a table tab, or a plain
``SELECT … FROM table [WHERE …] [ORDER BY …] [LIMIT …]``. Everything else (joins, grouping,
``DISTINCT``, views, a result without its key columns) is read-only, and :class:`ReadOnly` says why
so the UI can explain it and, for a table without a primary key, offer to pick the key columns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ..dialects import Dialect, TypeKind
from ..schema import DatabaseSchema, Table
from .values import is_editable

RowKey = tuple[Any, ...]


class KeySource(StrEnum):
    PRIMARY = "primary"
    UNIQUE = "unique"
    CHOSEN = "chosen"


class ReadOnlyReason(StrEnum):
    CONNECTION = "connection"  # the connection itself is read-only
    VIEW = "view"
    NOT_A_QUERY = "not_a_query"  # not a plain SELECT
    COMPLEX = "complex"  # joins, grouping, DISTINCT, aggregates, subqueries, ...
    UNKNOWN_TABLE = "unknown_table"  # the schema does not know the table (yet)
    NO_KEY = "no_key"  # no primary key and no unique index: the user may pick key columns
    KEY_NOT_SELECTED = "key_not_selected"  # the key columns are missing from the select list
    MISMATCH = "mismatch"  # the result does not look like the table


@dataclass(frozen=True, slots=True)
class ReadOnly:
    reason: ReadOnlyReason
    #: Table and column names the explanation needs (``KEY_NOT_SELECTED``: the missing columns).
    names: tuple[str, ...] = ()
    #: The table, when known: the key picker needs its columns.
    table: Table | None = None


@dataclass(frozen=True, slots=True)
class TargetColumn:
    """One column of the result that maps to a column of the table."""

    name: str
    #: Position in the result's columns.
    index: int
    type: str
    kind: TypeKind
    nullable: bool
    has_default: bool
    editable: bool
    key: bool


@dataclass(frozen=True, slots=True)
class EditTarget:
    table: Table
    #: How to write the table in SQL (``public.orders``), quoting included.
    reference: str
    key: tuple[str, ...]
    key_source: KeySource
    #: The mapped columns by result position (unmapped result columns are read-only).
    columns: tuple[TargetColumn, ...]
    #: Number of columns of the result (mapped or not).
    width: int

    def by_index(self, index: int) -> TargetColumn | None:
        for column in self.columns:
            if column.index == index:
                return column
        return None

    def by_name(self, name: str) -> TargetColumn | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def key_of(self, row: Sequence[Any]) -> RowKey | None:
        """The key values of ``row``; ``None`` when one is NULL (such a row has no identity)."""
        values = tuple(row[self.key_index(name)] for name in self.key)
        return None if any(v is None for v in values) else values

    def key_index(self, name: str) -> int:
        column = self.by_name(name)
        if column is None:
            raise KeyError(name)
        return column.index

    def values_of(self, row: Sequence[Any]) -> dict[str, Any]:
        return {column.name: row[column.index] for column in self.columns}


# ---------------------------------------------------------------------- building a target


def choose_key(
    table: Table, chosen: Sequence[str] | None
) -> tuple[tuple[str, ...], KeySource] | None:
    """The columns identifying a row: the user's choice, the primary key, or a unique index."""
    if chosen:
        names = {column.name for column in table.columns}
        if all(name in names for name in chosen):
            return tuple(chosen), KeySource.CHOSEN
    if table.primary_key:
        return table.primary_key, KeySource.PRIMARY
    unique = [key for key in table.unique_keys if key]
    if unique:
        return min(unique, key=len), KeySource.UNIQUE
    return None


def _make_target(
    table: Table,
    reference: str,
    dialect: Dialect,
    mapping: Sequence[str | None],
    chosen_key: Sequence[str] | None,
) -> EditTarget | ReadOnly:
    """``mapping[i]`` is the table column result column ``i`` shows (``None``: something else)."""
    if table.is_view:
        return ReadOnly(ReadOnlyReason.VIEW, (table.name,), table)
    picked = choose_key(table, chosen_key)
    if picked is None:
        return ReadOnly(ReadOnlyReason.NO_KEY, (table.name,), table)
    key, source = picked
    present = {name for name in mapping if name is not None}
    missing = tuple(name for name in key if name not in present)
    if missing:
        return ReadOnly(ReadOnlyReason.KEY_NOT_SELECTED, missing, table)
    columns: list[TargetColumn] = []
    for index, name in enumerate(mapping):
        column = table.column(name) if name is not None else None
        if column is None:
            continue
        kind = dialect.classify_type(column.type)
        columns.append(
            TargetColumn(
                name=column.name,
                index=index,
                type=column.type,
                kind=kind,
                nullable=column.nullable,
                has_default=column.default is not None,
                editable=is_editable(kind),
                key=column.name in key,
            )
        )
    return EditTarget(table, reference, key, source, tuple(columns), len(mapping))


def target_for_table(
    table: Table,
    reference: str,
    dialect: Dialect,
    columns: Sequence[str],
    chosen_key: Sequence[str] | None = None,
) -> EditTarget | ReadOnly:
    """The target of a table tab, whose result columns are the table's columns."""
    mapping: list[str | None] = []
    for name in columns:
        column = table.column(name)
        mapping.append(column.name if column is not None else None)
    if not any(mapping):
        return ReadOnly(ReadOnlyReason.MISMATCH, (table.name,), table)
    return _make_target(table, reference, dialect, mapping, chosen_key)


_FORBIDDEN = (
    "with",
    "with_",
    "joins",
    "group",
    "having",
    "distinct",
    "windows",
    "qualify",
    "laterals",
)


def target_for_query(
    sql: str,
    schema: DatabaseSchema,
    dialect: Dialect,
    result_columns: Sequence[str],
    chosen_keys: Callable[[Table], Sequence[str] | None]
    | Mapping[Any, Sequence[str]]
    | None = None,
) -> EditTarget | ReadOnly:
    """Decide whether the result of ``sql`` can be edited, and as which table."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect.sqlglot_name)
    except SqlglotError:
        return ReadOnly(ReadOnlyReason.NOT_A_QUERY)
    if not isinstance(tree, exp.Select):
        return ReadOnly(ReadOnlyReason.NOT_A_QUERY)
    source = tree.args.get("from") or tree.args.get("from_")
    if source is None or not isinstance(source.this, exp.Table):
        return ReadOnly(ReadOnlyReason.COMPLEX)
    if any(tree.args.get(name) for name in _FORBIDDEN) or any(
        expression.find(exp.AggFunc) for expression in tree.expressions
    ):
        return ReadOnly(ReadOnlyReason.COMPLEX)
    ref = source.this
    name = ref.name
    qualified = f"{ref.db}.{name}" if ref.db else name
    table = _find_table(schema, qualified)
    if table is None:
        return ReadOnly(ReadOnlyReason.UNKNOWN_TABLE, (qualified,))
    names = {name.casefold(), (ref.alias or "").casefold(), table.name.casefold()} - {""}
    mapping = _map_select_list(tree, table, names)
    if mapping is None or len(mapping) != len(result_columns):
        return ReadOnly(ReadOnlyReason.COMPLEX, (table.name,), table)
    for shown, mapped in zip(result_columns, mapping, strict=True):
        if (
            mapped is not None
            and shown.casefold() != mapped.casefold()
            and not _is_alias(tree, shown)
        ):
            return ReadOnly(ReadOnlyReason.MISMATCH, (table.name,), table)
    chosen: Sequence[str] | None = None
    if callable(chosen_keys):
        chosen = chosen_keys(table)
    elif chosen_keys is not None:
        chosen = chosen_keys.get(table.key)
    return _make_target(table, schema.reference(table, dialect), dialect, mapping, chosen)


def _is_alias(tree: exp.Select, shown: str) -> bool:
    return any(
        isinstance(item, exp.Alias) and item.alias.casefold() == shown.casefold()
        for item in tree.expressions
    )


def _find_table(schema: DatabaseSchema, name: str) -> Table | None:
    found = schema.find(name)
    if found is not None:
        return found
    wanted = name.casefold()
    matches = [
        t
        for t in schema.tables
        if t.name.casefold() == wanted or f"{t.schema}.{t.name}".casefold() == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _map_select_list(tree: exp.Select, table: Table, names: set[str]) -> list[str | None] | None:
    mapping: list[str | None] = []
    for item in tree.expressions:
        expression = item.this if isinstance(item, exp.Alias) else item
        if isinstance(expression, exp.Star):
            mapping.extend(column.name for column in table.columns)
        elif isinstance(expression, exp.Column) and isinstance(expression.this, exp.Star):
            if expression.table and expression.table.casefold() not in names:
                return None
            mapping.extend(column.name for column in table.columns)
        elif isinstance(expression, exp.Column):
            if expression.table and expression.table.casefold() not in names:
                mapping.append(None)
                continue
            column = _find_column(table, expression.name)
            mapping.append(column.name if column is not None else None)
        else:
            mapping.append(None)
    return mapping


def _find_column(table: Table, name: str) -> Any:
    exact = table.column(name)
    if exact is not None:
        return exact
    wanted = name.casefold()
    matches = [c for c in table.columns if c.name.casefold() == wanted]
    return matches[0] if len(matches) == 1 else None
