from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pytest

from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect, TypeKind
from easydbms.core.editing import (
    EditTarget,
    KeySource,
    ReadOnly,
    ReadOnlyReason,
    choose_key,
    target_for_query,
    target_for_table,
)
from easydbms.core.schema import Table, TableKind

from .conftest import books_table, column, composite_table, keyless_table, schema_of

R = ReadOnlyReason
COLUMNS = [c.name for c in books_table().columns]


def query(
    sql: str,
    columns: list[str] | None = None,
    dialect: Dialect = POSTGRESQL,
    chosen_keys: Callable[[Table], Sequence[str] | None]
    | Mapping[Any, Sequence[str]]
    | None = None,
) -> EditTarget | ReadOnly:
    schema = schema_of(
        books_table(),
        composite_table(),
        keyless_table(),
        keyless_table(True).__class__("public", "stock2"),
    )
    return target_for_query(
        sql, schema, dialect, columns if columns is not None else COLUMNS, chosen_keys
    )


def expect_target(result: EditTarget | ReadOnly) -> EditTarget:
    assert isinstance(result, EditTarget), result
    return result


def expect_read_only(result: EditTarget | ReadOnly, reason: ReadOnlyReason) -> ReadOnly:
    assert isinstance(result, ReadOnly), result
    assert result.reason is reason, result
    return result


# ---------------------------------------------------------------------------- table tabs


def test_a_table_tab_maps_every_column() -> None:
    table = books_table()
    target = expect_target(target_for_table(table, "books", POSTGRESQL, COLUMNS))
    assert target.key == ("id",)
    assert target.key_source is KeySource.PRIMARY
    assert [c.name for c in target.columns] == COLUMNS
    assert target.by_name("price").kind is TypeKind.DECIMAL  # type: ignore[union-attr]
    assert target.by_name("id").key  # type: ignore[union-attr]
    assert not target.by_name("title").key  # type: ignore[union-attr]
    assert target.width == len(COLUMNS)


def test_binary_columns_are_mapped_but_not_editable() -> None:
    target = expect_target(target_for_table(books_table(), "books", POSTGRESQL, COLUMNS))
    assert target.by_name("cover").editable is False  # type: ignore[union-attr]
    assert target.by_name("title").editable is True  # type: ignore[union-attr]


def test_nullability_and_defaults_are_carried_over() -> None:
    target = expect_target(target_for_table(books_table(), "books", POSTGRESQL, COLUMNS))
    title, in_print = target.by_name("title"), target.by_name("in_print")
    assert title is not None
    assert in_print is not None
    assert not title.nullable
    assert not title.has_default
    assert in_print.has_default


def test_views_are_read_only() -> None:
    view = Table("public", "v", TableKind.VIEW, (column("id"),), ())
    result = expect_read_only(target_for_table(view, "v", POSTGRESQL, ["id"]), R.VIEW)
    assert result.table is view


def test_a_table_without_a_key_asks_for_one() -> None:
    table = keyless_table()
    result = expect_read_only(
        target_for_table(table, "stock", POSTGRESQL, ["sku", "qty"]), R.NO_KEY
    )
    assert result.table is table


def test_a_unique_index_stands_in_for_a_missing_primary_key() -> None:
    target = expect_target(
        target_for_table(keyless_table(True), "stock", POSTGRESQL, ["sku", "qty"])
    )
    assert target.key == ("sku",)
    assert target.key_source is KeySource.UNIQUE


def test_the_users_choice_of_key_columns_wins() -> None:
    target = expect_target(
        target_for_table(keyless_table(), "stock", POSTGRESQL, ["sku", "qty"], ["sku", "qty"])
    )
    assert target.key == ("sku", "qty")
    assert target.key_source is KeySource.CHOSEN


def test_a_choice_naming_unknown_columns_is_ignored() -> None:
    expect_read_only(
        target_for_table(keyless_table(), "stock", POSTGRESQL, ["sku", "qty"], ["ghost"]), R.NO_KEY
    )


def test_partial_unique_indexes_do_not_identify_rows() -> None:
    from easydbms.core.schema import Index

    table = Table(
        "public",
        "t",
        columns=(column("a"),),
        indexes=(Index("p", ("a",), unique=True, covers_all_rows=False),),
    )
    expect_read_only(target_for_table(table, "t", POSTGRESQL, ["a"]), R.NO_KEY)


def test_choose_key_prefers_primary_then_the_shortest_unique_key() -> None:
    assert choose_key(books_table(), None) == (("id",), KeySource.PRIMARY)
    assert choose_key(books_table(), ["title"]) == (("title",), KeySource.CHOSEN)
    assert choose_key(keyless_table(), None) is None
    assert choose_key(keyless_table(True), None) == (("sku",), KeySource.UNIQUE)


def test_a_result_that_matches_no_column_is_a_mismatch() -> None:
    expect_read_only(target_for_table(books_table(), "books", POSTGRESQL, ["x", "y"]), R.MISMATCH)


def test_rows_know_their_key_and_values() -> None:
    target = expect_target(
        target_for_table(
            composite_table(), "lines", POSTGRESQL, ["note", "line_no", "order_id", "qty"]
        )
    )
    row = ("n", 2, 1, 5)
    assert target.key_of(row) == (1, 2)
    assert target.values_of(row) == {"note": "n", "line_no": 2, "order_id": 1, "qty": 5}
    assert target.key_of(("n", None, 1, 5)) is None  # a NULL key part: no identity


# ---------------------------------------------------------------------------- queries


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM books",
        "select * from public.books",
        "SELECT * FROM books WHERE pages > 100 ORDER BY title LIMIT 10 OFFSET 5",
        "SELECT * FROM books b",
        "SELECT b.* FROM books b",
        "SELECT books.* FROM books",
        'SELECT * FROM "books"',
        "SELECT * FROM books WHERE id IN (SELECT id FROM books WHERE pages > 1)",
    ],
)
def test_plain_selects_of_one_table_are_editable(sql: str) -> None:
    target = expect_target(query(sql))
    assert target.table.name == "books"
    assert len(target.columns) == len(COLUMNS)


def test_a_column_list_that_includes_the_key_is_editable() -> None:
    target = expect_target(query("SELECT title, id, pages FROM books", ["title", "id", "pages"]))
    assert [(c.name, c.index) for c in target.columns] == [("title", 0), ("id", 1), ("pages", 2)]


def test_aliased_columns_map_back_to_the_table() -> None:
    target = expect_target(
        query("SELECT id AS book_id, title AS name FROM books", ["book_id", "name"])
    )
    assert [(c.name, c.index) for c in target.columns] == [("id", 0), ("title", 1)]


def test_qualified_columns_with_a_table_alias() -> None:
    target = expect_target(query("SELECT b.id, b.title FROM books b", ["id", "title"]))
    assert [c.name for c in target.columns] == ["id", "title"]


def test_expressions_are_read_only_columns_of_an_editable_result() -> None:
    target = expect_target(
        query("SELECT id, upper(title) AS t, pages FROM books", ["id", "t", "pages"])
    )
    assert [(c.name, c.index) for c in target.columns] == [("id", 0), ("pages", 2)]
    assert target.width == 3
    assert target.by_index(1) is None


def test_star_next_to_other_columns() -> None:
    target = expect_target(query("SELECT *, 1 AS one FROM books", [*COLUMNS, "one"]))
    assert len(target.columns) == len(COLUMNS)
    assert target.width == len(COLUMNS) + 1


def test_identifiers_match_the_schema_regardless_of_case() -> None:
    target = expect_target(query("SELECT ID, Title FROM BOOKS", ["id", "title"]))
    assert [c.name for c in target.columns] == ["id", "title"]


def test_a_missing_key_column_makes_the_result_read_only() -> None:
    result = expect_read_only(
        query("SELECT title, pages FROM books", ["title", "pages"]), R.KEY_NOT_SELECTED
    )
    assert result.names == ("id",)


def test_a_key_hidden_behind_an_expression_is_still_missing() -> None:
    result = expect_read_only(
        query("SELECT id + 1 AS id, title FROM books", ["id", "title"]), R.KEY_NOT_SELECTED
    )
    assert result.names == ("id",)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM books b JOIN lines l ON l.order_id = b.id",
        "SELECT * FROM books, lines",
        "SELECT DISTINCT title FROM books",
        "SELECT title, count(*) FROM books GROUP BY title",
        "SELECT count(*) FROM books",
        "SELECT * FROM (SELECT * FROM books) x",
        "WITH x AS (SELECT * FROM books) SELECT * FROM x",
        "SELECT * FROM books HAVING 1 = 1",
        "SELECT id, row_number() OVER (ORDER BY id) FROM books WINDOW w AS ()",
        "SELECT 1",
    ],
)
def test_complex_queries_are_read_only(sql: str) -> None:
    result = query(sql, COLUMNS)
    assert isinstance(result, ReadOnly)
    assert result.reason in (R.COMPLEX, R.NOT_A_QUERY)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE books SET title = 'x'",
        "SELECT 1 UNION SELECT 2",
        "DELETE FROM books",
        "not sql at all (",
    ],
)
def test_other_statements_are_not_queries(sql: str) -> None:
    expect_read_only(query(sql), R.NOT_A_QUERY)


def test_an_unknown_table_is_reported_by_name() -> None:
    result = expect_read_only(query("SELECT * FROM ghosts"), R.UNKNOWN_TABLE)
    assert result.names == ("ghosts",)


def test_views_in_queries_are_read_only() -> None:
    view = Table("public", "v", TableKind.VIEW, (column("id"),), ())
    result = target_for_query("SELECT * FROM v", schema_of(view), POSTGRESQL, ["id"])
    expect_read_only(result, R.VIEW)


def test_the_result_must_have_the_shape_of_the_select_list() -> None:
    expect_read_only(query("SELECT * FROM books", ["id", "title"]), R.COMPLEX)


def test_renamed_columns_without_an_alias_are_a_mismatch() -> None:
    expect_read_only(query("SELECT id, title FROM books", ["id", "something_else"]), R.MISMATCH)


def test_a_chosen_key_is_found_through_a_callback_or_a_mapping() -> None:
    sql = "SELECT * FROM stock"
    columns = ["sku", "qty"]
    expect_read_only(query(sql, columns), R.NO_KEY)
    by_callback = expect_target(query(sql, columns, chosen_keys=lambda table: ["sku"]))
    assert by_callback.key == ("sku",)
    by_mapping = expect_target(query(sql, columns, chosen_keys={keyless_table().key: ["qty"]}))
    assert by_mapping.key == ("qty",)


@pytest.mark.parametrize("dialect", [MYSQL, SQLITE, POSTGRESQL])
def test_every_dialect_parses_its_own_quoting(dialect: Dialect) -> None:
    quote = "`" if dialect is MYSQL else '"'
    target = expect_target(query(f"SELECT * FROM {quote}books{quote}", dialect=dialect))
    assert target.table.name == "books"
