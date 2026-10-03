"""Behaviour every client must share, checked against real databases."""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from easydbms.core.connections import FileConnection, ServerConnection, replace
from easydbms.core.db import (
    ConnectionLost,
    NotConnectedError,
    QueryCancelled,
    QueryError,
    ReadOnlyViolation,
)

from .conftest import Target

pytestmark = pytest.mark.integration

SLOW_QUERY = {
    "postgresql": "SELECT pg_sleep(30)",
    "mysql": "SELECT SLEEP(30)",
    "sqlite": (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    ),
}


def make_table(target: Target, client: Any, rows: int = 0) -> str:
    """A table ``(id int primary key, name text)`` with ``rows`` generated rows."""
    table = target.table()
    quoted = target.quote(table)
    client.execute(f"CREATE TABLE {quoted} (id integer PRIMARY KEY, name varchar(100))")
    if rows:
        values = ", ".join(f"({i}, 'row {i}')" for i in range(1, rows + 1))
        client.execute(f"INSERT INTO {quoted} (id, name) VALUES {values}")
    return quoted


# ---------------------------------------------------------------------------- lifecycle


def test_connect_disconnect_lifecycle(target: Target) -> None:
    client = target.client()
    assert not client.is_connected
    assert client.server_info is None
    with pytest.raises(NotConnectedError):
        client.execute("SELECT 1")

    client.connect()
    client.connect()  # idempotent
    assert client.is_connected
    assert client.server_info is not None
    assert client.server_info.version
    assert client.dialect is target.config.dialect_impl

    client.disconnect()
    client.disconnect()  # idempotent
    assert not client.is_connected
    assert client.server_info is None
    with pytest.raises(NotConnectedError):
        client.execute("SELECT 1")


def test_context_manager_and_reconnect(target: Target) -> None:
    client = target.client()
    with client as connected:
        assert connected is client
        assert client.execute("SELECT 1").rows == ((1,),)
    assert not client.is_connected
    with client:
        assert client.execute("SELECT 2").rows == ((2,),)


def test_server_info_flavor(target: Target) -> None:
    with target.client() as client:
        info = client.server_info
        assert info is not None
        expected = {
            "postgresql": {"postgresql"},
            "mysql": {"mysql", "mariadb"},
            "sqlite": {"sqlite"},
        }
        assert info.flavor in expected[target.name]
        assert info.version[0] >= 3


# ---------------------------------------------------------------------------- execute


def test_select_result_shape(target: Target) -> None:
    with target.client() as client:
        result = client.execute("SELECT 1 AS one, 'two' AS two, NULL AS three")
    assert result.columns == ("one", "two", "three")
    assert result.rows == ((1, "two", None),)
    assert result.returns_rows
    assert result.rowcount is None
    assert not result.truncated
    assert result.duration >= 0


def test_percent_signs_in_sql_are_not_placeholders(target: Target) -> None:
    """The reason clients never pass a parameter container to the driver."""
    with target.client() as client:
        assert client.execute("SELECT '100%' AS p").rows == (("100%",),)
        like = client.execute("SELECT 'banana' LIKE '%nan%' AS m")
        assert like.rows[0][0] in (1, True)
        assert client.execute("SELECT 'a %s b %(x)s %% c' AS p").rows == (("a %s b %(x)s %% c",),)
        assert client.execute("SELECT ':name ? $1 @v' AS p").rows == ((":name ? $1 @v",),)


def test_commands_report_affected_rows(target: Target) -> None:
    with target.client() as client:
        table = make_table(target, client)
        other = target.quote(target.table())
        create = client.execute(f"CREATE TABLE {other} (id integer)")
        assert not create.returns_rows
        assert create.rowcount in (None, 0)
        client.execute(f"DROP TABLE {other}")
        insert = client.execute(f"INSERT INTO {table} VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        assert (insert.columns, insert.rows, insert.rowcount) == ((), (), 3)
        assert client.execute(f"UPDATE {table} SET name = 'x' WHERE id < 3").rowcount == 2
        assert client.execute(f"DELETE FROM {table} WHERE id = 99").rowcount == 0
        assert client.execute(f"DELETE FROM {table}").rowcount == 3


def test_autocommit_makes_changes_visible_to_other_connections(target: Target) -> None:
    with target.client() as writer, target.client() as reader:
        table = make_table(target, writer, rows=2)
        assert reader.execute(f"SELECT count(*) FROM {table}").rows == ((2,),)


@pytest.mark.parametrize(
    ("max_rows", "returned", "truncated"),
    [
        (None, 25, False),
        (0, 0, True),
        (1, 1, True),
        (24, 24, True),
        (25, 25, False),
        (26, 25, False),
        (1000, 25, False),
    ],
)
def test_max_rows(target: Target, max_rows: int | None, returned: int, truncated: bool) -> None:
    with target.client() as client:
        table = make_table(target, client, rows=25)
        result = client.execute(f"SELECT id FROM {table} ORDER BY id", max_rows=max_rows)
    assert len(result.rows) == returned
    assert result.truncated is truncated
    assert result.rows == tuple((i,) for i in range(1, returned + 1))


def test_max_rows_on_a_large_result_stops_early_and_keeps_the_connection_usable(
    target: Target,
) -> None:
    with target.client() as client:
        table = make_table(target, client, rows=3000)
        result = client.execute(f"SELECT id, name FROM {table} ORDER BY id", max_rows=10)
        assert len(result.rows) == 10
        assert result.truncated
        assert client.execute("SELECT 42").rows == ((42,),)


def test_negative_max_rows_is_rejected(target: Target) -> None:
    with target.client() as client, pytest.raises(ValueError, match="negative"):
        client.execute("SELECT 1", max_rows=-1)


def test_values_arrive_as_python_objects(target: Target) -> None:
    with target.client() as client:
        table = target.table()
        quoted = target.quote(table)
        types = {
            "postgresql": "n numeric(10,2), ts timestamp, b bytea, f double precision, t text",
            "mysql": "n decimal(10,2), ts datetime(6), b blob, f double, t text",
            "sqlite": "n numeric, ts text, b blob, f real, t text",
        }[target.name]
        literal = target.config.dialect_impl.render_literal
        client.execute(f"CREATE TABLE {quoted} (id integer, {types})")
        payload = b"\x00\xff"
        values = ", ".join(
            literal(value)
            for value in (
                Decimal("12.50"),
                datetime(2024, 1, 2, 3, 4, 5),
                payload,
                1.5,
                "héllo ✓ 😀",
            )
        )
        client.execute(f"INSERT INTO {quoted} VALUES (1, {values})")
        client.execute(f"INSERT INTO {quoted} (id) VALUES (2)")
        rows = client.execute(f"SELECT n, ts, b, f, t FROM {quoted} ORDER BY id").rows
    n, ts, b, f, t = rows[0]
    assert Decimal(str(n)) == Decimal("12.50")
    assert str(ts).startswith("2024-01-02")
    assert "03:04:05" in str(ts)
    assert bytes(b) == b"\x00\xff"
    assert f == 1.5
    assert t == "héllo ✓ 😀"
    assert rows[1] == (None, None, None, None, None)


# ---------------------------------------------------------------------------- errors


def test_syntax_errors_are_query_errors_and_the_connection_survives(target: Target) -> None:
    with target.client() as client:
        with pytest.raises(QueryError) as info:
            client.execute("SELEC 1")
        assert str(info.value)
        assert not isinstance(info.value, QueryCancelled)
        assert client.is_connected
        assert client.execute("SELECT 1").rows == ((1,),)


def test_missing_table_and_constraint_violations(target: Target) -> None:
    with target.client() as client:
        with pytest.raises(QueryError, match=r"(?i)does(n't| not) exist|no such table"):
            client.execute("SELECT * FROM erd_no_such_table_anywhere")
        table = make_table(target, client, rows=1)
        with pytest.raises(QueryError) as info:
            client.execute(f"INSERT INTO {table} (id, name) VALUES (1, 'duplicate')")
        assert info.value.code is not None or "unique" in str(info.value).lower()
        assert client.execute(f"SELECT count(*) FROM {table}").rows == ((1,),)


def test_error_position_where_the_server_provides_it(target: Target) -> None:
    if target.name != "postgresql":
        pytest.skip("only PostgreSQL reports an error position")
    with target.client() as client, pytest.raises(QueryError) as info:
        client.execute("SELECT 1, nope FROM (SELECT 1) AS t")
    assert info.value.position == len("SELECT 1, ") + 1
    assert info.value.code == "42703"


# ---------------------------------------------------------------------------- cancel


def run_in_thread(client: Any, sql: str) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def work() -> None:
        try:
            client.execute(sql)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    return thread, errors


def test_cancel_stops_a_running_statement(target: Target) -> None:
    import time

    with target.client() as client:
        thread, errors = run_in_thread(client, SLOW_QUERY[target.name])
        time.sleep(0.5)
        started = time.perf_counter()
        assert client.cancel() is True
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert time.perf_counter() - started < 5
        assert len(errors) == 1
        assert isinstance(errors[0], QueryCancelled)
        assert client.is_connected
        assert client.execute("SELECT 7").rows == ((7,),)


def test_cancel_when_idle_does_nothing(target: Target) -> None:
    client = target.client()
    assert client.cancel() is False  # not connected
    with client:
        assert client.cancel() is False  # connected but idle
        assert client.execute("SELECT 1").rows == ((1,),)


# ---------------------------------------------------------------------------- read-only


def test_read_only_connections_refuse_writes(target: Target) -> None:
    with target.client() as admin:
        table = make_table(target, admin, rows=2)
    read_only = replace(target.config, read_only=True)
    with target.client(read_only) as client:
        assert client.execute(f"SELECT count(*) FROM {table}").rows == ((2,),)
        for statement in (
            f"INSERT INTO {table} VALUES (99, 'no')",
            f"UPDATE {table} SET name = 'no'",
            f"DELETE FROM {table}",
        ):
            with pytest.raises(ReadOnlyViolation):
                client.execute(statement)
        assert client.execute(f"SELECT count(*) FROM {table}").rows == ((2,),)
    with target.client() as admin:  # a normal connection to the same database can still write
        assert admin.execute(f"DELETE FROM {table}").rowcount == 2


# ---------------------------------------------------------------------------- connection loss


def test_losing_the_connection_is_reported_and_recoverable(target: Target) -> None:
    if not target.is_server:
        pytest.skip("a file database cannot be disconnected from outside")
    with target.client() as victim, target.client() as killer:
        pid = victim.execute(
            "SELECT pg_backend_pid()" if target.name == "postgresql" else "SELECT CONNECTION_ID()"
        ).rows[0][0]
        if target.name == "postgresql":
            killer.execute(f"SELECT pg_terminate_backend({pid})")
        else:
            killer.execute(f"KILL CONNECTION {pid}")
        with pytest.raises(ConnectionLost):
            victim.execute("SELECT 1")
        assert not victim.is_connected
        with pytest.raises(NotConnectedError):
            victim.execute("SELECT 1")
        victim.connect()  # reconnecting works
        assert victim.execute("SELECT 1").rows == ((1,),)


# ---------------------------------------------------------------------------- concurrency


def test_concurrent_executes_are_serialised(target: Target) -> None:
    results: list[Any] = []
    errors: list[BaseException] = []
    with target.client() as client:

        def work(n: int) -> None:
            try:
                for _ in range(10):
                    results.append(client.execute(f"SELECT {n}").rows[0][0])
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert errors == []
    assert sorted(results) == sorted([n for n in range(4) for _ in range(10)])


# ---------------------------------------------------------------------------- engine specifics


def test_postgresql_options_and_application_name(target: Target) -> None:
    if target.name != "postgresql":
        pytest.skip("PostgreSQL only")
    assert isinstance(target.config, ServerConnection)
    with target.client() as client:
        assert client.execute("SHOW application_name").rows == (("EasyDBMS",),)
    named = replace(target.config, options={"application_name": "custom"})
    with target.client(named) as client:
        assert client.execute("SHOW application_name").rows == (("custom",),)


def test_sqlite_enforces_foreign_keys_and_supports_memory(target: Target) -> None:
    if target.name != "sqlite":
        pytest.skip("SQLite only")
    with target.client() as client:
        assert client.execute("PRAGMA foreign_keys").rows == ((1,),)
        client.execute("CREATE TABLE p (id integer PRIMARY KEY)")
        client.execute("CREATE TABLE c (pid integer REFERENCES p(id))")
        with pytest.raises(QueryError, match="FOREIGN KEY"):
            client.execute("INSERT INTO c VALUES (1)")
    memory = FileConnection.model_validate({"name": "m", "path": ":memory:"})
    with target.client(memory) as client:
        client.execute("CREATE TABLE t (a integer)")
        client.execute("INSERT INTO t VALUES (1)")
        assert client.execute("SELECT a FROM t").rows == ((1,),)
