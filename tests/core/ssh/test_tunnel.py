"""The SSH tunnel against a real (in-process) SSH server."""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import paramiko
import pytest

from easydbms.core.connections import (
    JUMP_PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SshAuth,
    SshConfig,
)
from easydbms.core.ssh import (
    KnownHosts,
    SshAuthFailed,
    SshHostKeyChanged,
    SshHostKeyUnknown,
    SshKeyError,
    SshSecretRequired,
    SshTunnel,
    SshTunnelError,
    SshUnreachable,
    fingerprint,
)
from tests.support.keys import write_key
from tests.support.ssh_agent import FakeAgent
from tests.support.ssh_server import EchoServer, FakeSshServer, closed_port

from .conftest import config, hop


def trusted(known: KnownHosts, *servers: FakeSshServer) -> KnownHosts:
    for server in servers:
        known.trust("127.0.0.1", server.port, server.host_key)
    return known


def talk(tunnel: SshTunnel, payload: bytes = b"hello through the tunnel") -> bytes:
    with socket.create_connection(("127.0.0.1", tunnel.local_port), timeout=5) as client:
        client.sendall(payload)
        client.settimeout(5)
        received = b""
        while len(received) < len(payload):
            chunk = client.recv(4096)
            if not chunk:
                break
            received += chunk
        return received


def tunnel_for(
    ssh: SshConfig, echo: EchoServer, known: KnownHosts, secrets: dict[str, str] | None = None
) -> SshTunnel:
    return SshTunnel(ssh, ("127.0.0.1", echo.port), secrets or {}, known, timeout=5)


# ---------------------------------------------------------------------------- the happy paths


def test_password_login_forwards_bytes_both_ways(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    with tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}) as tunnel:
        assert talk(tunnel) == b"hello through the tunnel"
        assert talk(tunnel, b"x" * 200_000) == b"x" * 200_000  # more than one buffer
        assert tunnel.alive
    assert ("127.0.0.1", echo.port) in server.forward_requests


def test_the_listener_is_local_only_and_closes_with_the_tunnel(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    tunnel = tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"})
    tunnel.open()
    port = tunnel.local_port
    assert tunnel._server is not None
    assert tunnel._server.server_address[0] == "127.0.0.1"
    tunnel.close()
    assert not tunnel.alive
    with (
        pytest.raises(ConnectionRefusedError),
        socket.create_connection(("127.0.0.1", port), timeout=1),
    ):
        pass
    tunnel.close()  # closing twice is fine
    with pytest.raises(RuntimeError):
        _ = tunnel.local_port


def test_many_connections_at_once(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    with tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}) as tunnel:
        results: list[bytes] = []

        def work(number: int) -> None:
            results.append(talk(tunnel, f"client {number}".encode() * 50))

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        assert len(results) == 8
        assert all(r.startswith(b"client ") for r in results)


def test_steps_are_reported_as_they_succeed(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    steps: list[tuple[str, str]] = []
    tunnel = tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"})
    tunnel.open(lambda name, detail, _seconds: steps.append((name, detail)))
    try:
        assert [name for name, _ in steps] == ["SSH", "Tunnel"]
        assert "alice@127.0.0.1" in steps[0][1]
        assert f"127.0.0.1:{echo.port}" in steps[1][1]
    finally:
        tunnel.close()


def test_keyboard_interactive_only_servers_accept_the_password(
    echo: EchoServer, known: KnownHosts
) -> None:
    with FakeSshServer(
        users={"alice": "wonderland"}, auth_methods=("keyboard-interactive",)
    ) as server:
        trusted(known, server)
        with tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}) as tunnel:
            assert talk(tunnel) == b"hello through the tunnel"


# ---------------------------------------------------------------------------- keys


def test_key_login(tmp_path: Path, echo: EchoServer, known: KnownHosts) -> None:
    key, path = write_key(tmp_path, "id_ecdsa")
    with FakeSshServer(authorized_keys=[key], auth_methods=("publickey",)) as server:
        trusted(known, server)
        ssh = config(server, auth=SshAuth.KEY, key_file=path)
        with tunnel_for(ssh, echo, known) as tunnel:
            assert talk(tunnel) == b"hello through the tunnel"


def test_ssh_agent_login(
    tmp_path: Path, echo: EchoServer, known: KnownHosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    key, _ = write_key(tmp_path, "agent_key")
    agent = FakeAgent(tmp_path, [key])
    monkeypatch.setenv("SSH_AUTH_SOCK", agent.path)
    try:
        with FakeSshServer(authorized_keys=[key], auth_methods=("publickey",)) as server:
            trusted(known, server)
            ssh = config(server, auth=SshAuth.AGENT)
            with tunnel_for(ssh, echo, known) as tunnel:
                assert talk(tunnel) == b"hello through the tunnel"
        assert agent.signed >= 1
    finally:
        agent.stop()


def test_an_agent_without_the_right_key_is_explained(
    tmp_path: Path, echo: EchoServer, known: KnownHosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    wanted, _ = write_key(tmp_path, "wanted")
    other, _ = write_key(tmp_path, "other")
    agent = FakeAgent(tmp_path, [other])
    monkeypatch.setenv("SSH_AUTH_SOCK", agent.path)
    try:
        with FakeSshServer(authorized_keys=[wanted], auth_methods=("publickey",)) as server:
            trusted(known, server)
            with pytest.raises(SshAuthFailed, match="ssh-agent"):
                tunnel_for(config(server, auth=SshAuth.AGENT), echo, known).open()
    finally:
        agent.stop()


def test_no_agent_at_all_is_explained(
    echo: EchoServer, known: KnownHosts, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    with FakeSshServer(auth_methods=("publickey",)) as server:
        trusted(known, server)
        with pytest.raises(SshAuthFailed, match="SSH_AUTH_SOCK"):
            tunnel_for(config(server, auth=SshAuth.AGENT), echo, known).open()


def test_an_encrypted_key_needs_and_uses_its_passphrase(
    tmp_path: Path, echo: EchoServer, known: KnownHosts
) -> None:
    key, path = write_key(tmp_path, "id_ecdsa", passphrase="open sesame")
    with FakeSshServer(authorized_keys=[key], auth_methods=("publickey",)) as server:
        trusted(known, server)
        ssh = config(server, auth=SshAuth.KEY, key_file=path)
        with pytest.raises(SshSecretRequired) as needed:
            tunnel_for(ssh, echo, known).open()
        assert needed.value.field == SSH_PASSPHRASE
        with pytest.raises(SshKeyError, match="wrong passphrase"):
            tunnel_for(ssh, echo, known, {SSH_PASSPHRASE: "nope"}).open()
        with tunnel_for(ssh, echo, known, {SSH_PASSPHRASE: "open sesame"}) as tunnel:
            assert talk(tunnel) == b"hello through the tunnel"


def test_a_key_the_server_does_not_know_is_rejected(
    tmp_path: Path, echo: EchoServer, known: KnownHosts
) -> None:
    _, path = write_key(tmp_path, "id_ecdsa")
    other, _ = write_key(tmp_path, "other")
    with FakeSshServer(authorized_keys=[other], auth_methods=("publickey",)) as server:
        trusted(known, server)
        with pytest.raises(SshAuthFailed, match="authorized_keys"):
            tunnel_for(config(server, auth=SshAuth.KEY, key_file=path), echo, known).open()


def test_missing_and_broken_key_files_are_explained(
    tmp_path: Path, server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    with pytest.raises(SshKeyError, match="does not exist"):
        tunnel_for(
            config(server, auth=SshAuth.KEY, key_file=str(tmp_path / "nope")), echo, known
        ).open()
    junk = tmp_path / "junk"
    junk.write_text("this is not a key")
    with pytest.raises(SshKeyError, match="not a usable private key"):
        tunnel_for(config(server, auth=SshAuth.KEY, key_file=str(junk)), echo, known).open()


# ---------------------------------------------------------------------------- password failures


def test_a_wrong_password_is_reported_and_nothing_stays_open(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    tunnel = tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wrong"})
    with pytest.raises(SshAuthFailed, match="rejected the password login of alice"):
        tunnel.open()
    assert tunnel.stage == "SSH"
    assert not tunnel.alive
    assert tunnel._transports == []


def test_a_missing_password_asks_for_it(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    with pytest.raises(SshSecretRequired) as needed:
        tunnel_for(config(server), echo, known).open()
    assert needed.value.field == SSH_PASSWORD
    assert server.attempts == []  # nothing was sent to the server


def test_a_server_without_password_login_says_what_it_accepts(
    echo: EchoServer, known: KnownHosts
) -> None:
    with FakeSshServer(users={"alice": "x"}, auth_methods=("publickey",)) as server:
        trusted(known, server)
        with pytest.raises(SshAuthFailed, match=r"does not accept password login.*publickey"):
            tunnel_for(config(server), echo, known, {SSH_PASSWORD: "x"}).open()


# ---------------------------------------------------------------------------- host keys


def test_an_unknown_server_is_not_trusted_and_gets_no_credentials(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    with pytest.raises(SshHostKeyUnknown) as unknown:
        tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}).open()
    error = unknown.value
    assert (error.host, error.port) == ("127.0.0.1", server.port)
    assert error.fingerprint == fingerprint(server.host_key)
    assert error.fingerprint.startswith("SHA256:")
    assert error.key_type == server.host_key.get_name()
    assert server.attempts == []  # not even a login attempt: the key is checked first


def test_trusting_remembers_the_server_across_runs(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts, tmp_path: Path
) -> None:
    with pytest.raises(SshHostKeyUnknown) as unknown:
        tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}).open()
    known.trust(unknown.value.host, unknown.value.port, server.host_key)
    assert f"[127.0.0.1]:{server.port}" in (tmp_path / "known_hosts").read_text()  # OpenSSH format
    again = KnownHosts(tmp_path / "known_hosts", extra=[])
    with tunnel_for(config(server), echo, again, {SSH_PASSWORD: "wonderland"}) as tunnel:
        assert talk(tunnel)


def test_a_changed_key_is_refused(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    other = paramiko.ECDSAKey.generate(bits=256)
    known.trust("127.0.0.1", server.port, other)  # we remember another key of the same type
    with pytest.raises(SshHostKeyChanged) as changed:
        tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}).open()
    assert "CHANGED" in str(changed.value)
    assert changed.value.known == fingerprint(other)
    assert changed.value.fingerprint == fingerprint(server.host_key)
    assert server.attempts == []


def test_the_users_own_known_hosts_are_honoured(
    server: FakeSshServer, echo: EchoServer, tmp_path: Path
) -> None:
    theirs = KnownHosts(tmp_path / "theirs", extra=[])
    theirs.trust("127.0.0.1", server.port, server.host_key)
    known = KnownHosts(tmp_path / "ours", extra=[tmp_path / "theirs"])
    with tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}) as tunnel:
        assert talk(tunnel)
    assert not (tmp_path / "ours").exists()  # read-only: nothing was written there


def test_known_hosts_can_forget_a_server(server: FakeSshServer, known: KnownHosts) -> None:
    known.trust("127.0.0.1", server.port, server.host_key)
    assert known.forget("127.0.0.1", server.port)
    assert not known.forget("127.0.0.1", server.port)
    verdict, _ = known.verdict("127.0.0.1", server.port, server.host_key)
    assert verdict.value == "unknown"


def test_the_default_port_is_written_without_brackets(tmp_path: Path) -> None:
    keys = KnownHosts(tmp_path / "kh", extra=[])
    key = paramiko.ECDSAKey.generate(bits=256)
    keys.trust("db.example.com", 22, key)
    keys.trust("db.example.com", 2222, key)
    lines = (tmp_path / "kh").read_text().splitlines()
    assert any(line.startswith("db.example.com ") for line in lines)
    assert any(line.startswith("[db.example.com]:2222 ") for line in lines)


# ------------------------------------------------------------------ reachability and forwarding


def test_a_closed_ssh_port_is_unreachable(echo: EchoServer, known: KnownHosts) -> None:
    ssh = SshConfig.model_validate(
        {"server": {"host": "127.0.0.1", "port": closed_port(), "user": "a", "auth": "password"}}
    )
    with pytest.raises(SshUnreachable, match="refused"):
        tunnel_for(ssh, echo, known, {SSH_PASSWORD: "x"}).open()


def test_an_unknown_ssh_host_is_a_dns_problem(echo: EchoServer, known: KnownHosts) -> None:
    ssh = SshConfig.model_validate(
        {"server": {"host": "no-such-host.invalid", "user": "a", "auth": "password"}}
    )
    with pytest.raises(SshUnreachable, match="cannot resolve"):
        tunnel_for(ssh, echo, known, {SSH_PASSWORD: "x"}).open()


def test_something_that_is_not_ssh_fails_the_handshake(echo: EchoServer, known: KnownHosts) -> None:
    ssh = SshConfig.model_validate(
        {"server": {"host": "127.0.0.1", "port": echo.port, "user": "a", "auth": "password"}}
    )
    with pytest.raises(SshUnreachable, match="handshake"):
        tunnel_for(ssh, echo, known, {SSH_PASSWORD: "x"}).open()


def test_forwarding_can_be_prohibited_by_the_server(echo: EchoServer, known: KnownHosts) -> None:
    with FakeSshServer(users={"alice": "x"}, allow_forwarding=False) as server:
        trusted(known, server)
        tunnel = tunnel_for(config(server), echo, known, {SSH_PASSWORD: "x"})
        with pytest.raises(SshTunnelError, match="not allowed"):
            tunnel.open()
        assert tunnel.stage == "Tunnel"
        assert not tunnel.alive


def test_a_database_that_is_not_listening_is_reported_as_such(
    server: FakeSshServer, known: KnownHosts
) -> None:
    trusted(known, server)
    tunnel = SshTunnel(
        config(server), ("127.0.0.1", closed_port()), {SSH_PASSWORD: "wonderland"}, known, timeout=5
    )
    with pytest.raises(SshTunnelError, match="refused or the host is unreachable"):
        tunnel.open()


def test_the_tunnel_notices_when_the_server_goes_away(
    server: FakeSshServer, echo: EchoServer, known: KnownHosts
) -> None:
    trusted(known, server)
    with tunnel_for(config(server), echo, known, {SSH_PASSWORD: "wonderland"}) as tunnel:
        assert tunnel.alive
        server.drop_connections()
        for _ in range(50):
            if not tunnel.alive:
                break
            threading.Event().wait(0.1)
        assert not tunnel.alive


# ---------------------------------------------------------------------------- jump host


def test_through_a_jump_host(echo: EchoServer, known: KnownHosts) -> None:
    with (
        FakeSshServer(users={"jumper": "jp"}) as bastion,
        FakeSshServer(users={"alice": "wonderland"}) as inner,
    ):
        trusted(known, bastion, inner)
        ssh = config(inner, jump=hop(bastion, user="jumper"))
        secrets = {SSH_PASSWORD: "wonderland", JUMP_PASSWORD: "jp"}
        steps: list[str] = []
        tunnel = tunnel_for(ssh, echo, known, secrets)
        tunnel.open(lambda name, _detail, _seconds: steps.append(name))
        try:
            assert steps == ["SSH jump", "SSH", "Tunnel"]
            assert talk(tunnel) == b"hello through the tunnel"
            # the bastion was asked for the inner SSH server, the inner one for the database
            assert ("127.0.0.1", inner.port) in bastion.forward_requests
            assert ("127.0.0.1", echo.port) in inner.forward_requests
            assert ("127.0.0.1", echo.port) not in bastion.forward_requests
        finally:
            tunnel.close()
        assert tunnel._transports == []


def test_a_wrong_jump_password_is_reported_for_the_jump_step(
    echo: EchoServer, known: KnownHosts
) -> None:
    with (
        FakeSshServer(users={"jumper": "jp"}) as bastion,
        FakeSshServer(users={"alice": "wonderland"}) as inner,
    ):
        trusted(known, bastion, inner)
        tunnel = tunnel_for(
            config(inner, jump=hop(bastion, user="jumper")),
            echo,
            known,
            {SSH_PASSWORD: "wonderland", JUMP_PASSWORD: "wrong"},
        )
        with pytest.raises(SshAuthFailed):
            tunnel.open()
        assert tunnel.stage == "SSH jump"


def test_the_inner_server_key_is_checked_too(echo: EchoServer, known: KnownHosts) -> None:
    with (
        FakeSshServer(users={"jumper": "jp"}) as bastion,
        FakeSshServer(users={"alice": "wonderland"}) as inner,
    ):
        trusted(known, bastion)  # but not the inner server
        tunnel = tunnel_for(
            config(inner, jump=hop(bastion, user="jumper")),
            echo,
            known,
            {SSH_PASSWORD: "wonderland", JUMP_PASSWORD: "jp"},
        )
        with pytest.raises(SshHostKeyUnknown) as unknown:
            tunnel.open()
        assert unknown.value.port == inner.port
        assert inner.attempts == []
