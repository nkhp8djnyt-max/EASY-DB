from __future__ import annotations

import pytest

from sql_erd_studio.core.dialects import (
    MYSQL,
    POSTGRESQL,
    SQLITE,
    Dialect,
    SqlSyntaxError,
    all_dialects,
    check_syntax,
    format_sql,
    paginate,
    translate,
)


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_check_syntax_accepts_valid_sql(dialect: Dialect) -> None:
    assert check_syntax("select a, b from t where x = 1; select 2", dialect) == []
    assert check_syntax("", dialect) == []


def test_check_syntax_reports_unterminated_literals_with_positions() -> None:
    issues = check_syntax("select 1;\nselect 'abc", POSTGRESQL)
    assert [(i.message, i.line, i.column) for i in issues] == [
        ("unterminated string literal", 2, 8)
    ]
    assert check_syntax("select /* x", MYSQL)[0].message == "unterminated block comment"
    assert check_syntax('select "x', SQLITE)[0].message == "unterminated quoted identifier"


def test_check_syntax_reports_structural_errors() -> None:
    issues = check_syntax("SELEC 1 FROM", POSTGRESQL)
    assert len(issues) == 1
    assert (issues[0].line, issues[0].column) == (1, 7)


def test_dialect_specific_syntax_is_checked_in_that_dialect() -> None:
    assert check_syntax("select `a` from t", MYSQL) == []
    assert check_syntax("select a::int from t", POSTGRESQL) == []
    assert check_syntax("select a ilike 'x'", POSTGRESQL) == []


def test_format_sql_multiple_statements_in_mysql() -> None:
    formatted = format_sql("select `a`,b from t where x=1;select 2", MYSQL)
    assert formatted == "SELECT\n  `a`,\n  b\nFROM t\nWHERE\n  x = 1;\n\nSELECT\n  2;"


def test_format_sql_keeps_comments_and_normalises_casts() -> None:
    out = format_sql("select a::int -- count\nfrom t", POSTGRESQL)
    assert "CAST(a AS INT)" in out
    assert "/* count */" in out  # sqlglot re-emits line comments as block comments


def test_format_sql_raises_for_broken_sql() -> None:
    with pytest.raises(SqlSyntaxError) as info:
        format_sql("select 'oops", POSTGRESQL)
    assert info.value.issues[0].line == 1
    with pytest.raises(SqlSyntaxError):
        format_sql("SELEC 1 FROM", SQLITE)


def test_translate_between_dialects() -> None:
    pg_query = "SELECT a::int, b ILIKE '%x%', NOW() FROM t LIMIT 3 OFFSET 2"
    to_mysql = translate(pg_query, source=POSTGRESQL, target=MYSQL)
    assert to_mysql.sql == (
        "SELECT CAST(a AS SIGNED), LOWER(b) LIKE LOWER('%x%'), CURRENT_TIMESTAMP() "
        "FROM t LIMIT 3 OFFSET 2;"
    )
    assert to_mysql.warnings == ()
    to_sqlite = translate(pg_query, source=POSTGRESQL, target=SQLITE)
    assert "CAST(a AS INTEGER)" in to_sqlite.sql
    assert "CURRENT_TIMESTAMP" in to_sqlite.sql
    assert translate("SELECT IFNULL(a, 0) FROM t", source=MYSQL, target=POSTGRESQL).sql == (
        "SELECT COALESCE(a, 0) FROM t;"
    )
    assert translate("SELECT GROUP_CONCAT(n) FROM t", source=SQLITE, target=POSTGRESQL).sql == (
        "SELECT STRING_AGG(n, ',') FROM t;"
    )


def test_translate_warns_about_functions_the_target_does_not_have() -> None:
    result = translate("SELECT DATE_TRUNC('day', ts) FROM t", source=POSTGRESQL, target=SQLITE)
    assert "TIMESTAMP_TRUNC() is not a known SQLite function" in result.warnings
    result = translate("SELECT my_udf(x) FROM t", source=MYSQL, target=POSTGRESQL)
    assert result.warnings == ("MY_UDF() is not a known PostgreSQL function",)


def test_translate_multiple_statements_and_pretty() -> None:
    result = translate("select 1; select 2", source=SQLITE, target=MYSQL, pretty=True)
    assert result.sql == "SELECT\n  1;\n\nSELECT\n  2;"


def test_translate_rejects_broken_source() -> None:
    with pytest.raises(SqlSyntaxError):
        translate("select 'oops", source=POSTGRESQL, target=MYSQL)


def test_paginate_shape() -> None:
    sql = paginate(
        POSTGRESQL, "select id, name from t;", limit=10, offset=20, order_by=[("name", True)]
    )
    assert sql == (
        "SELECT * FROM (\nselect id, name from t\n) AS _page "
        'ORDER BY "name" DESC LIMIT 10 OFFSET 20'
    )
    assert paginate(MYSQL, "select 1", limit=5, order_by=[("a`b", False)]) == (
        "SELECT * FROM (\nselect 1\n) AS _page ORDER BY `a``b` ASC LIMIT 5"
    )


def test_paginate_survives_a_trailing_line_comment() -> None:
    sql = paginate(SQLITE, "select 1 -- why;\n", limit=1)
    assert sql.endswith("\n) AS _page LIMIT 1")
    assert "-- why;\n)" in sql
    assert paginate(SQLITE, "select 1 -- a;", limit=1).count("-- a;") == 1


@pytest.mark.parametrize(
    "bad",
    ["select 1; select 2", "", "insert into t values (1)", "drop table t", "-- nothing"],
)
def test_paginate_rejects_anything_but_one_query(bad: str) -> None:
    with pytest.raises(ValueError, match=r"exactly one statement|cannot paginate"):
        paginate(POSTGRESQL, bad, limit=10)


def test_paginate_accepts_with_values_and_parenthesised_queries() -> None:
    for query in (
        "with x as (select 1) select * from x",
        "values (1), (2)",
        "(select 1) union (select 2)",
    ):
        assert paginate(POSTGRESQL, query, limit=1).startswith("SELECT * FROM (")
    with pytest.raises(ValueError, match="non-negative"):
        paginate(POSTGRESQL, "select 1", limit=-1)
