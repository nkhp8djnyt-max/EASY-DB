"""Sessions and the manager with SSH tunnels, TLS key files and password files in play.

The database is a ``ScriptedClient`` (it records the route it was given); the SSH server and the
tunnel are real.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from easydbms.core.connections import (
    JUMP_PASSWORD,
    PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    ConnectionStore,
    MemorySecretStore,
    ServerConnection,
    SshAuth,
    SshConfig,
    SshHop,
    SslConfig,
    SslMode,
)
from easydbms.core.db import ConnectionFailed, ConnectRuntime, DatabaseClient
from easydbms.core.session import ConnectionManager, Session, SessionState
from easydbms.core.ssh import KnownHosts, SshHostKeyUnknown
from tests.core.ssh.conftest import config as ssh_config
from tests.core.ssh.conftest import hop
from tests.support.certs import write_pki
from tests.support.keys import write_key
from tests.support.ssh_server import EchoServer, FakeSshServer

from .conftest import Events, ScriptedClient, server_config, state_of

SECRETS = {SSH_PASSWORD: "wonderland"}


@pytest.fixture
def bastion() -> Iterator[FakeSshServer]:
    with FakeSshServer(users={"alice": "wonderland", "jumper": "jp"}) as running:
        yield running


@pytest.fixture
def known(tmp_path: Path, bastion: FakeSshServer) -> KnownHosts:
    hosts = KnownHosts(tmp_path / "known_hosts", extra=[])
    hosts.trust("127.0.0.1", bastion.port, bastion.host_key)
    return hosts


@pytest.fixture
def echo() -> Iterator[EchoServer]:
    with EchoServer() as running:
        yield running


def tunnelled(bastion: FakeSshServer, echo: EchoServer, **extra: Any) -> ServerConnection:
    return server_config(
        host="127.0.0.1", port=echo.port, ssh=ssh_config(bastion).model_dump(), **extra
    )


def refused(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    return False


# ---------------------------------------------------------------------------- Session


def test_both_connections_of_a_session_share_one_tunnel(
    bastion: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    session = Session(tunnelled(bastion, echo), client_factory=ScriptedClient, known_hosts=known)
    session.connect(secrets=SECRETS)
    assert state_of(session) is SessionState.READY
    tunnel = session.tunnel
    assert tunnel is not None
    assert tunnel.alive
    (main,) = ScriptedClient.created
    assert main._runtime.route is not None
    assert main._runtime.route.port == tunnel.local_port
    assert isinstance(main.config, ServerConnection)
    assert main.config.host == "127.0.0.1"
    assert bastion.connections == 1

    session.run_on_meta(lambda client: client.execute("SELECT 1")).result(timeout=5)
    assert len(ScriptedClient.created) == 2  # the meta lane's own connection ...
    assert ScriptedClient.created[1]._runtime.route == main._runtime.route  # ... same tunnel
    assert bastion.connections == 1  # no second login

    port = tunnel.local_port
    session.disconnect()
    assert session.tunnel is None
    assert not tunnel.alive
    assert refused(port)


def test_the_tunnel_is_closed_again_when_the_database_refuses(
    bastion: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    ports: list[int] = []

    def factory(config: object, password: str | None, runtime: ConnectRuntime) -> DatabaseClient:
        assert runtime.route is not None
        ports.append(runtime.route.port)
        client = ScriptedClient(config, password, runtime)  # type: ignore[arg-type]
        client.fail_with = ConnectionFailed("password authentication failed")
        return client

    session = Session(tunnelled(bastion, echo), client_factory=factory, known_hosts=known)
    session.connect(secrets=SECRETS)
    assert state_of(session) is SessionState.ERROR
    assert str(session.error) == "password authentication failed"
    assert session.tunnel is None
    assert refused(ports[0])


def test_disconnecting_while_connecting_still_closes_the_tunnel(
    bastion: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    gate = threading.Event()
    opened = threading.Event()
    ports: list[int] = []

    def factory(config: object, password: str | None, runtime: ConnectRuntime) -> DatabaseClient:
        assert runtime.route is not None
        ports.append(runtime.route.port)
        client = ScriptedClient(config, password, runtime)  # type: ignore[arg-type]
        client.gate = gate
        opened.set()
        return client

    session = Session(tunnelled(bastion, echo), client_factory=factory, known_hosts=known)
    worker = threading.Thread(target=lambda: session.connect(secrets=SECRETS))
    worker.start()
    assert opened.wait(5)
    session.disconnect()
    gate.set()
    worker.join(5)
    assert state_of(session) is SessionState.DISCONNECTED
    assert refused(ports[0])


def test_an_unknown_ssh_server_is_an_error_until_it_is_trusted(
    tmp_path: Path, bastion: FakeSshServer, echo: EchoServer
) -> None:
    fresh = KnownHosts(tmp_path / "fresh_hosts", extra=[])
    session = Session(tunnelled(bastion, echo), client_factory=ScriptedClient, known_hosts=fresh)
    session.connect(secrets=SECRETS)
    assert state_of(session) is SessionState.ERROR
    error = session.error
    assert isinstance(error, SshHostKeyUnknown)
    assert ScriptedClient.created == []  # nothing was sent to the database

    fresh.trust(error.host, error.port, error.key)
    session.connect(secrets=SECRETS)
    assert state_of(session) is SessionState.READY
    session.disconnect()


def test_a_session_without_a_known_hosts_store_cannot_use_ssh(
    bastion: FakeSshServer, echo: EchoServer
) -> None:
    session = Session(tunnelled(bastion, echo), client_factory=ScriptedClient)
    session.connect(secrets=SECRETS)
    assert state_of(session) is SessionState.ERROR
    assert "known-hosts" in str(session.error)


# ---------------------------------------------------------------------------- manager


class Env:
    def __init__(self, tmp_path: Path, known: KnownHosts, environ: dict[str, str]) -> None:
        self.events = Events()
        self.store = ConnectionStore(tmp_path / "connections.json")
        self.secrets = MemorySecretStore()
        self.manager = ConnectionManager(
            self.store,
            self.secrets,
            listener=self.events,
            environ=environ,
            client_factory=ScriptedClient,
            known_hosts=known,
        )

    def add(self, config: ServerConnection) -> str:
        self.store.save(config)
        return config.id


@pytest.fixture
def env(tmp_path: Path, known: KnownHosts) -> Iterator[Env]:
    # no PGPASSFILE: ~/.pgpass of whoever runs the tests must not matter
    nothing = {"PGPASSFILE": str(tmp_path / "nope"), "PGSERVICEFILE": str(tmp_path / "nope")}
    fixture = Env(tmp_path, known, {**nothing, "PGSYSCONFDIR": str(tmp_path / "none")})
    yield fixture
    fixture.manager.close_all()


def test_the_manager_hands_typed_secrets_to_the_tunnel(
    env: Env, bastion: FakeSshServer, echo: EchoServer
) -> None:
    cid = env.add(tunnelled(bastion, echo, save_password=False))
    assert env.manager.missing_secrets(cid) == [PASSWORD, SSH_PASSWORD]
    session = env.manager.activate(cid, "dbpw", SECRETS).result(timeout=10)
    assert session.state is SessionState.READY
    assert env.manager.missing_secrets(cid) == []
    assert ScriptedClient.created[0].password == "dbpw"


def test_saved_secrets_come_from_the_secret_store(
    env: Env, bastion: FakeSshServer, echo: EchoServer
) -> None:
    cid = env.add(tunnelled(bastion, echo, save_password=True))
    assert env.manager.missing_secrets(cid) == [SSH_PASSWORD]  # the db password is not checked
    env.secrets.set(cid, "wonderland", SSH_PASSWORD)
    assert env.manager.missing_secrets(cid) == []
    session = env.manager.activate(cid).result(timeout=10)
    assert session.state is SessionState.READY


def test_what_a_connection_needs_depends_on_how_it_logs_in(env: Env, tmp_path: Path) -> None:
    plain_key = write_key(tmp_path, "plain")[1]
    locked_key = write_key(tmp_path, "locked", "pp")[1]
    pki = write_pki(tmp_path / "pki")

    def needs(ssh: SshConfig | None, ssl: SslConfig | None = None) -> list[str]:
        extra: dict[str, Any] = {"save_password": True}
        if ssh is not None:
            extra["ssh"] = ssh.model_dump()
        if ssl is not None:
            extra["ssl"] = ssl.model_dump()
        return env.manager.missing_secrets(env.add(server_config(**extra)))

    def key(path: str) -> SshHop:
        return SshHop(host="h", user="u", auth=SshAuth.KEY, key_file=path)

    assert needs(None) == []
    assert needs(SshConfig(server=SshHop(host="h", auth=SshAuth.AGENT))) == []
    assert needs(SshConfig(server=SshHop(host="h", user="u", auth=SshAuth.PASSWORD))) == [
        SSH_PASSWORD
    ]
    assert needs(SshConfig(server=key(plain_key))) == []
    assert needs(SshConfig(server=key(locked_key))) == [SSH_PASSPHRASE]
    jumped = SshConfig(
        server=SshHop(host="h", auth=SshAuth.AGENT),
        jump=SshHop(host="j", user="u", auth=SshAuth.PASSWORD),
    )
    assert needs(jumped) == [JUMP_PASSWORD]
    clear = SslConfig(mode=SslMode.REQUIRE, cert_file=str(pki["client.pem"]))
    locked = SslConfig(
        mode=SslMode.REQUIRE,
        cert_file=str(pki["client.pem"]),
        key_file=str(pki["client-encrypted.key"]),
    )
    assert (
        needs(
            None,
            SslConfig(
                mode=SslMode.REQUIRE,
                cert_file=str(pki["client.pem"]),
                key_file=str(pki["client.key"]),
            ),
        )
        == []
    )
    assert needs(None, clear) == []
    assert needs(None, locked) == [SSL_KEY_PASSWORD]


def test_pgpass_and_service_passwords_mean_nothing_to_ask(env: Env, tmp_path: Path) -> None:
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("db.invalid:5432:*:u:from-file\n")
    pgpass.chmod(0o600)
    manager = ConnectionManager(
        env.store,
        env.secrets,
        environ={"PGPASSFILE": str(pgpass), "PGSERVICEFILE": str(tmp_path / "nope")},
        client_factory=ScriptedClient,
    )
    known_user = env.add(server_config(save_password=False))
    stranger = env.add(server_config(user="nobody", save_password=False))
    assert not manager.needs_password(known_user)
    assert manager.needs_password(stranger)
    mysql = env.add(server_config(dialect="mysql", save_password=False))
    assert manager.needs_password(mysql)  # ~/.pgpass is PostgreSQL's


def test_the_host_key_can_be_trusted_and_the_connection_retried(
    tmp_path: Path, bastion: FakeSshServer, echo: EchoServer
) -> None:
    fresh = KnownHosts(tmp_path / "hosts", extra=[])
    env = Env(tmp_path, fresh, {})
    try:
        cid = env.add(tunnelled(bastion, echo))
        session = env.manager.activate(cid, None, SECRETS).result(timeout=10)
        error = session.error
        assert isinstance(error, SshHostKeyUnknown)
        env.manager.trust_host_key(error)
        session = env.manager.connect(cid).result(timeout=10)
        assert session.state is SessionState.READY  # typed secrets were kept
    finally:
        env.manager.close_all()


def test_trusting_needs_a_store(tmp_path: Path) -> None:
    env = Env(tmp_path, None, {})  # type: ignore[arg-type]
    env.manager = ConnectionManager(env.store, env.secrets, environ={})
    with pytest.raises(RuntimeError, match="known-hosts"):
        env.manager.trust_host_key(SshHostKeyUnknown("h", 22, "ssh-rsa", "SHA256:x"))


def test_the_manager_runs_connection_tests_with_its_own_settings(
    env: Env, bastion: FakeSshServer, echo: EchoServer
) -> None:
    config = tunnelled(bastion, echo)
    check = env.manager.test_connection(config, "pw", SECRETS)
    assert check.ok, check.failure
    assert [s.name for s in check.steps[:2]] == ["SSH", "Tunnel"]
    assert env.manager.test_connection(config, "pw", {SSH_PASSWORD: "wrong"}).ok is False
    # the tunnels of both tests are gone
    assert not any(isinstance(h, Session) for h in env.manager._sessions.values())


def test_jump_hosts_are_part_of_the_manager_flow(
    env: Env, bastion: FakeSshServer, echo: EchoServer
) -> None:
    config = tunnelled(bastion, echo, save_password=False)
    assert config.ssh is not None
    jump = hop(bastion, user="jumper")
    ssh = SshConfig(server=config.ssh.server, jump=jump)
    cid = env.add(config.model_copy(update={"ssh": ssh}))
    assert env.manager.missing_secrets(cid) == [PASSWORD, JUMP_PASSWORD, SSH_PASSWORD]
    session = env.manager.activate(cid, "x", {**SECRETS, JUMP_PASSWORD: "jp"}).result(timeout=10)
    assert session.state is SessionState.READY
    assert bastion.connections == 2  # the jump, then the server behind it
