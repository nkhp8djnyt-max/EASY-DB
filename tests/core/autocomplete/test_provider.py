"""Suggestions: what is offered where, in which order, and how it is inserted."""

from __future__ import annotations

import pytest

from easydbms.core.autocomplete import Completer, Completion, Completions, Expect, KeywordCase, Kind
from easydbms.core.autocomplete.provider import quality
from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect
from easydbms.core.schema import DatabaseSchema

from .conftest import col, shop_schema, tbl

K = Kind


@pytest.fixture(scope="module")
def completer() -> Completer:
    return Completer()


@pytest.fixture(scope="module")
def schema() -> DatabaseSchema:
    return shop_schema()


def run(
    completer: Completer,
    schema: DatabaseSchema | None,
    sql: str,
    *,
    dialect: Dialect = POSTGRESQL,
    **options: object,
) -> Completions:
    """Complete ``sql`` with the cursor at the last ``|``."""
    offset = sql.rindex("|")
    text = sql[:offset] + sql[offset + 1 :]
    return completer.complete(text, offset, dialect, schema, **options)  # type: ignore[arg-type]


def labels(result: Completions, kind: Kind | None = None) -> list[str]:
    return [i.label for i in result.items if kind is None or i.kind is kind]


def find(result: Completions, label: str, kind: Kind | None = None) -> Completion:
    for item in result.items:
        if item.label == label and (kind is None or item.kind is kind):
            return item
    raise AssertionError(f"{label!r} is not offered: {labels(result)[:15]}")


def apply(sql: str, result: Completions, item: Completion) -> str:
    """The text after accepting ``item`` (what the editor does, without the cursor)."""
    start, end = item.replace if item.replace else (result.replace_start, result.replace_end)
    return sql[:start] + item.insert + sql[end:]


# ---------------------------------------------------------------------------- statement start


def test_statement_start_offers_snippets_then_keywords(completer: Completer) -> None:
    result = run(completer, None, "|")
    assert result.expect is Expect.STATEMENT
    kinds = [i.kind for i in result.items]
    assert kinds[0] is K.SNIPPET
    assert "SELECT" in labels(result, K.KEYWORD)
    assert {"sel", "ins", "cte"} <= set(labels(result, K.SNIPPET))
    assert "ij" not in labels(result)  # joins make sense after a table, not at the start


def test_the_sel_snippet_expands_to_a_select_from(completer: Completer) -> None:
    result = run(completer, None, "sel|")
    item = result.items[0]
    assert (item.label, item.kind) == ("sel", K.SNIPPET)
    assert apply("sel", result, item) == "SELECT * FROM "
    assert item.cursor_back == 0


def test_snippet_cursor_marker_becomes_cursor_back(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM orders o |")
    item = find(result, "ij", K.SNIPPET)
    assert "$0" not in item.insert
    assert item.insert.startswith("INNER JOIN ")
    assert " ON " in item.insert
    assert item.cursor_back > 0
    assert item.insert[: len(item.insert) - item.cursor_back].endswith(" ")


def test_a_prefix_keeps_only_matching_candidates_best_first(completer: Completer) -> None:
    result = run(completer, None, "sel|")
    assert labels(result)[:2] == ["sel", "SELECT"]  # the exact trigger, then the keyword
    assert labels(result)[2:] == ["selc", "seld"]  # other snippets rank below the keyword
    assert "INSERT" not in labels(result)
    assert labels(run(completer, None, "select|"))[0] == "SELECT"


# ---------------------------------------------------------------------------- keyword case


@pytest.mark.parametrize(
    ("mode", "typed", "expected"),
    [
        (KeywordCase.UPPER, "sel", "SELECT"),
        (KeywordCase.LOWER, "SEL", "select"),
        (KeywordCase.PRESERVE, "sel", "select"),
        (KeywordCase.PRESERVE, "SEL", "SELECT"),
        (KeywordCase.PRESERVE, "Sel", "SELECT"),
    ],
)
def test_keyword_case(completer: Completer, mode: KeywordCase, typed: str, expected: str) -> None:
    result = run(completer, None, f"{typed}|", keyword_case=mode)
    assert expected in labels(result, K.KEYWORD)
    other = expected.swapcase() if expected.isupper() or expected.islower() else expected
    assert other not in labels(result, K.KEYWORD)


def test_preserve_without_typed_letters_defaults_to_upper(completer: Completer) -> None:
    result = run(completer, None, "|", keyword_case=KeywordCase.PRESERVE)
    assert "SELECT" in labels(result, K.KEYWORD)


def test_keyword_case_never_changes_identifiers(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM |", keyword_case=KeywordCase.LOWER)
    assert "customers" in labels(result, K.TABLE)
    result = run(completer, schema, "SELECT | FROM customers", keyword_case=KeywordCase.UPPER)
    assert "name" in labels(result, K.COLUMN)


def test_snippets_follow_the_keyword_case(completer: Completer) -> None:
    result = run(completer, None, "sel|", keyword_case=KeywordCase.LOWER)
    assert apply("sel", result, find(result, "sel", K.SNIPPET)) == "select * from "


# ---------------------------------------------------------------------------- tables


def test_after_from_tables_come_before_keywords(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM |")
    assert result.expect is Expect.TABLE
    names = labels(result)
    assert {"customers", "orders", "order_items", "sales_v"} <= set(names)
    assert find(result, "sales_v").kind is K.VIEW
    assert find(result, "customers").kind is K.TABLE


def test_table_rows_carry_column_count_and_comment(
    completer: Completer, schema: DatabaseSchema
) -> None:
    item = find(run(completer, schema, "SELECT * FROM |"), "customers")
    assert item.detail == "4 cols"
    assert "People who buy" in item.doc
    assert "4 columns: id, name, email, created_at" in item.doc
    assert find(run(completer, schema, "SELECT * FROM |"), "sales_v").detail == "2 cols · view"


def test_tables_are_filtered_by_what_is_typed(completer: Completer, schema: DatabaseSchema) -> None:
    assert labels(run(completer, schema, "SELECT * FROM cust|"))[:1] == ["customers"]
    names = labels(run(completer, schema, "SELECT * FROM ord|"), K.TABLE)
    assert names[:2] == ["order_items", "orders"]


def test_after_join_related_tables_come_first(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT * FROM orders o JOIN |")
    tables = labels(result, K.TABLE)
    assert set(tables[:2]) == {"customers", "order_items"}
    assert tables.index("categories") > tables.index("customers")
    assert "orders" in tables  # a self join is allowed


def test_the_relation_doc_names_the_foreign_key(
    completer: Completer, schema: DatabaseSchema
) -> None:
    item = find(run(completer, schema, "SELECT * FROM orders o JOIN |"), "customers")
    assert "foreign key orders(customer_id) → customers(id)" in item.doc


def test_ctes_are_offered_as_tables(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "WITH recent AS (SELECT id FROM orders) SELECT * FROM |")
    item = find(result, "recent", K.CTE)
    assert item.detail == "CTE · 1 cols"
    assert labels(result)[0] == "recent"


def test_other_schemas_are_prefixed_and_offered_as_schemas(completer: Completer) -> None:
    schema = DatabaseSchema(
        POSTGRESQL.id,
        ("public", "audit"),
        "public",
        (
            tbl("users", [col("id", pk=True)]),
            tbl("log", [col("id", pk=True), col("user_id")], schema="audit"),
        ),
    )
    result = run(completer, schema, "SELECT * FROM |")
    assert find(result, "users").insert == "users"
    log = find(result, "audit.log")
    assert log.insert == "audit.log"
    item = find(result, "audit", K.SCHEMA)
    assert item.insert == "audit."
    assert item.retrigger
    qualified = run(completer, schema, "SELECT * FROM audit.|")
    assert labels(qualified) == ["log"]


def test_identifiers_that_need_quotes_are_quoted(completer: Completer) -> None:
    schema = DatabaseSchema(
        POSTGRESQL.id,
        ("public",),
        "public",
        (tbl("Order Lines", [col("Line No", pk=True), col("select")], pk=("Line No",)),),
    )
    result = run(completer, schema, "SELECT * FROM |")
    assert find(result, "Order Lines").insert == '"Order Lines"'
    columns = run(completer, schema, 'SELECT | FROM "Order Lines"')
    assert find(columns, "Line No", K.COLUMN).insert == '"Line No"'
    assert find(columns, "select", K.COLUMN).insert == '"select"'


def test_without_a_schema_only_ctes_and_keywords_are_offered(completer: Completer) -> None:
    result = run(completer, None, "WITH a AS (SELECT 1) SELECT * FROM |")
    assert labels(result) == ["a"]
    assert labels(run(completer, None, "SELECT * FROM |")) == []


# ---------------------------------------------------------------------------- columns


def test_columns_of_from_tables_come_first(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT | FROM orders")
    assert labels(result, K.COLUMN) == ["customer_id", "id", "placed_at", "status", "total"]
    first_non_column = next(i for i, item in enumerate(result.items) if item.kind is not K.COLUMN)
    assert first_non_column == 5
    assert find(result, "status").detail == "text"


def test_the_column_type_is_the_right_hand_detail(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT | FROM products")
    assert find(result, "price").detail == "numeric(10,2)"
    assert "NOT NULL" in find(result, "category_id").doc
    assert "primary key" in find(result, "id").doc
    assert "→ categories.id" in find(result, "category_id").doc
    assert "Login address" in find(run(completer, schema, "SELECT | FROM customers"), "email").doc


def test_ambiguous_columns_are_qualified_with_their_alias(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(
        completer, schema, "SELECT | FROM orders o JOIN customers c ON c.id = o.customer_id"
    )
    names = labels(result, K.COLUMN)
    assert "o.id" in names
    assert "c.id" in names
    assert "id" not in names
    assert "status" in names
    assert "email" in names  # unambiguous: plain
    assert find(result, "o.id").insert == "o.id"


def test_aliases_are_offered_after_columns(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT | FROM orders o")
    alias = find(result, "o", K.ALIAS)
    assert alias.insert == "o."
    assert alias.retrigger
    assert alias.detail == "orders"
    kinds = [i.kind for i in result.items]
    assert kinds.index(K.COLUMN) < kinds.index(K.ALIAS) < kinds.index(K.FUNCTION)


def test_dot_after_an_alias_lists_its_columns(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT o.| FROM orders o JOIN customers c ON true")
    assert result.expect is Expect.QUALIFIED
    assert labels(result) == ["customer_id", "id", "placed_at", "status", "total"]  # alphabetical
    plain = run(completer, schema, "SELECT o.| FROM orders o")
    assert apply("SELECT o. FROM orders o", plain, find(plain, "status")) == (
        "SELECT o.status FROM orders o"
    )


def test_dot_after_a_table_name_and_schema_qualified_table(
    completer: Completer, schema: DatabaseSchema
) -> None:
    assert "email" in labels(run(completer, schema, "SELECT customers.| FROM customers"))
    assert "email" in labels(run(completer, schema, "SELECT public.customers.| FROM customers"))
    assert "email" in labels(
        run(completer, schema, "SELECT * FROM customers WHERE public.customers.e|")
    )


def test_qualified_prefix_filters(completer: Completer, schema: DatabaseSchema) -> None:
    assert labels(run(completer, schema, "SELECT o.st| FROM orders o"))[0] == "status"
    assert labels(run(completer, schema, "SELECT o.status| FROM orders o")) == ["status"]


def test_outer_query_columns_are_offered_in_a_subquery(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(
        completer,
        schema,
        "SELECT * FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = |)",
    )
    names = labels(result, K.COLUMN)
    assert "c.email" in names  # the outer table, always qualified
    assert "o.status" not in names  # the inner ones come unqualified first
    assert names.index("status") < names.index("c.email")
    kinds = [i.kind for i in result.items]
    assert kinds.index(K.FUNCTION) > max(i for i, k in enumerate(kinds) if k is K.COLUMN)


def test_derived_table_columns(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT | FROM (SELECT id, total AS t FROM orders) d")
    assert labels(result, K.COLUMN) == ["id", "t"]
    assert labels(
        run(completer, schema, "SELECT d.| FROM (SELECT id, total AS t FROM orders) d")
    ) == ["id", "t"]


def test_order_by_offers_select_aliases(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT total AS revenue FROM orders ORDER BY |")
    assert find(result, "revenue", K.ALIAS).detail == "output column"
    assert "revenue" not in labels(
        run(completer, schema, "SELECT total AS revenue FROM orders WHERE |")
    )


def test_no_from_yet_offers_columns_of_any_table_once_typing(
    completer: Completer, schema: DatabaseSchema
) -> None:
    assert K.COLUMN not in {i.kind for i in run(completer, schema, "SELECT |").items}
    result = run(completer, schema, "SELECT ema|")
    item = find(result, "email", K.COLUMN)
    assert item.detail == "customers · text"


def test_insert_and_update_target_columns(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "INSERT INTO customers (|")
    assert labels(result) == ["id", "name", "email", "created_at"]
    result = run(completer, schema, "INSERT INTO customers (id, |")
    assert labels(result) == ["name", "email", "created_at"]  # already listed ones are not repeated
    result = run(completer, schema, "UPDATE orders SET |")
    assert labels(result) == ["id", "customer_id", "status", "total", "placed_at"]
    result = run(completer, schema, "UPDATE orders SET status = 'x', |")
    assert "status" not in labels(result)


def test_alter_table_drop_column(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "ALTER TABLE customers DROP COLUMN |")
    assert labels(result) == ["id", "name", "email", "created_at"]


def test_using_offers_shared_columns(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT * FROM orders JOIN order_items USING (|)")
    assert labels(result) == []  # no common column names in the shop schema
    schema2 = DatabaseSchema(
        POSTGRESQL.id,
        ("public",),
        "public",
        (tbl("a", [col("id"), col("x")]), tbl("b", [col("id"), col("y")])),
    )
    assert labels(run(completer, schema2, "SELECT * FROM a JOIN b USING (|)")) == ["id"]


# ---------------------------------------------------------------------------- joins


def test_join_on_offers_the_foreign_key_condition_first(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM orders o JOIN customers c ON |")
    first = result.items[0]
    assert first.kind is K.JOIN
    assert first.insert == "o.customer_id = c.id"
    assert first.detail == "foreign key orders(customer_id)"
    assert "→ customers(id)" in first.doc


def test_join_condition_is_found_from_either_side(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM customers c JOIN orders o ON |")
    assert result.items[0].insert == "o.customer_id = c.id"


def test_join_condition_with_several_foreign_keys(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM order_items oi JOIN orders o ON |")
    assert [i.insert for i in result.items if i.kind is K.JOIN] == ["oi.order_id = o.id"]
    result = run(completer, schema, "SELECT * FROM products p JOIN order_items oi ON |")
    assert [i.insert for i in result.items if i.kind is K.JOIN] == ["oi.product_id = p.id"]


def test_a_self_join_offers_both_directions(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT * FROM employees e JOIN employees m ON |")
    joins = {i.insert for i in result.items if i.kind is K.JOIN}
    assert joins == {"e.manager_id = m.id", "m.manager_id = e.id"}


def test_join_condition_uses_table_names_without_aliases(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM orders JOIN customers ON |")
    assert result.items[0].insert == "orders.customer_id = customers.id"


def test_composite_foreign_key_joins_every_pair(completer: Completer) -> None:
    schema = DatabaseSchema(
        POSTGRESQL.id,
        ("public",),
        "public",
        (
            tbl("p", [col("a"), col("b")], pk=("a", "b")),
            tbl(
                "c",
                [col("id"), col("pa"), col("pb")],
                fks=[fk_pair()],
            ),
        ),
    )
    result = run(completer, schema, "SELECT * FROM p JOIN c ON |")
    assert result.items[0].insert == "c.pa = p.a AND c.pb = p.b"


def fk_pair():
    from easydbms.core.schema import ForeignKey

    return ForeignKey(None, ("pa", "pb"), "public", "p", ("a", "b"))


def test_no_condition_when_there_is_no_foreign_key(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM categories c JOIN customers u ON |")
    assert K.JOIN not in {i.kind for i in result.items}


def test_the_join_snippet_is_offered_after_a_table(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM orders |")
    assert {"JOIN", "WHERE"} <= set(labels(result, K.KEYWORD))
    assert {"ij", "lj"} <= set(labels(result, K.SNIPPET))


# ---------------------------------------------------------------------------- star expansion


def test_star_expands_only_when_forced(completer: Completer, schema: DatabaseSchema) -> None:
    sql = "SELECT *| FROM customers"
    assert K.STAR not in {i.kind for i in run(completer, schema, sql).items}
    result = run(completer, schema, sql, forced=True)
    item = result.items[0]
    assert item.kind is K.STAR
    assert apply("SELECT * FROM customers", result, item) == (
        "SELECT id, name, email, created_at FROM customers"
    )
    assert item.detail == "4 columns"


def test_star_expansion_qualifies_with_several_tables(
    completer: Completer, schema: DatabaseSchema
) -> None:
    sql = "SELECT *| FROM orders o JOIN customers c ON true"
    result = run(completer, schema, sql, forced=True)
    text = apply("SELECT * FROM orders o JOIN customers c ON true", result, result.items[0])
    assert text.startswith(
        "SELECT o.id, o.customer_id, o.status, o.total, o.placed_at, c.id, c.name,"
    )


def test_alias_star_expands_that_table_only(completer: Completer, schema: DatabaseSchema) -> None:
    sql = "SELECT c.*| FROM orders o JOIN customers c ON true"
    result = run(completer, schema, sql, forced=True)
    text = apply("SELECT c.* FROM orders o JOIN customers c ON true", result, result.items[0])
    assert (
        text == "SELECT c.id, c.name, c.email, c.created_at FROM orders o JOIN customers c ON true"
    )


def test_a_multiplication_star_is_not_expanded(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT price *| FROM products", forced=True)
    assert K.STAR not in {i.kind for i in result.items}


# ---------------------------------------------------------------------------- expressions


def test_functions_follow_columns_and_insert_parentheses(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT cou| FROM orders")
    item = find(result, "COUNT", K.FUNCTION)
    assert item.insert == "COUNT()"
    assert item.cursor_back == 1
    assert "COUNT(" in item.detail
    assert apply("SELECT cou FROM orders", result, item) == "SELECT COUNT() FROM orders"


def test_no_parentheses_when_one_follows(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT cou|(*) FROM orders")
    item = find(result, "COUNT", K.FUNCTION)
    assert item.insert == "COUNT"
    assert item.cursor_back == 0


def test_function_names_follow_the_keyword_case(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT cou| FROM orders", keyword_case=KeywordCase.LOWER)
    assert find(result, "count", K.FUNCTION).insert == "count()"


def test_is_offers_null_and_friends(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT * FROM orders WHERE status IS |")
    assert labels(result)[:2] == ["NULL", "NOT NULL"]
    assert K.COLUMN not in {i.kind for i in result.items}


def test_values_offer_functions_and_literals_not_columns(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "INSERT INTO customers (name) VALUES (|")
    kinds = {i.kind for i in result.items}
    assert K.COLUMN not in kinds
    assert K.FUNCTION in kinds
    assert "NULL" in labels(result, K.KEYWORD)
    assert "DEFAULT" in labels(result, K.KEYWORD)


def test_subquery_start_offers_query_keywords(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT * FROM (|")
    assert set(labels(result)) == {"SELECT", "WITH", "VALUES", "TABLE"}
    result = run(completer, schema, "SELECT * FROM orders WHERE id IN (|")
    assert "SELECT" in labels(result, K.KEYWORD)


def test_clause_keywords_after_a_complete_clause(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM orders WHERE total > 10 |")
    names = labels(result, K.KEYWORD)
    assert names[:2] == ["AND", "OR"]
    assert {"GROUP BY", "ORDER BY", "LIMIT"} <= set(names)
    assert "FROM" not in names
    assert "WHERE" not in names
    grouped = labels(run(completer, schema, "SELECT * FROM orders GROUP BY status |"), K.KEYWORD)
    assert grouped[0] == "HAVING"
    assert "ORDER BY" in grouped
    assert labels(run(completer, schema, "SELECT * FROM orders ORDER |"), K.KEYWORD) == ["BY"]
    assert "JOIN" in labels(run(completer, schema, "SELECT * FROM orders LEFT |"), K.KEYWORD)
    assert labels(run(completer, schema, "SELECT * FROM orders o ORDER BY o.id |"), K.KEYWORD)[
        :2
    ] == [
        "ASC",
        "DESC",
    ]


def test_types_after_a_cast(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT id::| FROM orders")
    assert result.expect is Expect.TYPE
    assert {"integer", "text"} <= {i.label.lower() for i in result.items}
    assert all(i.kind is K.TYPE for i in result.items)
    assert "VARCHAR" in labels(run(completer, schema, "CREATE TABLE t (a |"), K.TYPE)


def test_nothing_is_offered_where_a_new_name_is_typed(
    completer: Completer, schema: DatabaseSchema
) -> None:
    for sql in ("CREATE TABLE |", "SELECT 1 AS |", "SELECT * FROM orders LIMIT |", "SELECT 'abc|'"):
        result = run(completer, schema, sql)
        assert result.items == (), sql


# ---------------------------------------------------------------------------- case and quotes


def test_matching_is_case_insensitive(completer: Completer, schema: DatabaseSchema) -> None:
    assert labels(run(completer, schema, "SELECT * FROM CUST|"))[0] == "customers"
    assert labels(run(completer, schema, "SELECT * FROM customers WHERE NAM| = 1"))[0] == "name"
    assert labels(run(completer, schema, "SELECT NAM| FROM customers"), K.COLUMN)[0] == "name"
    assert labels(run(completer, schema, "SELECT C.NAM| FROM customers C"))[0] == "name"


def test_quoted_identifier_mode_offers_only_identifiers(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, 'SELECT "na| FROM customers')
    assert labels(result) == ["name"]
    assert result.items[0].insert == '"name"'
    assert K.KEYWORD not in {i.kind for i in result.items}
    tables = run(completer, schema, 'SELECT * FROM "ord|')
    assert labels(tables)[:2] == ["order_items", "orders"]
    assert tables.items[0].insert == '"order_items"'


def test_the_replaced_range_is_the_word_under_the_cursor(
    completer: Completer, schema: DatabaseSchema
) -> None:
    sql = "SELECT * FROM cust| WHERE x = 1"
    result = run(completer, schema, sql)
    assert result.prefix == "cust"
    assert (result.replace_start, result.replace_end) == (14, 18)
    text = "SELECT * FROM cust WHERE x = 1"
    assert apply(text, result, result.items[0]) == "SELECT * FROM customers WHERE x = 1"


def test_a_word_continuing_after_the_cursor_is_replaced_whole(
    completer: Completer, schema: DatabaseSchema
) -> None:
    result = run(completer, schema, "SELECT * FROM cu|stomers")
    assert result.prefix == "cu" or result.replace_end > result.replace_start
    assert (result.replace_start, result.replace_end) == (14, 23)


# ---------------------------------------------------------------------------- ranking


@pytest.mark.parametrize(
    ("needle", "label", "expected"),
    [
        ("cust", "customers", 4),
        ("name", "first_name", 3),
        ("name", "firstName", 3),
        ("tom", "customers", 2),
        ("ordit", "order_items", 1),
        ("ordit", "orders", -1),
        ("na", "cardinality", -1),  # two letters inside a word: noise
        ("ard", "cardinality", 2),
        ("nz", "cardinality", -1),
        ("customers", "customers", 5),
        ("xyz", "customers", -1),
        ("", "anything", 4),
    ],
)
def test_match_quality(needle: str, label: str, expected: int) -> None:
    assert quality(needle, label) == expected


def test_abbreviations_find_underscored_names(completer: Completer, schema: DatabaseSchema) -> None:
    assert labels(run(completer, schema, "SELECT * FROM ordit|"))[0] == "order_items"


def test_typos_are_tolerated(completer: Completer, schema: DatabaseSchema) -> None:
    assert labels(run(completer, schema, "SELECT * FROM custmers|"))[:1] == ["customers"]
    assert labels(run(completer, schema, "SELECT * FROM prodcuts|"))[:1] == ["products"]


def test_unrelated_text_matches_nothing(completer: Completer, schema: DatabaseSchema) -> None:
    assert labels(run(completer, schema, "SELECT * FROM qqqq|"), K.TABLE) == []


def test_word_start_beats_substring(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT * FROM order_items oi WHERE |")
    ranked = labels(run(completer, schema, "SELECT item| FROM order_items"), K.COLUMN)
    assert ranked == []  # no column contains "item"; the table name is not a column
    assert result.expect is Expect.OPERAND
    names = labels(
        run(completer, schema, "SELECT id| FROM orders o JOIN customers c ON true"), K.COLUMN
    )
    assert names[:2] == ["c.id", "o.id"]  # the column name is what is matched, not "o.id"
    assert names[2:] == ["customer_id"]


def test_usage_frequency_breaks_ties(completer: Completer, schema: DatabaseSchema) -> None:
    plain = labels(run(completer, schema, "SELECT * FROM |"), K.TABLE)
    assert plain[:2] == ["categories", "customers"]  # alphabetical without history
    counts = {"table:public.orders": 5, "table:public.products": 2}
    ranked = labels(
        run(completer, schema, "SELECT * FROM |", frequency=lambda k: counts.get(k, 0)), K.TABLE
    )
    assert ranked[:3] == ["orders", "products", "categories"]


def test_usage_never_beats_the_context_tier(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(
        completer,
        schema,
        "SELECT | FROM orders",
        frequency=lambda k: 1000 if k == "keyword:SELECT" else 0,
    )
    assert result.items[0].kind is K.COLUMN


def test_usage_never_beats_the_match_quality(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(
        completer, schema, "SELECT * FROM or|", frequency=lambda k: 99 if "products" in k else 0
    )
    assert labels(result)[:2] == ["order_items", "orders"]


def test_usage_keys_are_stable_identifiers(completer: Completer, schema: DatabaseSchema) -> None:
    result = run(completer, schema, "SELECT | FROM orders o")
    assert find(result, "status").usage_key == "column:public.orders.status"
    assert find(result, "o", K.ALIAS).usage_key == "alias:o"
    assert find(result, "COUNT").usage_key == "function:COUNT"
    assert find(result, "DISTINCT").usage_key == "keyword:DISTINCT"
    tables = run(completer, schema, "SELECT * FROM |")
    assert find(tables, "orders").usage_key == "table:public.orders"


def test_the_limit_caps_the_list(completer: Completer, schema: DatabaseSchema) -> None:
    assert len(run(completer, schema, "SELECT | FROM orders", limit=7).items) == 7
    assert len(run(completer, schema, "SELECT | FROM orders").items) <= 100


def test_results_are_deterministic(completer: Completer, schema: DatabaseSchema) -> None:
    first = run(completer, schema, "SELECT | FROM orders o JOIN customers c ON true")
    second = run(completer, schema, "SELECT | FROM orders o JOIN customers c ON true")
    assert first == second


# ---------------------------------------------------------------------------- dialects


def test_dialect_functions_differ(completer: Completer) -> None:
    pg = labels(run(completer, None, "SELECT string_ag|", dialect=POSTGRESQL), K.FUNCTION)
    my = labels(run(completer, None, "SELECT group_conc|", dialect=MYSQL), K.FUNCTION)
    lite = labels(run(completer, None, "SELECT json_ext|", dialect=SQLITE), K.FUNCTION)
    assert "STRING_AGG" in pg
    assert "GROUP_CONCAT" in my
    assert "JSON_EXTRACT" in lite
    assert "GROUP_CONCAT" not in labels(
        run(completer, None, "SELECT group_conc|", dialect=POSTGRESQL), K.FUNCTION
    )


def test_dialect_quoting(completer: Completer) -> None:
    schema = DatabaseSchema(
        MYSQL.id, ("shop",), "shop", (tbl("Order Lines", [col("a")], schema="shop"),)
    )
    assert (
        find(run(completer, schema, "SELECT * FROM |", dialect=MYSQL), "Order Lines").insert
        == "`Order Lines`"
    )
    lite = DatabaseSchema(
        SQLITE.id, ("main",), "main", (tbl("Order Lines", [col("a")], schema="main"),)
    )
    assert (
        find(run(completer, lite, "SELECT * FROM |", dialect=SQLITE), "Order Lines").insert
        == '"Order Lines"'
    )


def test_statement_keywords_respect_the_dialect(completer: Completer) -> None:
    pg = set(labels(run(completer, None, "|", dialect=POSTGRESQL), K.KEYWORD))
    my = set(labels(run(completer, None, "|", dialect=MYSQL), K.KEYWORD))
    lite = set(labels(run(completer, None, "|", dialect=SQLITE), K.KEYWORD))
    assert "COPY" in pg
    assert "COPY" not in my
    assert "COPY" not in lite
    assert "PRAGMA" in lite
    assert "PRAGMA" not in pg
    assert "SHOW" in my
    assert "SHOW" not in lite
    assert "SELECT" in pg & my & lite


def test_table_is_not_a_subquery_starter_outside_postgresql(completer: Completer) -> None:
    assert "TABLE" in labels(run(completer, None, "SELECT * FROM (|", dialect=POSTGRESQL))
    assert "TABLE" not in labels(run(completer, None, "SELECT * FROM (|", dialect=MYSQL))


# ---------------------------------------------------------------------------- robustness


@pytest.mark.parametrize(
    "sql",
    [
        "|",
        "   |",
        ";|",
        "SELECT|",
        "SELECT * FROM |;",
        "SELECT ((((|",
        "SELECT ) FROM |",
        "SELECT * FROM orders WHERE (a = 1 AND (b = |",
        "SELECT 'unterminated|",
        "SELECT /* unterminated |",
        "SELECT -- comment |",
        "SELECT $$ body |",
        "SELECT * FROM orders JOIN |",
        "WITH |",
        "WITH a AS |",
        "WITH a AS (|",
        "INSERT INTO |",
        "UPDATE |",
        "DELETE FROM |",
        "CREATE |",
        "ALTER |",
        "DROP |",
        "EXPLAIN |",
        "EXPLAIN ANALYZE SELECT | FROM orders",
        "SELECT a.b.c.d.|",
        "SELECT .|",
        "SELECT * FROM a, b, c, |",
        "SELECT * FROM orders o, |",
        "SELECT\n  o.|\nFROM orders o",
    ],
)
def test_never_raises_and_returns_consistent_ranges(
    completer: Completer, schema: DatabaseSchema, sql: str
) -> None:
    offset = sql.rindex("|")
    text = sql[:offset] + sql[offset + 1 :]
    for dialect in (POSTGRESQL, MYSQL, SQLITE):
        result = completer.complete(text, offset, dialect, schema, forced=True)
        assert 0 <= result.replace_start <= offset <= result.replace_end <= len(text)
        for item in result.items:
            assert item.label
            assert item.insert
            if item.replace:
                assert 0 <= item.replace[0] <= item.replace[1] <= len(text)


def test_every_cursor_position_of_a_real_query_works(
    completer: Completer, schema: DatabaseSchema
) -> None:
    text = (
        "WITH recent AS (SELECT id, customer_id FROM orders\n"
        "  WHERE placed_at > now() - interval '7 days')\n"
        "SELECT c.name, count(*) AS n, sum(o.total)\n"
        "FROM recent r JOIN customers c ON c.id = r.customer_id\n"
        "LEFT JOIN orders o ON o.id = r.id\n"
        "WHERE c.email LIKE '%@x.com' AND o.status IN ('a', 'b')\n"
        "GROUP BY c.name HAVING count(*) > 1 ORDER BY n DESC, c.name LIMIT 10;\n"
        "UPDATE orders SET status = 'x' WHERE id IN (SELECT order_id FROM order_items);"
    )
    for dialect in (POSTGRESQL, MYSQL, SQLITE):
        for offset in range(len(text) + 1):
            result = completer.complete(text, offset, dialect, schema)
            assert result.replace_start <= offset <= result.replace_end


def test_a_large_schema_stays_fast(completer: Completer) -> None:
    import time

    tables = tuple(
        tbl(f"table_{n}", [col("id", pk=True), *(col(f"column_{m}") for m in range(30))])
        for n in range(1500)
    )
    schema = DatabaseSchema(POSTGRESQL.id, ("public",), "public", tables)
    completer.complete("SELECT * FROM t", 15, POSTGRESQL, schema)  # builds the index once
    started = time.perf_counter()
    result = completer.complete("SELECT * FROM tabl", 18, POSTGRESQL, schema)
    elapsed = time.perf_counter() - started
    assert len(result.items) == 100
    assert elapsed < 0.5, elapsed
