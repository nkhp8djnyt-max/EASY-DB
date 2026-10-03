"""Run what the dialect layer generates against real servers.

These are the tests that matter most: quoting, literals, null-safe comparison, pagination,
read-only sessions, script splitting and translation are all checked by letting the database
execute the result. They need ERD_TEST_POSTGRES_URL / ERD_TEST_MYSQL_URL for the server engines.

SQL that contains user-style text (``%``, quotes, ...) is sent through the raw DBAPI cursor with no
parameters: psycopg and PyMySQL interpret ``%`` as a placeholder as soon as a parameter container,
even an empty one, is passed, which SQLAlchemy's ``exec_driver_sql`` does.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pymysql
import pytest
from sqlalchemy import Connection, text

from sql_erd_studio.core.dialects import (
    MYSQL,
    POSTGRESQL,
    SQLITE,
    paginate,
    split_statements,
    translate,
)

from .conftest import EngineCase

pytestmark = pytest.mark.integration

DB_ERRORS = (psycopg.Error, pymysql.err.MySQLError, sqlite3.Error)


@dataclass
class Rows:
    columns: list[str]
    rows: list[tuple[Any, ...]]

    def scalar(self) -> Any:
        assert len(self.rows) == 1, self.rows
        assert len(self.rows[0]) == 1, self.rows
        return self.rows[0][0]

    def one(self) -> tuple[Any, ...]:
        assert len(self.rows) == 1, self.rows
        return self.rows[0]


def run(conn: Connection, sql: str) -> Rows:
    """Execute ``sql`` verbatim through the DBAPI cursor, without any parameter handling."""
    cursor = conn.connection.cursor()
    try:
        cursor.execute(sql)
        if cursor.description is None:
            return Rows([], [])
        return Rows([c[0] for c in cursor.description], [tuple(r) for r in cursor.fetchall()])
    finally:
        cursor.close()


def commit(conn: Connection) -> None:
    conn.connection.commit()


def rollback(conn: Connection) -> None:
    conn.connection.rollback()


SIMPLE_DDL = (
    "CREATE TABLE {t} (id integer PRIMARY KEY, name varchar(100), score integer, flag boolean)"
)


def create_simple_table(case: EngineCase) -> str:
    """Five rows incl. NULLs; returns the (lower-case, safe) table name."""
    name = case.new_table_name()
    quoted = case.dialect.quote_ident(name)
    with case.engine.begin() as conn:
        run(conn, SIMPLE_DDL.format(t=quoted))
        conn.execute(
            text(
                f"INSERT INTO {quoted} (id, name, score, flag) VALUES (:id, :name, :score, :flag)"
            ),
            [
                {"id": 1, "name": "Alice", "score": 10, "flag": True},
                {"id": 2, "name": None, "score": None, "flag": False},
                {"id": 3, "name": "bob", "score": 30, "flag": True},
                {"id": 4, "name": "Carol", "score": 20, "flag": None},
                {"id": 5, "name": "alice", "score": 5, "flag": False},
            ],
        )
    return name


def ids(conn: Connection, sql: str, **params: Any) -> list[int]:
    return [row[0] for row in conn.execute(text(sql), params)]


# ---------------------------------------------------------------------------- null-safe equality


def test_null_safe_eq_matches_null_like_a_value(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    t = d.quote_ident(create_simple_table(engine_case))
    with engine_case.engine.connect() as conn:
        predicate = d.null_safe_eq("score", ":old")
        assert ids(conn, f"SELECT id FROM {t} WHERE {predicate}", old=None) == [2]
        assert ids(conn, f"SELECT id FROM {t} WHERE {predicate}", old=30) == [3]
        assert ids(conn, f"SELECT id FROM {t} WHERE {predicate}", old=999) == []
        # the control: plain "=" never matches NULL, which is why optimistic locking needs this
        assert ids(conn, f"SELECT id FROM {t} WHERE score = :old", old=None) == []


def test_null_safe_eq_in_an_optimistic_update(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    t = d.quote_ident(create_simple_table(engine_case))
    update = text(f"UPDATE {t} SET name = :new WHERE id = :id AND {d.null_safe_eq('name', ':old')}")
    with engine_case.engine.begin() as conn:
        assert conn.execute(update, {"new": "x", "id": 2, "old": None}).rowcount == 1
        assert conn.execute(update, {"new": "y", "id": 2, "old": None}).rowcount == 0
        assert conn.execute(update, {"new": "y", "id": 2, "old": "x"}).rowcount == 1


# ---------------------------------------------------------------------------- identifiers

AWKWARD_NAMES = ["select", "Mixed Case", 'quo"te', "back`tick", "ünï", "a;b", "--x", "a.b", "100%"]


def test_quote_ident_survives_awkward_names(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    table = d.quote_ident(engine_case.new_table_name())
    columns = ", ".join(f"{d.quote_ident(name)} integer" for name in AWKWARD_NAMES)
    values = ", ".join(str(i) for i in range(len(AWKWARD_NAMES)))
    with engine_case.engine.begin() as conn:
        run(conn, f"CREATE TABLE {table} ({columns})")
        run(conn, f"INSERT INTO {table} VALUES ({values})")
        result = run(conn, f"SELECT * FROM {table}")
        assert result.columns == AWKWARD_NAMES
        assert result.one() == tuple(range(len(AWKWARD_NAMES)))
        assert run(conn, f"SELECT {d.quote_ident('Mixed Case')} FROM {table}").scalar() == 1


RESERVED_PROBES = {
    "postgresql": ["select", "order", "table", "group", "from", "where", "user", "limit", "check"],
    "mysql": ["select", "order", "table", "group", "from", "where", "key", "index", "limit"],
    "sqlite": ["select", "order", "table", "group", "from", "where", "index", "limit", "union"],
}


def test_needs_quoting_agrees_with_the_server(engine_case: EngineCase) -> None:
    """Names we leave unquoted must work as identifiers; reserved words must need quotes."""
    d = engine_case.dialect

    def create(column: str) -> None:
        table = d.quote_ident(engine_case.new_table_name())
        with engine_case.engine.begin() as conn:  # a fresh transaction per attempt
            run(conn, f"CREATE TABLE {table} ({column} integer)")

    for name in ["users", "Users1", "_x", "a$b", "naive"]:
        create(d.quote_ident_if_needed(name))
        if not d.needs_quoting(name):
            create(name)  # really works unquoted
    for name in RESERVED_PROBES[d.id.value]:
        assert d.needs_quoting(name), name
        with pytest.raises(DB_ERRORS):
            create(name)
        create(d.quote_ident(name))


QUALIFIER = {"postgresql": "public", "sqlite": "main"}


def test_quote_qualified_with_a_schema(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    t = create_simple_table(engine_case)
    with engine_case.engine.connect() as conn:
        schema = run(conn, "SELECT DATABASE()").scalar() if d is MYSQL else QUALIFIER[d.id.value]
        assert run(conn, f"SELECT count(*) FROM {d.quote_qualified(schema, t)}").scalar() == 5


# ---------------------------------------------------------------------------- pagination


def test_paginate_pages_and_sorts_on_the_server(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    t = d.quote_ident(create_simple_table(engine_case))
    query = f"SELECT id, name AS {d.quote_ident('Display Name')} FROM {t} WHERE id > 0; -- trailing"
    with engine_case.engine.connect() as conn:

        def page(**kwargs: Any) -> list[int]:
            return [row[0] for row in run(conn, paginate(d, query, **kwargs)).rows]

        assert page(limit=2, order_by=[("id", False)]) == [1, 2]
        assert page(limit=2, offset=2, order_by=[("id", False)]) == [3, 4]
        assert page(limit=3, order_by=[("id", True)]) == [5, 4, 3]
        assert page(limit=1, offset=4, order_by=[("id", False)]) == [5]
        assert page(limit=10, offset=10, order_by=[("id", False)]) == []
        assert page(limit=0) == []
        # sort by an alias that needs quoting (NULL rows excluded: their placement differs)
        scored = (
            f"SELECT id, score AS {d.quote_ident('Display Score')} FROM {t} WHERE score IS NOT NULL"
        )
        by_alias = paginate(d, scored, limit=10, order_by=[("Display Score", True)])
        assert [row[0] for row in run(conn, by_alias).rows] == [3, 4, 1, 5]


# ---------------------------------------------------------------------------- read-only sessions


def test_read_only_statements_block_and_restore_writes(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    t = d.quote_ident(create_simple_table(engine_case))
    insert = f"INSERT INTO {t} (id, name) VALUES (100, 'x')"
    count = f"SELECT count(*) FROM {t}"
    with engine_case.engine.connect() as conn:
        try:
            for statement in d.read_only_statements(True):
                run(conn, statement)
            commit(conn)  # the setting applies to the next transaction
            assert run(conn, count).scalar() == 5  # reads still work
            rollback(conn)
            with pytest.raises(DB_ERRORS):
                run(conn, insert)
            rollback(conn)
        finally:
            for statement in d.read_only_statements(False):
                run(conn, statement)
            commit(conn)
        run(conn, insert)
        commit(conn)
        assert run(conn, count).scalar() == 6


# ---------------------------------------------------------------------------- literals

TYPED_DDL = {
    "postgresql": (
        "CREATE TABLE {t} (id integer PRIMARY KEY, s text, i bigint, f double precision, "
        "n numeric(12,4), b boolean, ts timestamp, d date, tm time, j jsonb, u uuid, bl bytea)"
    ),
    "mysql": (
        "CREATE TABLE {t} (id integer PRIMARY KEY, s text, i bigint, f double, "
        "n decimal(12,4), b boolean, ts datetime(6), d date, tm time(6), j json, u char(36), "
        "bl blob)"
    ),
    "sqlite": (
        "CREATE TABLE {t} (id integer PRIMARY KEY, s text, i integer, f real, n numeric, "
        "b boolean, ts text, d text, tm text, j text, u text, bl blob)"
    ),
}

STRINGS = [
    "plain",
    "",
    "O'Reilly",
    "two''quotes",
    "back\\slash",
    "trailing\\",
    "\\'",
    "new\nline\r\nand\ttab",
    "unicode ✓ é 😀",
    "100% :named ?q $1 -- not a comment; /* nor this */",
    "semi;colon",
]


@pytest.mark.parametrize("value", STRINGS)
def test_string_literals_round_trip(engine_case: EngineCase, value: str) -> None:
    d = engine_case.dialect
    table = d.quote_ident(engine_case.new_table_name())
    with engine_case.engine.begin() as conn:
        run(conn, TYPED_DDL[d.id.value].format(t=table))
        run(conn, f"INSERT INTO {table} (id, s) VALUES (1, {d.render_literal(value)})")
        assert run(conn, f"SELECT s FROM {table}").scalar() == value


def normalise(value: Any, kind: str) -> Any:
    if isinstance(value, timedelta):  # PyMySQL returns TIME columns as timedelta
        value = (datetime.min + value).time()
    match kind:
        case "bytes":
            return bytes(value)
        case "json":
            return json.loads(value) if isinstance(value, str) else value
        case "decimal":
            return Decimal(str(value))
        case "bool":
            return bool(value)
        case "text":
            return str(value)
    return value


def test_typed_literals_round_trip(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    table = d.quote_ident(engine_case.new_table_name())
    as_text = "text" if d is SQLITE else "plain"  # SQLite keeps what it was given, as text
    values: dict[str, tuple[Any, str]] = {
        "i": (-(2**40), "plain"),
        "f": (0.1, "plain"),
        "n": (Decimal("12.5000"), "decimal"),
        "b": (True, "bool"),
        "d": (date(2024, 2, 29), as_text),
        "ts": (datetime(2024, 1, 2, 3, 4, 5, 123456), as_text),
        "tm": (time(3, 4, 5), as_text),
        "j": ({"a": [1, "é"], "b": None}, "json"),
        "u": (UUID("12345678-1234-5678-1234-567812345678"), "text"),
        "bl": (b"\x00\x01\xff'\\", "bytes"),
    }
    columns = ", ".join(values)
    literals = ", ".join(d.render_literal(v) for v, _ in values.values())
    with engine_case.engine.begin() as conn:
        run(conn, TYPED_DDL[d.id.value].format(t=table))
        run(conn, f"INSERT INTO {table} (id, {columns}) VALUES (1, {literals})")
        row = run(conn, f"SELECT {columns} FROM {table}").one()
    for column, stored in zip(values, row, strict=True):
        expected, kind = values[column]
        if kind == "text":
            expected = d.render_literal(expected).strip("'") if column != "u" else str(expected)
        assert normalise(stored, kind) == expected, column


def test_null_and_false_literals(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    table = d.quote_ident(engine_case.new_table_name())
    with engine_case.engine.begin() as conn:
        run(conn, TYPED_DDL[d.id.value].format(t=table))
        run(
            conn,
            f"INSERT INTO {table} (id, s, b) "
            f"VALUES (1, {d.render_literal(None)}, {d.render_literal(False)})",
        )
        s, b = run(conn, f"SELECT s, b FROM {table}").one()
    assert s is None
    assert bool(b) is False


# ---------------------------------------------------------------------------- scripts

SCRIPTS = {
    "postgresql": """
        CREATE TABLE {t} (id integer PRIMARY KEY, note text);
        CREATE TABLE {t}_log (msg text);
        -- the body below contains semicolons that must not split the statement
        CREATE FUNCTION {t}_fn() RETURNS trigger AS $body$
        BEGIN
            INSERT INTO {t}_log VALUES ('inserted; ' || NEW.id);
            RETURN NEW;
        END;
        $body$ LANGUAGE plpgsql;
        CREATE TRIGGER {t}_trg AFTER INSERT ON {t} FOR EACH ROW EXECUTE FUNCTION {t}_fn();
        INSERT INTO {t} VALUES (1, 'a;b'), (2, 'it''s');
    """,
    "mysql": """
        CREATE TABLE {t} (id integer PRIMARY KEY, note text);
        CREATE TABLE {t}_log (msg text);
        CREATE TRIGGER {t}_trg AFTER INSERT ON {t} FOR EACH ROW
        BEGIN
            DECLARE prefix VARCHAR(20);
            IF NEW.id > 0 THEN
                SET prefix = 'inserted; ';
            END IF;
            INSERT INTO {t}_log VALUES (CONCAT(prefix, NEW.id));
        END;
        INSERT INTO {t} VALUES (1, 'a;b'), (2, 'it''s'); # trailing; comment
    """,
    "sqlite": """
        CREATE TABLE {t} (id integer PRIMARY KEY, note text);
        CREATE TABLE {t}_log (msg text);
        CREATE TRIGGER {t}_trg AFTER INSERT ON {t}
        BEGIN
            INSERT INTO {t}_log VALUES (
                CASE WHEN new.id > 0 THEN 'inserted; ' ELSE '' END || new.id
            );
        END;
        INSERT INTO {t} VALUES (1, 'a;b'), (2, 'it''s');
    """,
}


def test_split_script_with_trigger_bodies_executes_statement_by_statement(
    engine_case: EngineCase,
) -> None:
    d = engine_case.dialect
    t = engine_case.new_table_name()
    engine_case.track_table(f"{t}_log")
    if d is POSTGRESQL:
        engine_case.add_cleanup(f"DROP FUNCTION IF EXISTS {t}_fn()")
    statements = split_statements(SCRIPTS[d.id.value].format(t=t), d)
    assert len(statements) == (5 if d is POSTGRESQL else 4)
    with engine_case.engine.begin() as conn:
        for statement in statements:
            run(conn, statement.body)
        notes = [r[0] for r in run(conn, f"SELECT note FROM {t} ORDER BY id").rows]
        log = sorted(r[0] for r in run(conn, f"SELECT msg FROM {t}_log").rows)
    assert notes == ["a;b", "it's"]
    assert log == ["inserted; 1", "inserted; 2"]


# ---------------------------------------------------------------------------- server info


def test_server_info_matches_the_live_server(engine_case: EngineCase) -> None:
    d = engine_case.dialect
    t = d.quote_ident(create_simple_table(engine_case))
    with engine_case.engine.connect() as conn:
        raw = run(conn, "SELECT sqlite_version()" if d is SQLITE else "SELECT version()").scalar()
        info = d.server_info(raw)
        assert info.version
        if d is MYSQL:
            assert info.flavor == ("mariadb" if "mariadb" in raw.lower() else "mysql")
        else:
            assert info.flavor == d.id.value
        if info.supports_returning:
            assert (
                run(conn, f"INSERT INTO {t} (id, name) VALUES (50, 'r') RETURNING id").scalar()
                == 50
            )
        rollback(conn)


# ---------------------------------------------------------------------------- translation

EXPECTED = [("1-Alice", 10), ("5-alice", 5)]
TRANSLATION_CASES = {
    "postgresql": (
        POSTGRESQL,
        "SELECT CAST(id AS TEXT) || '-' || name AS label, COALESCE(score, 0) AS s FROM {t} "
        "WHERE name ILIKE 'a%' ORDER BY s DESC, id LIMIT 2 OFFSET 0",
    ),
    "mysql": (
        MYSQL,
        "SELECT CONCAT(id, '-', name) AS label, IFNULL(score, 0) AS s FROM {t} "
        "WHERE LOWER(name) LIKE 'a%' ORDER BY s DESC, id LIMIT 2",
    ),
    "sqlite": (
        SQLITE,
        "SELECT id || '-' || name AS label, IFNULL(score, 0) AS s FROM {t} "
        "WHERE lower(name) LIKE 'a%' ORDER BY s DESC, id LIMIT 2",
    ),
}


@pytest.mark.parametrize("source_name", list(TRANSLATION_CASES))
def test_translated_queries_run_and_agree(engine_case: EngineCase, source_name: str) -> None:
    d = engine_case.dialect
    source, query = TRANSLATION_CASES[source_name]
    table = create_simple_table(engine_case)
    translated = translate(query.format(t=table), source=source, target=d)
    assert translated.warnings == ()
    with engine_case.engine.connect() as conn:
        rows = [(label, int(score)) for label, score in run(conn, translated.sql.rstrip(";")).rows]
    assert rows == EXPECTED
