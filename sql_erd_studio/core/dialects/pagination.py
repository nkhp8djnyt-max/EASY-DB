"""Server-side paging and sorting of an arbitrary ``SELECT`` for the results grid."""

from __future__ import annotations

from collections.abc import Sequence

from .base import Dialect
from .splitter import split_statements

_QUERY_KEYWORDS = frozenset({"SELECT", "WITH", "VALUES", "TABLE", "("})
_ALIAS = "_page"


def paginate(
    dialect: Dialect,
    sql: str,
    *,
    limit: int,
    offset: int = 0,
    order_by: Sequence[tuple[str, bool]] = (),
) -> str:
    """Wrap ``sql`` so the server returns one page, optionally sorted by result columns.

    ``order_by`` is ``(column_name, descending)`` pairs naming columns of the *result*; names are
    quoted for the dialect. The same ``LIMIT n OFFSET m`` syntax is valid in all three dialects.

    The wrapper is a derived table, so it inherits the usual restriction that its columns must be
    uniquely named (MySQL rejects ``SELECT a, a FROM t`` as a derived table). Raises
    ``ValueError`` when ``sql`` is not exactly one row-returning statement.
    """
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be non-negative")
    statements = split_statements(sql, dialect)
    if len(statements) != 1:
        raise ValueError(f"expected exactly one statement, got {len(statements)}")
    statement = statements[0]
    if statement.keyword not in _QUERY_KEYWORDS:
        raise ValueError(f"cannot paginate a {statement.keyword or 'non-query'} statement")

    # The newline before ")" keeps a trailing "-- comment" in the inner query from eating it.
    parts = [f"SELECT * FROM (\n{statement.body}\n) AS {_ALIAS}"]
    if order_by:
        terms = ", ".join(
            f"{dialect.quote_ident(column)} {'DESC' if descending else 'ASC'}"
            for column, descending in order_by
        )
        parts.append(f"ORDER BY {terms}")
    parts.append(f"LIMIT {limit}")
    if offset:
        parts.append(f"OFFSET {offset}")
    return " ".join(parts)
