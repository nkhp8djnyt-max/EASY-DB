from __future__ import annotations

import threading
from pathlib import Path

import pytest

from easydbms.core.db import ConnectionFailed, DatabaseFileError
from easydbms.core.session import Session, SessionState

from .conftest import Events, ScriptedClient, file_config, server_config, sqlite_file, state_of


def test_connect_and_disconnect_a_real_sqlite_file(tmp_path: Path) -> None:
    events = Events()
    session = Session(file_config(sqlite_file(tmp_path)), events)
    assert state_of(session) is SessionState.DISCONNECTED
    assert session.client is None

    session.connect()
    assert state_of(session) is SessionState.READY
    assert session.client is not None
    assert session.client.execute("SELECT 1").rows == ((1,),)
    assert session.server_info is not None
    assert session.server_info.flavor == "sqlite"
    assert events.states() == ["connecting", "ready"]
    assert events.items[-1].server_info is session.server_info

    session.disconnect()
    assert state_of(session) is SessionState.DISCONNECTED
    assert session.client is None
    assert events.states() == ["connecting", "ready", "disconnected"]
    session.disconnect()  # nothing to do, nothing announced
    assert events.states() == ["connecting", "ready", "disconnected"]


def test_failures_are_recorded_not_raised_and_can_be_retried(tmp_path: Path) -> None:
    events = Events()
    path = tmp_path / "later.db"
    session = Session(file_config(path), events)
    session.connect()
    assert state_of(session) is SessionState.ERROR
    assert isinstance(session.error, DatabaseFileError)
    assert session.client is None
    assert events.states() == ["connecting", "error"]
    assert events.items[-1].error is session.error

    sqlite_file(tmp_path, "later.db")  # the user fixes the problem
    session.connect()
    assert state_of(session) is SessionState.READY
    assert session.error is None


def test_connecting_twice_is_a_no_op(tmp_path: Path) -> None:
    events = Events()
    session = Session(file_config(sqlite_file(tmp_path)), events)
    session.connect()
    first = session.client
    session.connect()
    assert session.client is first
    assert events.states() == ["connecting", "ready"]


def test_environment_placeholders_are_resolved_at_connect_time(tmp_path: Path) -> None:
    sqlite_file(tmp_path, "env.db")
    config = file_config("${DATA_DIR}/env.db")
    session = Session(config)
    session.connect(environ={"DATA_DIR": str(tmp_path)})
    assert state_of(session) is SessionState.READY
    assert config.path == "${DATA_DIR}/env.db"  # the saved config keeps the placeholder


def test_undefined_variables_and_invalid_expansions_become_errors() -> None:
    session = Session(file_config("${NOPE}/x.db"))
    session.connect(environ={})
    assert state_of(session) is SessionState.ERROR
    assert "NOPE" in str(session.error)

    blank_host = Session(server_config(host="${H:-x}"), client_factory=ScriptedClient)
    blank_host.connect(environ={"H": ""})  # falls back to "x": fine
    assert blank_host.state is SessionState.READY

    invalid = Session(server_config(host="${H}"), client_factory=ScriptedClient)
    invalid.connect(environ={"H": "   "})  # whitespace-only host fails validation
    assert invalid.state is SessionState.ERROR
    assert isinstance(invalid.error, ValueError)


def test_password_and_resolved_config_reach_the_client() -> None:
    session = Session(server_config(user="${U}"), client_factory=ScriptedClient)
    session.connect("pw-${P}", environ={"U": "alice", "P": "x"})
    (client,) = ScriptedClient.created
    assert client.password == "pw-x"
    assert client.config.user == "alice"  # type: ignore[union-attr]


def test_a_client_that_fails_to_connect_is_left_disconnected() -> None:
    def factory(config: object, password: str | None) -> ScriptedClient:
        client = ScriptedClient(config, password)  # type: ignore[arg-type]
        client.fail_with = ConnectionFailed("nope")
        return client

    session = Session(server_config(), client_factory=factory)
    session.connect()
    assert state_of(session) is SessionState.ERROR
    assert str(session.error) == "nope"
    assert not ScriptedClient.created[0].is_connected


def test_unexpected_exceptions_leave_a_clean_error_state_and_propagate() -> None:
    def factory(config: object, password: str | None) -> ScriptedClient:
        raise RuntimeError("boom")

    events = Events()
    session = Session(server_config(), events, client_factory=factory)
    with pytest.raises(RuntimeError, match="boom"):
        session.connect()
    assert state_of(session) is SessionState.ERROR
    assert events.states() == ["connecting", "error"]


def test_disconnect_while_connecting_discards_the_late_result() -> None:
    gates: list[ScriptedClient] = []

    def factory(config: object, password: str | None) -> ScriptedClient:
        client = ScriptedClient(config, password)  # type: ignore[arg-type]
        client.gate.clear()
        gates.append(client)
        return client

    events = Events()
    session = Session(server_config(), events, client_factory=factory)
    worker = threading.Thread(target=session.connect)
    worker.start()
    while not gates or state_of(session) is not SessionState.CONNECTING:
        pass
    session.disconnect()
    assert state_of(session) is SessionState.DISCONNECTED
    gates[0].gate.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert state_of(session) is SessionState.DISCONNECTED  # not resurrected
    assert session.client is None
    assert gates[0].closed
    assert "ready" not in events.states()


def test_fail_records_an_error_before_any_connect() -> None:
    events = Events()
    session = Session(server_config(), events)
    session.fail(RuntimeError("password store locked"))
    assert state_of(session) is SessionState.ERROR
    assert str(session.error) == "password store locked"
    assert events.states() == ["error"]
