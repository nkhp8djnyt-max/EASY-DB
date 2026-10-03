from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from easydbms.core.connections import FileConnection
from easydbms.core.db import ConnectRuntime, NotConnectedError
from easydbms.core.schema import DatabaseSchema
from easydbms.core.session import SchemaChanged, SchemaState, Session, SessionState

from .conftest import Events, ScriptedClient, file_config, sqlite_file


def make_db(tmp_path: Path) -> Path:
    path = sqlite_file(tmp_path)
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE a (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id));"
    )
    con.close()
    return path


def schema_events(events: Events) -> list[SchemaChanged]:
    return [e for e in events.items if isinstance(e, SchemaChanged)]


def test_load_schema_emits_loading_then_ready(tmp_path: Path) -> None:
    events = Events()
    session = Session(file_config(make_db(tmp_path)), events)
    session.connect()
    assert session.schema_state is SchemaState.NONE
    assert session.schema is None

    session.load_schema().result(timeout=10)

    assert [e.state for e in schema_events(events)] == [SchemaState.LOADING, SchemaState.READY]
    schema = session.schema
    assert isinstance(schema, DatabaseSchema)
    assert [t.name for t in schema.tables] == ["a", "b"]
    assert session.schema_state is SchemaState.READY
    assert session.schema_version == 1
    assert schema_events(events)[-1].schema is schema
    session.disconnect()


def test_reload_bumps_the_version_and_keeps_the_old_schema_meanwhile(tmp_path: Path) -> None:
    session = Session(file_config(make_db(tmp_path)))
    session.connect()
    session.load_schema().result(timeout=10)
    first = session.schema
    session.load_schema().result(timeout=10)
    assert session.schema_version == 2
    assert session.schema is not first
    assert session.schema is not None
    assert first is not None
    assert [t.name for t in session.schema.tables] == [t.name for t in first.tables]
    session.disconnect()


def test_meta_lane_uses_its_own_connection(tmp_path: Path) -> None:
    session = Session(file_config(make_db(tmp_path)))
    session.connect()
    main = session.client
    seen: list[Any] = []
    session.run_on_meta(seen.append).result(timeout=10)
    assert seen[0] is not main
    assert seen[0].is_connected
    session.run_on_meta(seen.append).result(timeout=10)
    assert seen[1] is seen[0]  # opened once
    session.disconnect()
    assert not seen[0].is_connected


def test_meta_lane_works_while_the_query_lane_is_busy(tmp_path: Path) -> None:
    session = Session(file_config(make_db(tmp_path)))
    session.connect()
    client = session.client
    assert client is not None
    release = threading.Event()
    entered = threading.Event()
    original = client._run

    def slow(raw: Any, sql: str, max_rows: int | None) -> Any:
        entered.set()
        release.wait(timeout=10)
        return original(raw, "SELECT 1", max_rows)

    client._run = slow  # type: ignore[method-assign]
    from easydbms.core.dialects import split_statements

    run = session.run_script(split_statements("select 1;", client.dialect), 10)
    assert entered.wait(timeout=10)
    session.load_schema().result(timeout=10)  # does not wait for the busy query
    assert session.schema_state is SchemaState.READY
    release.set()
    run.future.result(timeout=10)
    session.disconnect()


def test_in_memory_sqlite_shares_the_only_connection() -> None:
    config = FileConnection.model_validate({"name": "mem", "path": ":memory:"})
    session = Session(config)
    session.connect()
    assert session.client is not None
    session.client.execute("CREATE TABLE only_here (id INTEGER PRIMARY KEY)")
    session.load_schema().result(timeout=10)
    assert session.schema is not None
    assert [t.name for t in session.schema.tables] == ["only_here"]
    session.disconnect()


def test_failure_becomes_error_state_not_an_exception(tmp_path: Path) -> None:
    events = Events()
    session = Session(file_config(make_db(tmp_path)), events)
    session.connect()
    client = session.client
    assert client is not None
    session.run_on_meta(lambda c: c.execute("DROP TABLE b")).result(timeout=10)

    original_execute = type(client).execute

    def broken(self: Any, sql: str, **kwargs: Any) -> Any:
        if "sqlite_master" in sql:
            raise RuntimeError("catalog unavailable")
        return original_execute(self, sql, **kwargs)

    type(client).execute = broken  # type: ignore[method-assign]
    try:
        session.load_schema().result(timeout=10)
    finally:
        type(client).execute = original_execute  # type: ignore[method-assign]
    assert session.schema_state is SchemaState.ERROR
    assert isinstance(session.schema_error, RuntimeError)
    assert schema_events(events)[-1].state is SchemaState.ERROR
    assert schema_events(events)[-1].error is session.schema_error
    session.disconnect()


def test_disconnect_discards_a_result_in_flight(tmp_path: Path) -> None:
    session = Session(file_config(make_db(tmp_path)))
    session.connect()
    gate = threading.Event()
    started = threading.Event()

    def blocked(client: Any) -> None:
        started.set()
        gate.wait(timeout=10)

    session.run_on_meta(blocked)
    started.wait(timeout=10)
    future = session.load_schema()  # queued behind the blocked job
    session.disconnect()
    gate.set()
    with pytest.raises(Exception):  # noqa: B017, PT011 - cancelled or NotConnectedError
        future.result(timeout=10)
    assert session.schema is None
    assert session.schema_state is SchemaState.NONE


def test_requires_a_ready_session(tmp_path: Path) -> None:
    session = Session(file_config(make_db(tmp_path)))
    with pytest.raises(NotConnectedError):
        session.load_schema()
    with pytest.raises(NotConnectedError):
        session.run_on_meta(lambda c: None)
    assert session.state is SessionState.DISCONNECTED


def test_disconnect_closes_both_connections(tmp_path: Path) -> None:
    created: list[ScriptedClient] = []

    def factory(config: Any, password: str | None, runtime: ConnectRuntime) -> ScriptedClient:
        client = ScriptedClient(config, password, runtime)
        created.append(client)
        return client

    session = Session(file_config(make_db(tmp_path)), client_factory=factory)
    session.connect()
    session.run_on_meta(lambda c: None).result(timeout=10)
    assert len(created) == 2
    session.disconnect()
    assert all(c.closed for c in created)
