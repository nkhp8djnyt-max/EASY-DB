"""Running scripts through Session/ScriptRun against every engine."""

from __future__ import annotations

import threading
import time

import pytest

from easydbms.core.db import DatabaseClient
from easydbms.core.dialects import split_statements
from easydbms.core.queries import Outcome, ScriptRun, StatementOutcome
from easydbms.core.session import Session
from tests.core.conftest import Target

from .test_clients import SLOW_QUERY

pytestmark = pytest.mark.integration


def run(client: DatabaseClient, sql: str, limit: int = 100) -> list[StatementOutcome]:
    statements = split_statements(sql, client.dialect)
    handle = ScriptRun(client, statements, limit).start_on(
        lambda fn: threading.Thread(target=fn).start()
    )
    return handle.future.result(timeout=30)


def test_every_statement_gets_an_outcome(target: Target) -> None:
    table = target.quote(target.table())
    with target.client() as client:
        outcomes = run(
            client,
            f"CREATE TABLE {table} (a integer); INSERT INTO {table} VALUES (1), (2), (3); "
            f"SELECT a FROM {table} ORDER BY a; UPDATE {table} SET a = a + 1;",
        )
    assert [o.outcome for o in outcomes] == [Outcome.OK] * 4
    assert outcomes[1].result is not None
    assert outcomes[1].result.rowcount == 3
    assert outcomes[2].result is not None
    assert outcomes[2].result.rows == ((1,), (2,), (3,))
    assert outcomes[3].result is not None
    assert outcomes[3].result.rowcount == 3


def test_an_error_stops_the_script_and_skips_the_rest(target: Target) -> None:
    with target.client() as client:
        outcomes = run(client, "SELECT 1; SELEC 2; SELECT 3")
        assert [o.outcome for o in outcomes] == [Outcome.OK, Outcome.ERROR, Outcome.SKIPPED]
        assert outcomes[1].error is not None
        assert client.execute("SELECT 4").rows == ((4,),)  # the connection is fine


def test_the_row_limit_applies_per_statement(target: Target) -> None:
    table = target.quote(target.table())
    values = ", ".join(f"({i})" for i in range(1, 51))
    with target.client() as client:
        outcomes = run(
            client,
            f"CREATE TABLE {table} (a integer); INSERT INTO {table} VALUES {values}; "
            f"SELECT a FROM {table} ORDER BY a",
            limit=10,
        )
    result = outcomes[2].result
    assert result is not None
    assert len(result.rows) == 10
    assert result.truncated


def test_cancel_aborts_the_running_statement_and_skips_the_rest(target: Target) -> None:
    with target.client() as client:
        statements = split_statements(f"{SLOW_QUERY[target.name]}; SELECT 1", client.dialect)
        seen: list[int] = []
        handle = ScriptRun(client, statements, 100, lambda i, o: seen.append(i))
        handle.start_on(lambda fn: threading.Thread(target=fn).start())
        time.sleep(0.5)
        handle.cancel()
        outcomes = handle.future.result(timeout=15)
        assert [o.outcome for o in outcomes] == [Outcome.CANCELLED, Outcome.SKIPPED]
        assert seen == [0, 1]
        assert client.execute("SELECT 2").rows == ((2,),)


def test_a_session_runs_scripts_on_its_own_lane_in_order(target: Target) -> None:
    session = Session(target.config)
    session.connect(target.password)
    try:
        events: list[str] = []
        first = session.run_script(
            split_statements("SELECT 1", session.config.dialect_impl),
            10,
            lambda i, o: events.append("a"),
        )
        second = session.run_script(
            split_statements("SELECT 2", session.config.dialect_impl),
            10,
            lambda i, o: events.append("b"),
        )
        first.future.result(timeout=10)
        second.future.result(timeout=10)
        assert events == ["a", "b"]
    finally:
        session.disconnect()


def test_running_without_a_connection_is_an_error(target: Target) -> None:
    from easydbms.core.db import NotConnectedError

    session = Session(target.config)
    with pytest.raises(NotConnectedError):
        session.run_script([], 10)


def test_postgres_limit_is_streamed_not_buffered(target: Target) -> None:
    if target.name != "postgresql":
        pytest.skip("PostgreSQL streaming")
    table = target.quote(target.table())
    with target.client() as client:
        client.execute(
            f"CREATE TABLE {table} AS SELECT g AS id, repeat('x', 200) AS pad "
            "FROM generate_series(1, 400000) g"
        )
        started = time.perf_counter()
        result = client.execute(f"SELECT * FROM {table}", max_rows=5)
        elapsed = time.perf_counter() - started
        assert len(result.rows) == 5
        assert result.truncated
        assert result.columns == ("id", "pad")
        assert elapsed < 1.5
        assert client.execute(f"SELECT count(*) FROM {table}").rows == ((400000,),)


def test_postgres_streaming_keeps_commands_and_empty_selects_working(target: Target) -> None:
    if target.name != "postgresql":
        pytest.skip("PostgreSQL streaming")
    table = target.quote(target.table())
    with target.client() as client:
        assert client.execute(f"CREATE TABLE {table} (a int)", max_rows=10).rows == ()
        empty = client.execute(f"SELECT * FROM {table}", max_rows=10)
        assert (empty.rows, empty.truncated) == ((), False)
        insert = client.execute(f"INSERT INTO {table} VALUES (1), (2)", max_rows=10)
        assert insert.rowcount == 2  # not streamed: INSERT is not a row-returning keyword
        returning = client.execute(f"INSERT INTO {table} VALUES (3) RETURNING a", max_rows=10)
        assert returning.rows == ((3,),)
