"""A small schema and a target to build change sets on."""

from __future__ import annotations

from collections.abc import Sequence

from easydbms.core.dialects import POSTGRESQL, Dialect
from easydbms.core.editing import EditTarget, ReadOnly, target_for_table
from easydbms.core.schema import Column, DatabaseSchema, Index, Table, TableKind


def column(
    name: str,
    type_: str = "integer",
    *,
    pk: bool = False,
    null: bool = True,
    default: str | None = None,
) -> Column:
    return Column(name, type_, nullable=null and not pk, default=default, primary_key=pk)


def books_table() -> Table:
    return Table(
        "public",
        "books",
        TableKind.TABLE,
        (
            column("id", pk=True),
            column("title", "text", null=False),
            column("pages", "integer"),
            column("price", "numeric(10,2)"),
            column("rating", "double precision"),
            column("published", "date"),
            column("added", "timestamp"),
            column("in_print", "boolean", default="true"),
            column("meta", "jsonb"),
            column("cover", "bytea"),
        ),
        ("id",),
    )


def schema_of(*tables: Table) -> DatabaseSchema:
    return DatabaseSchema(POSTGRESQL.id, ("public",), "public", tables)


def target_of(
    table: Table | None = None,
    dialect: Dialect = POSTGRESQL,
    chosen_key: Sequence[str] | None = None,
) -> EditTarget:
    table = table or books_table()
    result = target_for_table(
        table, f"public.{table.name}", dialect, [c.name for c in table.columns], chosen_key
    )
    assert not isinstance(result, ReadOnly), result
    return result


def composite_table() -> Table:
    return Table(
        "public",
        "lines",
        TableKind.TABLE,
        (
            column("order_id", pk=True),
            column("line_no", pk=True),
            column("qty"),
            column("note", "text"),
        ),
        ("order_id", "line_no"),
    )


def keyless_table(unique: bool = False) -> Table:
    indexes = (Index("lines_sku", ("sku",), unique=True),) if unique else ()
    return Table(
        "public",
        "stock",
        TableKind.TABLE,
        (column("sku", "text"), column("qty")),
        (),
        (),
        indexes,
    )
