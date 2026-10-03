"""A small shop schema and a helper that puts the cursor where a ``|`` stands."""

from __future__ import annotations

import pytest

from easydbms.core.autocomplete import SchemaIndex
from easydbms.core.autocomplete.context import CursorContext, analyze
from easydbms.core.dialects import POSTGRESQL, Dialect
from easydbms.core.schema import Column, DatabaseSchema, ForeignKey, Index, Table, TableKind


def col(
    name: str,
    type_: str = "integer",
    *,
    pk: bool = False,
    null: bool = True,
    comment: str | None = None,
) -> Column:
    return Column(name, type_, nullable=null and not pk, primary_key=pk, comment=comment)


def fk(
    columns: tuple[str, ...],
    ref: str,
    ref_columns: tuple[str, ...] = ("id",),
    schema: str = "public",
) -> ForeignKey:
    return ForeignKey(None, columns, schema, ref, ref_columns)


def tbl(
    name: str,
    columns: list[Column],
    pk: tuple[str, ...] = ("id",),
    fks: list[ForeignKey] | None = None,
    schema: str = "public",
    kind: TableKind = TableKind.TABLE,
    comment: str | None = None,
    indexes: list[Index] | None = None,
) -> Table:
    return Table(
        schema, name, kind, tuple(columns), pk, tuple(fks or ()), tuple(indexes or ()), comment
    )


def shop_schema() -> DatabaseSchema:
    tables = (
        tbl("categories", [col("id", pk=True), col("name", "text", null=False)]),
        tbl(
            "customers",
            [
                col("id", pk=True),
                col("name", "text", null=False),
                col("email", "text", comment="Login address"),
                col("created_at", "timestamp"),
            ],
            comment="People who buy",
        ),
        tbl(
            "products",
            [
                col("id", pk=True),
                col("category_id", null=False),
                col("name", "text"),
                col("price", "numeric(10,2)"),
            ],
            fks=[fk(("category_id",), "categories")],
        ),
        tbl(
            "orders",
            [
                col("id", pk=True),
                col("customer_id", null=False),
                col("status", "text"),
                col("total", "numeric(10,2)"),
                col("placed_at", "timestamp"),
            ],
            fks=[fk(("customer_id",), "customers")],
        ),
        tbl(
            "order_items",
            [
                col("order_id", null=False),
                col("line_no", null=False),
                col("product_id", null=False),
                col("qty"),
            ],
            pk=("order_id", "line_no"),
            fks=[fk(("order_id",), "orders"), fk(("product_id",), "products")],
        ),
        tbl(
            "employees",
            [col("id", pk=True), col("manager_id"), col("name", "text")],
            fks=[fk(("manager_id",), "employees")],
        ),
        tbl("sales_v", [col("order_id"), col("total", "numeric")], pk=(), kind=TableKind.VIEW),
    )
    return DatabaseSchema(POSTGRESQL.id, ("public",), "public", tables)


@pytest.fixture(scope="session")
def index() -> SchemaIndex:
    return SchemaIndex(shop_schema())


def at(sql: str, index: SchemaIndex | None = None, dialect: Dialect = POSTGRESQL) -> CursorContext:
    """Analyse ``sql`` with the cursor at the last ``|`` (an earlier ``||`` stays an operator)."""
    offset = sql.rindex("|")
    return analyze(sql[:offset] + sql[offset + 1 :], offset, dialect, index)
