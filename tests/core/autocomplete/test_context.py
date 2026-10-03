from __future__ import annotations

import pytest

from easydbms.core.autocomplete import CursorContext, Expect, SchemaIndex, Source
from easydbms.core.dialects import MYSQL, SQLITE

from .conftest import at

E = Expect


def refs(ctx: CursorContext) -> list[str]:
    return [s.ref for s in ctx.sources]


def columns(source: Source) -> list[str]:
    return [c.name for c in source.columns]


# ---------------------------------------------------------------------------- where we are


@pytest.mark.parametrize(
    "sql",
    [
        "|",
        "  \n |",
        "SELECT 1;|",
        "SELECT 1; |",
        "SELECT 1;\n\n  |\n\nSELECT 2",
        "sel|",
        "EXPLAIN |",
        "EXPLAIN ANALYZE |",
        "-- note\n|",
    ],
)
def test_statement_start(sql: str, index: SchemaIndex) -> None:
    assert at(sql, index).expect is E.STATEMENT


def test_the_word_being_typed_is_the_prefix_and_gets_replaced(index: SchemaIndex) -> None:
    ctx = at("SELECT * FROM cust|omers c", index)
    assert ctx.prefix == "cust"
    assert (ctx.replace_start, ctx.replace_end) == (14, 23)  # the whole word, not just the head
    ctx = at("SELECT * FROM |", index)
    assert (ctx.prefix, ctx.replace_start, ctx.replace_end) == ("", 14, 14)


def test_offsets_refer_to_the_whole_text_not_the_statement(index: SchemaIndex) -> None:
    ctx = at("SELECT 1;\nSELECT * FROM cu|", index)
    assert ctx.expect is E.TABLE
    assert (ctx.replace_start, ctx.replace_end) == (24, 26)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'abc|",
        "SELECT 'abc|def' FROM t",
        "SELECT * FROM t -- comment|",
        "SELECT /* a |b */ 1",
        "SELECT /* open |",
        "SELECT 12|",
        "SELECT :par|",
        "SELECT $1|",
        "SELECT * FROM t LIMIT |",
        "SELECT 1 AS |",
    ],
)
def test_nothing_to_complete(sql: str, index: SchemaIndex) -> None:
    assert at(sql, index).expect is E.NOTHING


def test_after_a_string_or_comment_completion_resumes(index: SchemaIndex) -> None:
    assert at("SELECT 'a' |", index).expect is E.AFTER_OPERAND
    assert at("SELECT /* c */ |", index).expect is E.OPERAND
    assert at("SELECT * FROM t -- c\nWHERE |", index).expect is E.OPERAND


# ---------------------------------------------------------------------------- tables


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM |",
        "SELECT * FROM cu|",
        "SELECT * FROM a JOIN |",
        "SELECT * FROM a LEFT OUTER JOIN |",
        "SELECT * FROM a NATURAL JOIN |",
        "SELECT * FROM a CROSS JOIN |",
        "SELECT * FROM a, |",
        "UPDATE |",
        "INSERT INTO |",
        "DELETE FROM |",
        "SELECT * FROM a WHERE x IN (SELECT y FROM |)",
        "SELECT * FROM a JOIN b ON a.id = b.id JOIN |",
        "WITH t AS (SELECT 1) SELECT * FROM |",
    ],
)
def test_table_positions(sql: str, index: SchemaIndex) -> None:
    assert at(sql, index).expect is E.TABLE


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers |",
        "SELECT * FROM customers c |",
        "SELECT * FROM customers AS c |",
        "SELECT * FROM a JOIN customers c |",
        "UPDATE customers |",
        "DELETE FROM customers |",
        "INSERT INTO customers |",
    ],
)
def test_after_a_table_the_next_clause_follows(sql: str, index: SchemaIndex) -> None:
    assert at(sql, index).expect is E.AFTER_OPERAND


def test_from_inside_a_function_is_not_a_table(index: SchemaIndex) -> None:
    ctx = at("SELECT EXTRACT(YEAR FROM |) FROM orders", index)
    assert ctx.expect is E.OPERAND
    assert refs(ctx) == ["orders"]


def test_clause_names(index: SchemaIndex) -> None:
    cases = {
        "SELECT * FROM a |": "FROM",
        "SELECT * FROM a JOIN b |": "JOIN",
        "SELECT * FROM a JOIN b ON |": "JOIN ON",
        "SELECT * FROM a WHERE |": "WHERE",
        "SELECT a FROM t GROUP BY |": "GROUP BY",
        "SELECT a FROM t GROUP BY a HAVING |": "HAVING",
        "SELECT a FROM t ORDER BY |": "ORDER BY",
        "SELECT a FROM t LIMIT 5 |": "LIMIT",
        "UPDATE t SET |": "SET",
        "INSERT INTO t VALUES |": "VALUES",
        "SELECT a FROM t UNION SELECT |": "SELECT",
        "DELETE FROM t WHERE |": "WHERE",
    }
    for sql, clause in cases.items():
        assert at(sql, index).clause == clause, sql


# ---------------------------------------------------------------------------- expressions


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT |",
        "SELECT a, |",
        "SELECT DISTINCT |",
        "SELECT a + |",
        "SELECT * FROM t WHERE |",
        "SELECT * FROM t WHERE a = |",
        "SELECT * FROM t WHERE a = 1 AND |",
        "SELECT * FROM t WHERE NOT |",
        "SELECT * FROM t WHERE a IN (|",
        "SELECT * FROM t WHERE a LIKE |",
        "SELECT * FROM t WHERE a BETWEEN |",
        "SELECT * FROM t GROUP BY |",
        "SELECT * FROM t GROUP BY a, |",
        "SELECT * FROM t ORDER BY |",
        "SELECT * FROM t HAVING |",
        "SELECT CASE WHEN |",
        "SELECT CASE WHEN a THEN |",
        "SELECT count(|)",
        "SELECT * FROM a JOIN b ON |",
        "UPDATE t SET a = |",
        "SELECT * FROM t WHERE a IS |",
        "SELECT * FROM t WHERE a || |",
    ],
)
def test_operand_positions(sql: str, index: SchemaIndex) -> None:
    assert at(sql, index).expect is E.OPERAND


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a |",
        "SELECT 1 |",
        "SELECT 'x' |",
        "SELECT (1) |",
        "SELECT * FROM t WHERE a = 1 |",
        "SELECT * FROM t WHERE a |",
        "SELECT * FROM t ORDER BY a |",
        "SELECT * FROM t ORDER BY a DESC |",
        "SELECT * FROM t WHERE a IS NULL |",
        "SELECT count(*) |",
        "SELECT * |",
    ],
)
def test_after_an_expression(sql: str, index: SchemaIndex) -> None:
    assert at(sql, index).expect is E.AFTER_OPERAND


def test_prev_word_is_exposed(index: SchemaIndex) -> None:
    assert at("SELECT * FROM t WHERE a IS |", index).prev_word == "IS"
    assert at("SELECT * FROM a JOIN b ON |", index).prev_word == "ON"
    assert at("SELECT a, |", index).prev_word == ""


def test_the_cursor_before_from_sees_the_tables_after_it(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM customers c JOIN orders o ON o.customer_id = c.id", index)
    assert ctx.expect is E.OPERAND
    assert refs(ctx) == ["c", "o"]
    assert ctx.sources[0].table is not None
    assert columns(ctx.sources[0]) == ["id", "name", "email", "created_at"]


def test_the_word_being_typed_is_not_a_table(index: SchemaIndex) -> None:
    ctx = at("SELECT * FROM cust|", index)
    assert ctx.sources == []  # "cust" is what is being typed, not a table that exists
    ctx = at("SELECT * FROM customers c WHERE c.na|", index)
    assert refs(ctx) == ["c"]


def test_select_aliases_are_collected_for_order_by(index: SchemaIndex) -> None:
    ctx = at(
        "SELECT c.name AS n, count(*) cnt, c.id FROM customers c GROUP BY c.id ORDER BY |", index
    )
    assert ctx.select_aliases == ["n", "cnt"]


def test_unclosed_parentheses_do_not_swallow_the_following_clauses(index: SchemaIndex) -> None:
    ctx = at("SELECT count(| FROM orders o WHERE o.id > 1", index)
    assert ctx.expect is E.OPERAND
    assert refs(ctx) == ["o"]
    ctx = at("SELECT coalesce(max(| FROM orders", index)
    assert refs(ctx) == ["orders"]


def test_a_scalar_subquery_sees_its_own_and_the_outer_tables(index: SchemaIndex) -> None:
    ctx = at(
        "SELECT * FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = |)",
        index,
    )
    assert ctx.expect is E.OPERAND
    assert refs(ctx) == ["o"]
    assert [s.ref for s in ctx.outer_sources] == ["c"]


def test_the_outer_query_does_not_see_the_subquery(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM customers c WHERE c.id IN (SELECT customer_id FROM orders o)", index)
    assert refs(ctx) == ["c"]
    assert ctx.outer_sources == []


def test_union_branches_have_their_own_tables(index: SchemaIndex) -> None:
    ctx = at("SELECT id FROM customers UNION SELECT | FROM orders", index)
    assert refs(ctx) == ["orders"]
    ctx = at("SELECT | FROM customers UNION SELECT id FROM orders", index)
    assert refs(ctx) == ["customers"]


def test_only_the_statement_at_the_cursor_counts(index: SchemaIndex) -> None:
    ctx = at("SELECT * FROM customers;\nSELECT | FROM orders;\nSELECT * FROM products", index)
    assert refs(ctx) == ["orders"]


# ---------------------------------------------------------------------------- qualifiers


def test_alias_dot(index: SchemaIndex) -> None:
    ctx = at("SELECT c.| FROM customers c", index)
    assert ctx.expect is E.QUALIFIED
    assert ctx.qualifier == ("c",)
    assert ctx.prefix == ""


def test_alias_dot_with_a_prefix(index: SchemaIndex) -> None:
    ctx = at("SELECT c.na| FROM customers c", index)
    assert (ctx.expect, ctx.qualifier, ctx.prefix) == (E.QUALIFIED, ("c",), "na")


def test_schema_dot_table_dot_and_quoted_parts(index: SchemaIndex) -> None:
    assert at("SELECT public.customers.| FROM public.customers", index).qualifier == (
        "public",
        "customers",
    )
    assert at('SELECT "c".| FROM customers "c"', index).qualifier == ("c",)
    assert at("SELECT * FROM public.|", index).qualifier == ("public",)
    assert at("SELECT * FROM public.|", index).expect is E.QUALIFIED


def test_a_dot_that_belongs_to_a_number_is_not_a_qualifier(index: SchemaIndex) -> None:
    ctx = at("SELECT 1.| FROM t", index)  # lexed as one number
    assert ctx.expect is E.NOTHING
    assert ctx.qualifier == ()


# ---------------------------------------------------------------------------- sources


def test_aliases_with_and_without_as(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM customers AS c, orders o, products", index)
    assert refs(ctx) == ["c", "o", "products"]
    assert [s.kind for s in ctx.sources] == ["table", "table", "table"]


def test_unknown_tables_are_kept_as_unknown_sources(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM nowhere n", index)
    assert refs(ctx) == ["n"]
    assert ctx.sources[0].kind == "unknown"
    assert ctx.sources[0].columns == ()


def test_views_are_marked(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM sales_v", index)
    assert ctx.sources[0].kind == "view"


def test_case_insensitive_table_names(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM CUSTOMERS c", index)
    assert ctx.sources[0].table is not None
    assert ctx.sources[0].table.name == "customers"


def test_schema_qualified_and_quoted_tables(index: SchemaIndex) -> None:
    ctx = at('SELECT | FROM public.customers c JOIN "orders" o ON 1=1', index)
    assert [s.table.name for s in ctx.sources if s.table] == ["customers", "orders"]


def test_a_reserved_word_after_a_table_is_not_an_alias(index: SchemaIndex) -> None:
    ctx = at("SELECT | FROM customers WHERE id = 1", index)
    assert refs(ctx) == ["customers"]
    ctx = at("SELECT | FROM customers LEFT JOIN orders ON 1=1", index)
    assert refs(ctx) == ["customers", "orders"]


def test_derived_tables_expose_their_output_columns(index: SchemaIndex) -> None:
    ctx = at(
        "SELECT x.| FROM (SELECT id, name AS nm, count(*) AS n FROM customers GROUP BY 1, 2) x",
        index,
    )
    assert columns(ctx.sources[0]) == ["id", "nm", "n"]
    assert ctx.sources[0].kind == "derived"


def test_derived_tables_with_a_star_use_the_inner_tables(index: SchemaIndex) -> None:
    ctx = at("SELECT x.| FROM (SELECT * FROM customers) x", index)
    assert columns(ctx.sources[0]) == ["id", "name", "email", "created_at"]
    ctx = at("SELECT x.| FROM (SELECT c.*, o.total FROM customers c JOIN orders o ON 1=1) x", index)
    assert columns(ctx.sources[0])[:2] == ["id", "name"]
    assert columns(ctx.sources[0])[-1] == "total"
    ctx = at("SELECT x.| FROM (SELECT o.* FROM customers c JOIN orders o ON 1=1) x", index)
    assert columns(ctx.sources[0]) == ["id", "customer_id", "status", "total", "placed_at"]


def test_unfinished_derived_tables_still_work_by_tokens(index: SchemaIndex) -> None:
    ctx = at("SELECT x.| FROM (SELECT id, name FROM customers WHERE ) x", index)
    assert columns(ctx.sources[0]) == ["id", "name"]


def test_ctes(index: SchemaIndex) -> None:
    ctx = at(
        "WITH big AS (SELECT id, total FROM orders WHERE total > 10) SELECT | FROM big b", index
    )
    assert refs(ctx) == ["b"]
    assert ctx.sources[0].kind == "cte"
    assert columns(ctx.sources[0]) == ["id", "total"]
    assert [c.name for c in ctx.ctes] == ["big"]


def test_cte_column_lists_win(index: SchemaIndex) -> None:
    ctx = at("WITH t (a, b) AS (SELECT id, total FROM orders) SELECT | FROM t", index)
    assert columns(ctx.sources[0]) == ["a", "b"]


def test_several_ctes_and_cte_over_cte(index: SchemaIndex) -> None:
    sql = (
        "WITH a AS (SELECT id, name FROM customers), b AS (SELECT id AS cid, name FROM a) "
        "SELECT | FROM b"
    )
    ctx = at(sql, index)
    assert [c.name for c in ctx.ctes] == ["a", "b"]
    assert columns(ctx.sources[0]) == ["cid", "name"]


def test_a_recursive_cte_does_not_loop(index: SchemaIndex) -> None:
    sql = (
        "WITH RECURSIVE r AS (SELECT id, manager_id FROM employees UNION ALL "
        "SELECT e.id, e.manager_id FROM employees e JOIN r ON r.id = e.manager_id) SELECT | FROM r"
    )
    ctx = at(sql, index)
    assert columns(ctx.sources[0]) == ["id", "manager_id"]


def test_ctes_are_visible_inside_the_main_query_and_subqueries(index: SchemaIndex) -> None:
    ctx = at(
        "WITH t AS (SELECT id FROM orders) SELECT * FROM customers WHERE id IN (SELECT | FROM t)",
        index,
    )
    assert refs(ctx) == ["t"]
    assert [c.name for c in ctx.ctes] == ["t"]


def test_the_cte_name_shadows_a_table(index: SchemaIndex) -> None:
    ctx = at("WITH customers AS (SELECT 1 AS one) SELECT | FROM customers", index)
    assert columns(ctx.sources[0]) == ["one"]


# ---------------------------------------------------------------------------- join and DML targets


def test_join_target_is_the_table_being_joined(index: SchemaIndex) -> None:
    ctx = at("SELECT * FROM customers c JOIN orders o ON |", index)
    assert ctx.join_target is not None
    assert ctx.join_target.ref == "o"
    assert ctx.join_target is ctx.sources[1]
    ctx = at(
        "SELECT * FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items i ON |",
        index,
    )
    assert ctx.join_target is not None
    assert ctx.join_target.ref == "i"
    assert refs(ctx) == ["c", "o", "i"]


def test_no_join_target_outside_on(index: SchemaIndex) -> None:
    assert at("SELECT * FROM customers c JOIN orders o WHERE |", index).join_target is None


def test_insert_column_list(index: SchemaIndex) -> None:
    ctx = at("INSERT INTO customers (|", index)
    assert ctx.expect is E.COLUMN_LIST
    assert ctx.target is not None
    assert ctx.target.name == "customers"
    ctx = at("INSERT INTO customers (id, name, |) VALUES (1, 'a')", index)
    assert ctx.expect is E.COLUMN_LIST
    assert ctx.listed == ("id", "name")


def test_values_group_is_not_a_column_context(index: SchemaIndex) -> None:
    ctx = at("INSERT INTO customers (id) VALUES (|", index)
    assert ctx.in_values
    assert ctx.expect is E.OPERAND


def test_on_conflict_columns(index: SchemaIndex) -> None:
    ctx = at("INSERT INTO customers (id) VALUES (1) ON CONFLICT (|", index)
    assert ctx.expect is E.COLUMN_LIST
    assert ctx.target is not None
    assert ctx.target.name == "customers"


def test_insert_select_sees_the_select_sources(index: SchemaIndex) -> None:
    ctx = at("INSERT INTO customers (id) SELECT | FROM orders o", index)
    assert refs(ctx) == ["o"]
    assert ctx.expect is E.OPERAND
    assert ctx.target is not None
    assert ctx.target.name == "customers"


def test_update_set(index: SchemaIndex) -> None:
    ctx = at("UPDATE customers c SET |", index)
    assert ctx.expect is E.SET_TARGET
    assert ctx.target is not None
    assert ctx.target.name == "customers"
    ctx = at("UPDATE customers SET name = 'x', email = 'y', |", index)
    assert ctx.expect is E.SET_TARGET
    assert ctx.listed == ("name", "email")
    ctx = at("UPDATE customers SET name = |", index)
    assert ctx.expect is E.OPERAND
    assert refs(ctx) == ["customers"]


def test_update_from_and_delete_using(index: SchemaIndex) -> None:
    ctx = at("UPDATE orders o SET status = 'x' FROM customers c WHERE |", index)
    assert refs(ctx) == ["o", "c"]
    ctx = at(
        "DELETE FROM orders o WHERE o.customer_id IN (SELECT id FROM customers WHERE |)", index
    )
    assert refs(ctx) == ["customers"]
    assert [s.ref for s in ctx.outer_sources] == ["o"]


def test_using_columns(index: SchemaIndex) -> None:
    ctx = at("SELECT * FROM orders o JOIN order_items i USING (|", index)
    assert ctx.expect is E.COLUMN_LIST
    assert refs(ctx) == ["o", "i"]


# ---------------------------------------------------------------------------- types and stars


def test_type_positions(index: SchemaIndex) -> None:
    assert at("SELECT id::| FROM customers", index).expect is E.TYPE
    assert at("SELECT CAST(id AS |) FROM customers", index).expect is E.TYPE
    assert at("SELECT CAST(id AS integer) |", index).expect is E.AFTER_OPERAND
    assert at("SELECT id AS |", index).expect is E.NOTHING


def test_star_right_before_the_cursor(index: SchemaIndex) -> None:
    ctx = at("SELECT *| FROM customers c", index)
    assert ctx.star == (7, 8, None)
    ctx = at("SELECT c.*| FROM customers c", index)
    assert ctx.star == (7, 10, "c")
    assert at("SELECT * |FROM customers c", index).star is None  # a space follows the *
    assert at("SELECT 2 *| 3", index).star is None  # multiplication
    assert at("SELECT a, *| FROM customers", index).star == (10, 11, None)
    assert at("SELECT count(*)| FROM customers", index).star is None


# ---------------------------------------------------------------------------- DDL


def test_ddl_positions(index: SchemaIndex) -> None:
    assert at("DROP TABLE |", index).expect is E.TABLE
    assert at("DROP TABLE IF EXISTS |", index).expect is E.TABLE
    assert at("DROP VIEW |", index).expect is E.TABLE
    assert at("TRUNCATE |", index).expect is E.TABLE
    assert at("TRUNCATE TABLE |", index).expect is E.TABLE
    assert at("ALTER TABLE |", index).expect is E.TABLE
    assert at("DROP |", index).expect is E.AFTER_OPERAND
    assert at("CREATE |", index).expect is E.AFTER_OPERAND
    assert at("CREATE TABLE |", index).expect is E.NOTHING  # a new name
    assert at("CREATE INDEX i ON |", index).expect is E.TABLE


def test_alter_table_columns(index: SchemaIndex) -> None:
    ctx = at("ALTER TABLE customers DROP COLUMN |", index)
    assert ctx.expect is E.TARGET_COLUMN
    assert ctx.target is not None
    assert ctx.target.name == "customers"
    assert at("ALTER TABLE customers |", index).expect is E.AFTER_OPERAND
    assert at("ALTER TABLE customers ADD COLUMN |", index).expect is E.NOTHING
    assert at("ALTER TABLE customers ADD COLUMN nick |", index).expect is E.TYPE


def test_create_index_columns(index: SchemaIndex) -> None:
    ctx = at("CREATE INDEX i ON orders (|", index)
    assert ctx.expect is E.COLUMN_LIST
    assert ctx.target is not None
    assert ctx.target.name == "orders"
    ctx = at("CREATE UNIQUE INDEX i ON orders (status, |", index)
    assert ctx.listed == ("status",)


def test_create_table_column_definitions(index: SchemaIndex) -> None:
    assert at("CREATE TABLE t (|", index).expect is E.NOTHING
    assert at("CREATE TABLE t (a |", index).expect is E.TYPE
    assert at("CREATE TABLE t (a integer |", index).expect is E.AFTER_OPERAND
    assert at("CREATE TABLE t (a integer, b |", index).expect is E.TYPE
    assert at("CREATE TABLE t (a integer REFERENCES |", index).expect is E.TABLE


# ---------------------------------------------------------------------------- quoted names


def test_inside_a_quoted_identifier(index: SchemaIndex) -> None:
    ctx = at('SELECT "cust| FROM customers', index)
    assert ctx.quote == '"'
    assert ctx.prefix == "cust"
    assert ctx.expect is E.OPERAND
    ctx = at('SELECT * FROM "cust|"', index)
    assert (ctx.quote, ctx.prefix, ctx.expect) == ('"', "cust", E.TABLE)
    assert ctx.replace_end - ctx.replace_start == len('"cust"')  # the closing quote goes too


def test_mysql_backticks_and_sqlite_brackets(index: SchemaIndex) -> None:
    ctx = at("SELECT * FROM `cust|`", index, MYSQL)
    assert (ctx.quote, ctx.prefix, ctx.expect) == ("`", "cust", E.TABLE)
    ctx = at("SELECT * FROM [cust|]", index, SQLITE)
    assert (ctx.quote, ctx.prefix, ctx.expect) == ("[", "cust", E.TABLE)
    assert at('SELECT "abc|"', index, MYSQL).expect is E.NOTHING  # a string in MySQL


# ---------------------------------------------------------------------------- no schema


def test_everything_works_without_a_schema() -> None:
    ctx = at("SELECT | FROM customers c")
    assert ctx.expect is E.OPERAND
    assert refs(ctx) == ["c"]
    assert ctx.sources[0].kind == "unknown"
    ctx = at("SELECT * FROM customers c JOIN o|")
    assert ctx.expect is E.TABLE


def test_a_huge_script_is_cut_down_to_one_statement(index: SchemaIndex) -> None:
    script = "SELECT 1;\n" * 5000 + "SELECT | FROM customers c;\n" + "SELECT 2;\n" * 5000
    ctx = at(script, index)
    assert refs(ctx) == ["c"]
