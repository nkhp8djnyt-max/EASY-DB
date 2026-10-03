from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path

import pytest

from easydbms.core.connections import (
    ConnectionNotFoundError,
    ConnectionStore,
    MemorySecretStore,
    VaultSecretStore,
)
from easydbms.core.db import ConnectionFailed, ConnectRuntime, DatabaseFileError
from easydbms.core.session import (
    ActiveChanged,
    ConnectionManager,
    Session,
    SessionState,
)

from .conftest import Events, ScriptedClient, file_config, server_config, sqlite_file


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.events = Events()
        self.store = ConnectionStore(tmp_path / "connections.json")
        self.secrets = MemorySecretStore()
        self.manager = ConnectionManager(self.store, self.secrets, listener=self.events, environ={})

    def add_file(self, tmp_path: Path, name: str) -> str:
        config = file_config(sqlite_file(tmp_path, f"{name}.db"), name)
        self.store.save(config)
        return config.id

    def wait(self, future: Future[Session]) -> Session:
        return future.result(timeout=10)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Fixture]:
    fixture = Fixture(tmp_path)
    yield fixture
    fixture.manager.close_all()


def test_activate_connects_lazily_and_announces_everything(env: Fixture, tmp_path: Path) -> None:
    cid = env.add_file(tmp_path, "one")
    assert env.manager.active_id is None
    assert env.manager.state_of(cid) is SessionState.DISCONNECTED  # nothing opened yet

    session = env.wait(env.manager.activate(cid))
    assert session.state is SessionState.READY
    assert env.manager.active_id == cid
    assert env.manager.active is session
    assert env.events.states() == ["connecting", "ready"]
    assert isinstance(env.events.items[0], ActiveChanged)  # the switch is announced first
    assert env.events.items[0].connection_id == cid


def test_several_connections_stay_open_and_switching_does_not_reconnect(
    env: Fixture, tmp_path: Path
) -> None:
    a, b = env.add_file(tmp_path, "a"), env.add_file(tmp_path, "b")
    session_a = env.wait(env.manager.activate(a))
    client_a = session_a.client
    env.wait(env.manager.activate(b))
    assert env.manager.active_id == b
    assert env.manager.state_of(a) is SessionState.READY
    assert env.manager.state_of(b) is SessionState.READY

    again = env.wait(env.manager.activate(a))  # back to the first: already open
    assert again is session_a
    assert again.client is client_a
    assert env.events.states() == ["connecting", "ready", "connecting", "ready"]
    changes = [e.connection_id for e in env.events.items if isinstance(e, ActiveChanged)]
    assert changes == [a, b, a]


def test_activating_the_active_connection_again_announces_nothing(
    env: Fixture, tmp_path: Path
) -> None:
    cid = env.add_file(tmp_path, "one")
    env.wait(env.manager.activate(cid))
    before = len(env.events.items)
    env.wait(env.manager.activate(cid))
    assert len(env.events.items) == before


def test_a_failed_connection_resolves_to_an_error_session_and_can_be_retried(
    env: Fixture, tmp_path: Path
) -> None:
    config = file_config(tmp_path / "missing.db", "missing")
    env.store.save(config)
    session = env.wait(env.manager.activate(config.id))
    assert session.state is SessionState.ERROR
    assert isinstance(session.error, DatabaseFileError)
    assert env.manager.active_id == config.id  # still the active (broken) selection

    sqlite_file(tmp_path, "missing.db")
    session = env.wait(env.manager.activate(config.id))  # selecting it again retries
    assert session.state is SessionState.READY


def test_concurrent_connect_requests_share_one_attempt(tmp_path: Path) -> None:
    gate = threading.Event()

    def factory(config: object, password: str | None, runtime: ConnectRuntime) -> ScriptedClient:
        client = ScriptedClient(config, password, runtime)  # type: ignore[arg-type]
        client.gate = gate
        return client

    store = ConnectionStore(tmp_path / "c.json")
    config = server_config()
    store.save(config)
    manager = ConnectionManager(store, MemorySecretStore(), client_factory=factory, environ={})
    try:
        first = manager.connect(config.id)
        second = manager.connect(config.id)
        assert first is second
        gate.set()
        assert first.result(timeout=5).state is SessionState.READY
        assert len(ScriptedClient.created) == 1
    finally:
        gate.set()
        manager.close_all()


# ---------------------------------------------------------------------------- passwords


def scripted_manager(
    tmp_path: Path, secrets: MemorySecretStore | VaultSecretStore
) -> tuple[ConnectionManager, ConnectionStore, Events]:
    events = Events()
    store = ConnectionStore(tmp_path / "c.json")
    manager = ConnectionManager(
        store, secrets, listener=events, environ={}, client_factory=ScriptedClient
    )
    return manager, store, events


def test_the_stored_password_is_used(tmp_path: Path) -> None:
    secrets = MemorySecretStore()
    manager, store, _ = scripted_manager(tmp_path, secrets)
    config = server_config()
    store.save(config)
    secrets.set(config.id, "stored-pw")
    try:
        manager.activate(config.id).result(timeout=5)
        assert ScriptedClient.created[0].password == "stored-pw"
    finally:
        manager.close_all()


def test_a_typed_password_wins_and_is_remembered_for_the_run(tmp_path: Path) -> None:
    secrets = MemorySecretStore()
    manager, store, _ = scripted_manager(tmp_path, secrets)
    config = server_config(save_password=False)
    store.save(config)
    secrets.set(config.id, "should-not-be-read")
    try:
        assert manager.needs_password(config.id)
        manager.activate(config.id, password="typed").result(timeout=5)
        assert ScriptedClient.created[0].password == "typed"
        assert not manager.needs_password(config.id)  # not asked again this run

        manager.disconnect(config.id)
        manager.activate(config.id).result(timeout=5)
        assert ScriptedClient.created[1].password == "typed"
    finally:
        manager.close_all()


def test_connections_that_keep_no_password_never_touch_the_secret_store(tmp_path: Path) -> None:
    class Exploding(MemorySecretStore):
        def get(self, connection_id: str, field: str = "password") -> str | None:
            raise AssertionError("must not be read")

    manager, store, _ = scripted_manager(tmp_path, Exploding())
    config = server_config(save_password=False)
    store.save(config)
    try:
        manager.activate(config.id).result(timeout=5)
        assert ScriptedClient.created[0].password is None
    finally:
        manager.close_all()


def test_file_connections_do_not_need_passwords(env: Fixture, tmp_path: Path) -> None:
    cid = env.add_file(tmp_path, "f")
    assert not env.manager.needs_password(cid)


def test_a_locked_vault_becomes_a_session_error(tmp_path: Path) -> None:
    vault = VaultSecretStore(tmp_path / "vault.json", scrypt_n=2**10)
    manager, store, events = scripted_manager(tmp_path, vault)
    config = server_config()
    store.save(config)
    try:
        session = manager.activate(config.id).result(timeout=5)
        assert session.state is SessionState.ERROR
        assert "locked" in str(session.error)
        assert ScriptedClient.created == []
        assert events.states() == ["error"]

        vault.unlock("master")
        vault.set(config.id, "pw")
        session = manager.activate(config.id).result(timeout=5)
        assert session.state is SessionState.READY
        assert ScriptedClient.created[0].password == "pw"
    finally:
        manager.close_all()


# ---------------------------------------------------------------------------- lifecycle


def test_unknown_connection_ids_are_rejected(env: Fixture) -> None:
    with pytest.raises(ConnectionNotFoundError):
        env.manager.activate("nope")
    with pytest.raises(ConnectionNotFoundError):
        env.manager.session("nope")
    assert env.manager.active_id is None


def test_disconnect_keeps_the_session_but_closes_the_client(env: Fixture, tmp_path: Path) -> None:
    cid = env.add_file(tmp_path, "one")
    session = env.wait(env.manager.activate(cid))
    env.manager.disconnect(cid)
    assert session.state is SessionState.DISCONNECTED
    assert env.manager.active_id == cid
    env.manager.disconnect("never-opened")  # harmless


def test_forget_drops_the_session_and_clears_the_active_selection(
    env: Fixture, tmp_path: Path
) -> None:
    a, b = env.add_file(tmp_path, "a"), env.add_file(tmp_path, "b")
    session_a = env.wait(env.manager.activate(a))
    env.wait(env.manager.activate(b))
    env.manager.forget(a)  # not active: nothing announced about the selection
    assert session_a.state is SessionState.DISCONNECTED
    assert env.manager.active_id == b

    env.manager.forget(b)
    assert env.manager.active_id is None
    assert env.manager.active is None
    assert isinstance(env.events.items[-1], ActiveChanged)
    assert env.events.items[-1].connection_id is None
    # a forgotten connection starts from scratch next time
    assert env.manager.session(a) is not session_a


def test_forget_during_connect_leaves_no_zombie(tmp_path: Path) -> None:
    gate = threading.Event()

    def factory(config: object, password: str | None, runtime: ConnectRuntime) -> ScriptedClient:
        client = ScriptedClient(config, password, runtime)  # type: ignore[arg-type]
        client.gate = gate
        gate.clear()
        return client

    store = ConnectionStore(tmp_path / "c.json")
    config = server_config()
    store.save(config)
    manager = ConnectionManager(store, MemorySecretStore(), client_factory=factory, environ={})
    future = manager.activate(config.id)
    while not ScriptedClient.created:
        pass
    manager.forget(config.id)
    gate.set()
    session = future.result(timeout=5)
    assert session.state is SessionState.DISCONNECTED
    assert session.client is None
    assert ScriptedClient.created[0].closed
    manager.close_all()


def test_close_all_disconnects_everything(env: Fixture, tmp_path: Path) -> None:
    sessions = [env.wait(env.manager.activate(env.add_file(tmp_path, n))) for n in "ab"]
    env.manager.close_all()
    assert [s.state for s in sessions] == [SessionState.DISCONNECTED] * 2
    assert env.manager.active_id is None


def test_a_connection_error_object_is_delivered_to_listeners(tmp_path: Path) -> None:
    def factory(config: object, password: str | None, runtime: ConnectRuntime) -> ScriptedClient:
        client = ScriptedClient(config, password, runtime)  # type: ignore[arg-type]
        client.fail_with = ConnectionFailed("server said no")
        return client

    events = Events()
    store = ConnectionStore(tmp_path / "c.json")
    config = server_config()
    store.save(config)
    manager = ConnectionManager(
        store, MemorySecretStore(), listener=events, client_factory=factory, environ={}
    )
    try:
        manager.activate(config.id).result(timeout=5)
        error = events.items[-1].error
        assert isinstance(error, ConnectionFailed)
        assert str(error) == "server said no"
    finally:
        manager.close_all()


def test_manager_loads_the_schema_after_connecting_when_asked(tmp_path: Path) -> None:
    import sqlite3

    from easydbms.core.session import SchemaChanged, SchemaState

    events = Events()
    store = ConnectionStore(tmp_path / "connections.json")
    path = sqlite_file(tmp_path, "s.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    con.close()
    config = file_config(path, "s")
    store.save(config)
    manager = ConnectionManager(
        store, MemorySecretStore(), listener=events, environ={}, load_schema=True
    )
    try:
        session = manager.activate(config.id).result(timeout=10)
        deadline = threading.Event()
        for _ in range(200):
            if session.schema_state is SchemaState.READY:
                break
            deadline.wait(0.05)
        assert session.schema is not None
        assert [t.name for t in session.schema.tables] == ["t"]
        states = [e.state for e in events.items if isinstance(e, SchemaChanged)]
        assert states == [SchemaState.LOADING, SchemaState.READY]
    finally:
        manager.close_all()
