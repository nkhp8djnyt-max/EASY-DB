from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from easydbms.core import simd
from easydbms.core.dialects import (
    MYSQL,
    POSTGRESQL,
    SQLITE,
    Dialect,
    all_dialects,
    split_statements,
    statement_at,
)


@pytest.fixture(autouse=True, params=["native", "python"])
def splitter_mode(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every test here runs against both splitters (the native one only if it was built)."""
    if request.param == "native" and not simd.available():
        pytest.skip("native extension not built")
    simd.set_enabled(request.param == "native")
    yield
    simd.set_enabled(True)


def texts(sql: str, dialect: Dialect) -> list[str]:
    return [s.text for s in split_statements(sql, dialect)]


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_plain_scripts(dialect: Dialect) -> None:
    assert texts("select 1; select 2;\nselect 3", dialect) == [
        "select 1;",
        "select 2;",
        "select 3",
    ]
    assert texts("", dialect) == []
    assert texts("  \n\t", dialect) == []
    assert texts("-- only a comment\n/* and another */", dialect) == []
    assert texts(";;  ;", dialect) == []
    assert texts("select 1;;select 2", dialect) == ["select 1;", "select 2"]


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_semicolons_inside_strings_comments_and_quoted_identifiers(dialect: Dialect) -> None:
    assert len(split_statements("select 'a;b'; select 2", dialect)) == 2
    assert len(split_statements("select 1 /* ; */; select 2 -- ;\n; select 3", dialect)) == 3
    assert len(split_statements("select 'it''s;'; select 2", dialect)) == 2


def test_dialect_specific_quoting_rules() -> None:
    assert len(split_statements('select "a;b"; select 2', POSTGRESQL)) == 2
    assert len(split_statements('select "a;b"; select 2', SQLITE)) == 2
    assert len(split_statements('select "a;b"; select 2', MYSQL)) == 2  # a string in MySQL
    assert len(split_statements("select `a;b`; select 2", MYSQL)) == 2
    assert len(split_statements("select [a;b]; select 2", SQLITE)) == 2
    assert len(split_statements(r"select 'it\'s; ok'; select 2", MYSQL)) == 2
    assert len(split_statements("select 1 # a;b\n; select 2", MYSQL)) == 2
    assert len(split_statements("select $$a;b$$; select 2", POSTGRESQL)) == 2
    assert len(split_statements("select /* a /* ; */ ; */ 1; select 2", POSTGRESQL)) == 2


def test_postgresql_function_bodies() -> None:
    script = (
        "CREATE FUNCTION f() RETURNS int AS $f$ BEGIN RETURN 1; END; $f$ LANGUAGE plpgsql;"
        "SELECT f();"
    )
    assert len(split_statements(script, POSTGRESQL)) == 2
    atomic = (
        "CREATE FUNCTION g() RETURNS int LANGUAGE sql "
        "BEGIN ATOMIC SELECT 1; SELECT 2; END; select 3"
    )
    assert texts(atomic, POSTGRESQL)[-1] == "select 3"
    assert len(split_statements(atomic, POSTGRESQL)) == 2


def test_sqlite_trigger_bodies_stay_whole() -> None:
    script = (
        "CREATE TRIGGER trg AFTER INSERT ON a BEGIN "
        "INSERT INTO b VALUES (new.id); "
        "UPDATE c SET x = CASE WHEN new.id > 1 THEN 2 ELSE 3 END; "
        "END; select 1"
    )
    parts = texts(script, SQLITE)
    assert len(parts) == 2
    assert parts[0].endswith("END;")
    assert parts[1] == "select 1"


def test_mysql_routine_bodies_with_nested_blocks() -> None:
    script = (
        "CREATE PROCEDURE p(IN a INT) BEGIN "
        "IF a > 1 THEN SELECT 1; ELSEIF a < 0 THEN SELECT 2; END IF; "
        "WHILE a > 0 DO SET a = a - 1; END WHILE; "
        "LOOP SELECT 3; END LOOP; "
        "CASE a WHEN 1 THEN SELECT 4; ELSE SELECT 5; END CASE; "
        "SELECT CASE WHEN a THEN 1 END; "
        "BEGIN SELECT 6; END; "
        "END; SELECT 7"
    )
    parts = texts(script, MYSQL)
    assert len(parts) == 2
    assert parts[0].endswith("END;")
    assert parts[1] == "SELECT 7"


def test_definer_clause_and_function_named_columns() -> None:
    script = "CREATE DEFINER=`root`@`localhost` PROCEDURE p() BEGIN SELECT 1; END; SELECT 2"
    assert len(split_statements(script, MYSQL)) == 2
    # "function" as a column name must not turn on compound-body handling
    assert len(split_statements("CREATE TABLE t (function int); BEGIN; SELECT 1", MYSQL)) == 3


@pytest.mark.parametrize("dialect", [POSTGRESQL, SQLITE], ids=lambda d: d.id.value)
def test_transaction_control_statements_are_not_bodies(dialect: Dialect) -> None:
    assert texts("BEGIN; SELECT 1; COMMIT;", dialect) == ["BEGIN;", "SELECT 1;", "COMMIT;"]


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_unterminated_trailing_input_is_the_last_statement(dialect: Dialect) -> None:
    assert texts("select 1; select 'oops; select 3", dialect) == [
        "select 1;",
        "select 'oops; select 3",
    ]


def test_statement_fields() -> None:
    sql = "  -- first\n  select 1 ;\n\n(select 2)"
    first, second = split_statements(sql, POSTGRESQL)
    assert first.keyword == "SELECT"
    assert first.text == "-- first\n  select 1 ;"
    assert first.body == "-- first\n  select 1"
    assert sql[first.start : first.end].strip() == first.text
    assert second.keyword == "("
    assert second.body == "(select 2)"
    assert sql[second.start : second.end] == second.text


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (0, "SELECT 1;"),
        (5, "SELECT 1;"),
        (9, "SELECT 1;"),  # right after the semicolon
        (10, "SELECT 1;"),  # blank line between statements -> the previous one
        (11, "SELECT 2;"),  # first character of the next statement
        (15, "SELECT 2;"),
        (21, "SELECT 2;"),
        (99, "SELECT 2;"),  # past the end is clamped
    ],
)
def test_statement_at(offset: int, expected: str) -> None:
    sql = "SELECT 1;\n\nSELECT 2;"
    found = statement_at(sql, offset, POSTGRESQL)
    assert found is not None
    assert found.text == expected


def test_statement_at_comment_above_a_statement_belongs_to_it() -> None:
    sql = "SELECT 1;\n\n-- the users\nSELECT 2;"
    found = statement_at(sql, sql.index("the users"), POSTGRESQL)
    assert found is not None
    assert found.body.endswith("SELECT 2")


def test_statement_at_without_statements() -> None:
    assert statement_at("", 0, POSTGRESQL) is None
    assert statement_at("-- nothing\n", 3, POSTGRESQL) is None


def test_splitting_scales_linearly() -> None:
    many = "select 1;\n" * 20_000
    long_statement = "select " + ", ".join(f"c{i}" for i in range(30_000)) + " from t"
    started = time.perf_counter()
    assert len(split_statements(many, POSTGRESQL)) == 20_000
    assert len(split_statements(long_statement, POSTGRESQL)) == 1
    assert time.perf_counter() - started < 5


def test_a_semicolon_inside_a_trailing_comment_is_not_a_terminator() -> None:
    (statement,) = split_statements("select 1 -- note;", POSTGRESQL)
    assert statement.body == "select 1 -- note;"
    (terminated,) = split_statements("select 1; -- note;", POSTGRESQL)
    assert terminated.body == "select 1"
