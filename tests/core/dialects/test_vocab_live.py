"""Check the static vocabulary and type classification against what the servers report."""

from __future__ import annotations

import pymysql
import pytest

from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, TypeKind

from .conftest import EngineCase
from .test_engines import DB_ERRORS, run

pytestmark = pytest.mark.integration

K = TypeKind

#: Functions PostgreSQL implements as SQL syntax rather than as pg_proc entries.
POSTGRES_SYNTAX_FUNCTIONS = {"COALESCE", "NULLIF", "TRIM", "GREATEST", "LEAST"}
#: Functions that exist in MySQL 8 but not in the MariaDB used by the local test server.
MYSQL_ONLY_FUNCTIONS = {"REGEXP_LIKE"}
MYSQL_NO_SUCH_FUNCTION = 1305


def test_function_vocabulary_exists_on_the_server(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    names = {f.name for f in d.functions}
    with engine_case.engine.connect() as conn:
        if d is SQLITE:
            known = {r[0].upper() for r in run(conn, "PRAGMA function_list").rows}
            assert names - known == set()
        elif d is POSTGRESQL:
            known = {r[0].upper() for r in run(conn, "SELECT proname FROM pg_proc").rows}
            assert names - known - POSTGRES_SYNTAX_FUNCTIONS == set()
        else:
            missing = set()
            for name in names - MYSQL_ONLY_FUNCTIONS:
                try:
                    run(conn, f"SELECT {name}()")
                except pymysql.err.MySQLError as error:
                    # a wrong argument count (1582) or syntax (1064) proves the function exists
                    if error.args[0] == MYSQL_NO_SUCH_FUNCTION:
                        missing.add(name)
                conn.connection.rollback()
            assert missing == set()


def test_postgresql_reserved_words_cover_the_servers_catalog(engine_case: EngineCase) -> None:
    if engine_case.dialect is not POSTGRESQL:
        pytest.skip("PostgreSQL only")
    with engine_case.engine.connect() as conn:
        rows = run(conn, "SELECT word, catcode FROM pg_get_keywords()").rows
    live = {word.upper() for word, category in rows if category in ("R", "T")}
    assert live - POSTGRESQL.reserved_words == set()
    assert POSTGRESQL.reserved_words - live == set()  # and nothing needlessly quoted


def test_data_type_vocabulary_is_accepted_by_the_server(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    for data_type in d.data_types:
        # serial*, varchar, enum, ... need DDL context, arguments or both; try the usual forms
        errors = []
        for declaration in (data_type, f"{data_type}(10)", f"{data_type}('a')"):
            table = d.quote_ident(engine_case.new_table_name())
            try:
                with engine_case.engine.begin() as conn:
                    run(conn, f"CREATE TABLE {table} (c {declaration})")
                break
            except DB_ERRORS as error:
                errors.append(str(error))
        else:
            pytest.fail(f"{d.id.value} rejects data type {data_type!r}: {errors}")


# (declared in DDL, expected kind of what the server reports back)
CLASSIFY_CASES = {
    "postgresql": [
        ("integer", K.INTEGER),
        ("bigint", K.INTEGER),
        ("smallint", K.INTEGER),
        ("serial", K.INTEGER),
        ("numeric(10,2)", K.DECIMAL),
        ("double precision", K.FLOAT),
        ("real", K.FLOAT),
        ("boolean", K.BOOLEAN),
        ("varchar(20)", K.TEXT),
        ("char(3)", K.TEXT),
        ("text", K.TEXT),
        ("bytea", K.BINARY),
        ("date", K.DATE),
        ("time", K.TIME),
        ("timetz", K.TIME),
        ("timestamp(3)", K.DATETIME),
        ("timestamptz", K.DATETIME),
        ("uuid", K.UUID),
        ("json", K.JSON),
        ("jsonb", K.JSON),
        ("integer[]", K.ARRAY),
        ("text[]", K.ARRAY),
        ("interval", K.OTHER),
        ("inet", K.OTHER),
        ("money", K.OTHER),
    ],
    "mysql": [
        ("tinyint(1)", K.BOOLEAN),
        ("boolean", K.BOOLEAN),
        ("bit(1)", K.BOOLEAN),
        ("tinyint", K.INTEGER),
        ("smallint", K.INTEGER),
        ("int", K.INTEGER),
        ("bigint unsigned", K.INTEGER),
        ("year", K.INTEGER),
        ("decimal(10,2)", K.DECIMAL),
        ("float", K.FLOAT),
        ("double", K.FLOAT),
        ("varchar(20)", K.TEXT),
        ("char(3)", K.TEXT),
        ("text", K.TEXT),
        ("longtext", K.TEXT),
        ("blob", K.BINARY),
        ("varbinary(16)", K.BINARY),
        ("date", K.DATE),
        ("time(6)", K.TIME),
        ("datetime(6)", K.DATETIME),
        ("timestamp", K.DATETIME),
        ("enum('a','b')", K.ENUM),
        ("bit(8)", K.OTHER),
    ],
    "sqlite": [
        ("INTEGER", K.INTEGER),
        ("BIGINT UNSIGNED", K.INTEGER),
        ("TEXT", K.TEXT),
        ("VARCHAR(20)", K.TEXT),
        ("REAL", K.FLOAT),
        ("DOUBLE PRECISION", K.FLOAT),
        ("BLOB", K.BINARY),
        ("BOOLEAN", K.BOOLEAN),
        ("DATE", K.DATE),
        ("DATETIME", K.DATETIME),
        ("TIMESTAMP", K.DATETIME),
        ("DECIMAL(10,5)", K.DECIMAL),
        ("NUMERIC", K.DECIMAL),
        ("JSON", K.JSON),
    ],
}


def test_classify_type_on_what_the_server_reports(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    cases = CLASSIFY_CASES[d.id.value]
    table = engine_case.new_table_name()
    columns = ", ".join(f"c{i} {ddl}" for i, (ddl, _) in enumerate(cases))
    with engine_case.engine.begin() as conn:
        run(conn, f"CREATE TABLE {d.quote_ident(table)} ({columns})")
        if d is POSTGRESQL:
            reported = run(
                conn,
                "SELECT a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod) "
                f"FROM pg_attribute a WHERE a.attrelid = '{table}'::regclass "
                "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
            ).rows
        elif d is MYSQL:
            reported = run(
                conn,
                "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' "
                "ORDER BY ORDINAL_POSITION",
            ).rows
        else:
            reported = [(r[1], r[2]) for r in run(conn, f"PRAGMA table_info({table})").rows]
    assert len(reported) == len(cases)
    mismatches = [
        (ddl, server_type, d.classify_type(server_type), expected)
        for (ddl, expected), (_, server_type) in zip(cases, reported, strict=True)
        if d.classify_type(server_type) is not expected
    ]
    assert mismatches == []
