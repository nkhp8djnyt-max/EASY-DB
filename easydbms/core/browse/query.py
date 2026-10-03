"""SQL for browsing a table page by page: sorting, a user-written filter, stable paging."""

from __future__ import annotations

from collections.abc import Sequence

import sqlglot
from sqlglot.errors import SqlglotError

from ..dialects import Dialect, split_statements
from ..schema import Table


class FilterError(ValueError):
    """The text typed into the filter box is not one usable condition."""


def validate_filter(text: str, dialect: Dialect) -> None:
    """Raise :class:`FilterError` unless ``text`` is a single condition for a ``WHERE`` clause.

    The text becomes part of a ``SELECT``; a second statement hidden behind a ``;`` would run
    too, so anything the splitter reads as more than one statement is refused. (Inside quotes and
    comments a ``;`` is fine.)
    """
    condition = text.strip()
    if not condition:
        return
    if len(split_statements(condition, dialect)) > 1 or _has_terminator(condition, dialect):
        raise FilterError("Only one condition is allowed (no ';').")
    try:
        sqlglot.parse_one(f"SELECT 1 WHERE {condition}", read=dialect.sqlglot_name)
    except SqlglotError as error:
        raise FilterError(str(error).splitlines()[0]) from error


def _has_terminator(condition: str, dialect: Dialect) -> bool:
    statements = split_statements(condition, dialect)
    return bool(statements) and statements[0].text.endswith(";")


def page_sql(
    dialect: Dialect,
    table: Table,
    *,
    limit: int,
    offset: int = 0,
    order_by: Sequence[tuple[str, bool]] = (),
    where: str = "",
) -> str:
    """``SELECT *`` of one page of ``table``.

    ``order_by`` is ``(column, descending)`` pairs. The primary key is always appended as the last
    sort keys, so rows with equal values keep a fixed order and consecutive pages neither repeat
    nor skip rows. ``OFFSET`` paging re-reads the skipped rows: it is exact, and slows down on
    pages deep inside a very large table.
    """
    if limit < 1 or offset < 0:
        raise ValueError("limit must be positive and offset must not be negative")
    names = {column.name for column in table.columns}
    for column, _ in order_by:
        if column not in names:
            raise ValueError(f"{table.name} has no column {column!r}")
    validate_filter(where, dialect)

    parts = [f"SELECT * FROM {dialect.quote_qualified(table.schema, table.name)}"]
    condition = where.strip()
    if condition:
        parts.append(f"WHERE ({condition}\n)")  # the newline protects a trailing "-- comment"
    keys = list(order_by)
    for column in table.primary_key:
        if all(column != chosen for chosen, _ in keys):
            keys.append((column, False))
    if keys:
        terms = ", ".join(
            f"{dialect.quote_ident(column)} {'DESC' if descending else 'ASC'}"
            for column, descending in keys
        )
        parts.append(f"ORDER BY {terms}")
    parts.append(f"LIMIT {limit}")
    if offset:
        parts.append(f"OFFSET {offset}")
    sql = " ".join(parts)
    if len(split_statements(sql, dialect)) != 1:  # belt and braces for the check above
        raise FilterError("The filter must be a single condition.")
    return sql
