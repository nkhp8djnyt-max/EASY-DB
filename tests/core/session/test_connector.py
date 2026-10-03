"""``prepare`` / ``check_connection``: service files, ~/.pgpass and the SSH tunnel in front of a
real database (the in-process SSH server forwards to the PostgreSQL / MariaDB under test)."""

from __future__ import annotations

import socket
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from easydbms.core.connections import (
    SSH_PASSWORD,
    ServerConnection,
    SshConfig,
    replace,
)
from easydbms.core.db import CheckStep, ConnectionFailed, create_client
from easydbms.core.session import check_connection, prepare
from easydbms.core.ssh import (
    KnownHosts,
    SshAuthFailed,
    SshHostKeyChanged,
    SshHostKeyUnknown,
    SshSecretRequired,
)
from tests.core.conftest import Target
from tests.core.ssh.conftest import config as ssh_config
from tests.support.ssh_server import FakeSshServer


@pytest.fixture
def known(tmp_path: Path) -> KnownHosts:
    return KnownHosts(tmp_path / "known_hosts", extra=[])


@pytest.fixture
def bastion() -> Iterator[FakeSshServer]:
    with FakeSshServer(users={"alice": "wonderland"}) as running:
        yield running


@pytest.fixture
def server(target: Target) -> ServerConnection:
    if not isinstance(target.config, ServerConnection):
        pytest.skip("server connections only")
    return target.config


def tunnelled(server: ServerConnection, ssh: SshConfig) -> ServerConnection:
    changed = replace(server, ssh=ssh)
    assert isinstance(changed, ServerConnection)
    return changed


def trust(known: KnownHosts, bastion: FakeSshServer) -> None:
    known.trust("127.0.0.1", bastion.port, bastion.host_key)


def names(steps: Sequence[CheckStep]) -> list[str]:
    return [step.name for step in steps]


# ---------------------------------------------------------------------------- no tunnel


def test_a_plain_connection_needs_no_preparation(server: ServerConnection) -> None:
    prepared = prepare(server, "pw", {}, {}, None)
    assert prepared.tunnel is None
    assert prepared.runtime.route is None
    assert prepared.password == "pw"
    assert prepared.steps == []
    prepared.close()


def test_placeholders_are_resolved_first(server: ServerConnection) -> None:
    prepared = prepare(
        replace(server, user="${DB_USER}"), "${DB_PW}", {}, {"DB_USER": "u", "DB_PW": "p"}, None
    )
    assert isinstance(prepared.config, ServerConnection)
    assert (prepared.config.user, prepared.password) == ("u", "p")


def test_an_undefined_variable_is_a_failed_step(server: ServerConnection) -> None:
    check = check_connection(replace(server, user="${NOPE}"), None, {}, {}, None)
    assert not check.ok
    assert check.steps[-1].name == "Settings"
    assert "NOPE" in check.steps[-1].detail


# ---------------------------------------------------------------------------- the tunnel


def test_the_database_is_reached_through_the_tunnel(
    target: Target, server: ServerConnection, bastion: FakeSshServer, known: KnownHosts
) -> None:
    trust(known, bastion)
    config = tunnelled(server, ssh_config(bastion))
    prepared = prepare(config, target.password, {SSH_PASSWORD: "wonderland"}, {}, known, timeout=5)
    port = 0
    try:
        assert prepared.tunnel is not None
        port = prepared.tunnel.local_port
        assert prepared.runtime.route is not None
        assert prepared.runtime.route.port == prepared.tunnel.local_port
        assert names(prepared.steps) == ["SSH", "Tunnel"]
        # the host stays the real one: only the socket goes to the local end of the tunnel
        assert isinstance(prepared.config, ServerConnection)
        assert prepared.config.host == server.host
        with create_client(prepared.config, prepared.password, prepared.runtime) as client:
            assert client.execute("SELECT 1").rows == ((1,),)
        assert (server.host, server.effective_port) in bastion.forward_requests
    finally:
        prepared.close()
    assert prepared.tunnel is not None
    assert not prepared.tunnel.alive
    with pytest.raises(ConnectionRefusedError), socket.create_connection(("127.0.0.1", port), 1):
        pass


def test_the_connection_test_lists_the_tunnel_steps_then_the_database_steps(
    target: Target, server: ServerConnection, bastion: FakeSshServer, known: KnownHosts
) -> None:
    trust(known, bastion)
    config = tunnelled(server, ssh_config(bastion))
    check = check_connection(config, target.password, {SSH_PASSWORD: "wonderland"}, {}, known)
    assert check.ok, check.failure
    assert names(check.steps)[:2] == ["SSH", "Tunnel"]
    assert names(check.steps)[-1] == "Query"
    assert "TLS" in names(check.steps)  # the servers under test offer TLS
    assert "DNS" not in names(check.steps)  # the SSH steps already proved the way
    assert check.server_version


def test_a_wrong_ssh_password_fails_at_the_ssh_step(
    target: Target, server: ServerConnection, bastion: FakeSshServer, known: KnownHosts
) -> None:
    trust(known, bastion)
    config = tunnelled(server, ssh_config(bastion))
    check = check_connection(config, target.password, {SSH_PASSWORD: "nope"}, {}, known)
    assert isinstance(check.failure, SshAuthFailed)
    assert [(s.name, s.ok) for s in check.steps] == [("SSH", False)]


def test_a_missing_ssh_password_is_reported_with_its_field(
    server: ServerConnection, bastion: FakeSshServer, known: KnownHosts
) -> None:
    trust(known, bastion)
    config = tunnelled(server, ssh_config(bastion))
    with pytest.raises(SshSecretRequired) as raised:
        prepare(config, None, {}, {}, known)
    assert raised.value.field == SSH_PASSWORD


def test_an_unknown_server_key_is_reported_with_the_key_to_trust(
    target: Target, server: ServerConnection, bastion: FakeSshServer, known: KnownHosts
) -> None:
    config = tunnelled(server, ssh_config(bastion))
    secrets = {SSH_PASSWORD: "wonderland"}
    check = check_connection(config, target.password, secrets, {}, known)
    failure = check.failure
    assert isinstance(failure, SshHostKeyUnknown)
    assert failure.fingerprint.startswith("SHA256:")
    assert check.steps[-1].name == "SSH"
    assert not check.steps[-1].ok
    known.trust(failure.host, failure.port, failure.key)  # what the "trust" button does
    assert check_connection(config, target.password, secrets, {}, known).ok


def test_a_changed_server_key_is_refused(
    target: Target, server: ServerConnection, bastion: FakeSshServer, known: KnownHosts
) -> None:
    with FakeSshServer(users={"alice": "wonderland"}) as impostor:
        known.trust("127.0.0.1", bastion.port, impostor.host_key)  # remembered another key
        config = tunnelled(server, ssh_config(bastion))
        check = check_connection(config, target.password, {SSH_PASSWORD: "wonderland"}, {}, known)
    assert isinstance(check.failure, SshHostKeyChanged)


def test_without_a_known_hosts_store_ssh_is_refused(
    server: ServerConnection, bastion: FakeSshServer
) -> None:
    config = tunnelled(server, ssh_config(bastion))
    with pytest.raises(ConnectionFailed, match="known-hosts"):
        prepare(config, None, {SSH_PASSWORD: "wonderland"}, {}, None)


# ---------------------------------------------------------------------------- service and pgpass


def service_environment(
    tmp_path: Path, server: ServerConnection, password: str | None
) -> dict[str, str]:
    service = tmp_path / "pg_service.conf"
    service.write_text(
        f"[prod]\nhost={server.host}\nport={server.effective_port}\n"
        f"dbname={server.database}\nuser={server.user}\n"
        + (f"password={password}\n" if password else "")
    )
    return {
        "PGSERVICEFILE": str(service),
        "PGSYSCONFDIR": str(tmp_path / "none"),
        "PGPASSFILE": str(tmp_path / "pgpass"),
    }


def postgres(server: ServerConnection) -> ServerConnection:
    if server.dialect_impl.id.value != "postgresql":
        pytest.skip("PostgreSQL only")
    return server


def test_a_service_fills_in_what_the_connection_leaves_out(
    target: Target, server: ServerConnection, tmp_path: Path
) -> None:
    server = postgres(server)
    env = service_environment(tmp_path, server, target.password)
    bare = replace(server, host="", port=None, user="", database="", service="prod")
    prepared = prepare(bare, None, {}, env, None)
    assert isinstance(prepared.config, ServerConnection)
    assert (prepared.config.host, prepared.config.user) == (server.host, server.user)
    assert prepared.password == target.password
    assert names(prepared.steps) == ["Service", "Password"]
    with create_client(prepared.config, prepared.password, prepared.runtime) as client:
        assert client.execute("SELECT 1").rows == ((1,),)


def test_pgpass_supplies_the_password_when_none_is_given(
    target: Target, server: ServerConnection, tmp_path: Path
) -> None:
    server = postgres(server)
    env = service_environment(tmp_path, server, None)
    pgpass = tmp_path / "pgpass"
    pgpass.write_text(f"{server.host}:{server.effective_port}:*:{server.user}:{target.password}\n")
    pgpass.chmod(0o600)
    check = check_connection(
        replace(server, host="", port=None, user="", database="", service="prod"),
        None,
        {},
        env,
        None,
    )
    assert check.ok, check.failure
    assert names(check.steps)[:2] == ["Service", "Password"]
    assert "line 1" in check.steps[1].detail


def test_an_unknown_service_is_a_failed_service_step(
    server: ServerConnection, tmp_path: Path
) -> None:
    server = postgres(server)
    env = service_environment(tmp_path, server, None)
    check = check_connection(replace(server, service="ghost"), None, {}, env, None)
    assert not check.ok
    assert [(s.name, s.ok) for s in check.steps] == [("Service", False)]
    assert "'ghost'" in check.steps[0].detail
