"""An SSH tunnel to the database: a listener on 127.0.0.1 that forwards through the SSH server.

The tunnel is opened by the session before it connects and closed with it. The chain is
``this machine -> [jump host] -> SSH server -> database host:port``; each hop is verified against
the known hosts *before* any credential is sent, and the forward is tried once during
:meth:`SshTunnel.open` so "the SSH server cannot reach the database" is reported as that, not as an
obscure database error later.
"""

from __future__ import annotations

import contextlib
import getpass
import select
import socket
import socketserver
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import paramiko

from ..connections import (
    JUMP_PASSPHRASE,
    JUMP_PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SshAuth,
    SshConfig,
    SshHop,
)
from .errors import (
    SshAuthFailed,
    SshError,
    SshHostKeyChanged,
    SshHostKeyUnknown,
    SshSecretRequired,
    SshTunnelError,
    SshUnreachable,
)
from .hostkeys import KnownHosts, Verdict, fingerprint
from .keys import load_key

LOCAL_HOST = "127.0.0.1"
_CHUNK = 32768

#: ``on_step(name, detail, seconds)`` is called as each stage of opening succeeds.
StepCallback = Callable[[str, str, float], None]


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    transport: Any
    target: tuple[str, int]


class _Forward(socketserver.BaseRequestHandler):
    """One database connection: pump bytes between the local socket and an SSH channel."""

    def handle(self) -> None:
        server = self.server
        assert isinstance(server, _Server)
        try:
            channel = server.transport.open_channel(
                "direct-tcpip", server.target, self.request.getpeername()
            )
        except (paramiko.SSHException, OSError, EOFError):
            return
        try:
            while server.transport.is_active():
                readable, _, _ = select.select([self.request, channel], [], [], 1.0)
                if self.request in readable:
                    data = self.request.recv(_CHUNK)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(_CHUNK)
                    if not data:
                        break
                    self.request.sendall(data)
        except (OSError, EOFError, paramiko.SSHException):
            pass
        finally:
            with contextlib.suppress(Exception):
                channel.close()
            with contextlib.suppress(Exception):
                self.request.close()


class SshTunnel:
    def __init__(
        self,
        ssh: SshConfig,
        target: tuple[str, int],
        secrets: Mapping[str, str],
        known_hosts: KnownHosts,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._ssh = ssh
        self._target = target
        self._secrets = secrets
        self._known = known_hosts
        self._timeout = timeout
        self._transports: list[Any] = []
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        #: What is being done right now (``"SSH jump"``, ``"SSH"``, ``"Tunnel"``): names the
        #: failing step when :meth:`open` raises.
        self.stage = ""

    # ------------------------------------------------------------------ public API

    @property
    def local_port(self) -> int:
        if self._server is None:
            raise RuntimeError("the tunnel is not open")
        return int(self._server.server_address[1])

    @property
    def alive(self) -> bool:
        return bool(self._transports) and all(t.is_active() for t in self._transports)

    def open(self, on_step: StepCallback | None = None) -> None:
        """Connect the chain and start listening; raises :class:`SshError` (nothing stays open)."""
        report = on_step or (lambda *_: None)
        try:
            transport: Any = None
            if self._ssh.jump is not None:
                self.stage = "SSH jump"
                started = time.perf_counter()
                jump = self._connect_hop(self._ssh.jump, JUMP_PASSWORD, JUMP_PASSPHRASE, None)
                self._transports.append(jump)
                report(self.stage, _hop_text(self._ssh.jump), time.perf_counter() - started)
                server = self._ssh.server
                channel = self._channel(jump, (server.host, server.port), "the SSH server")
                transport = channel
            self.stage = "SSH"
            started = time.perf_counter()
            server_transport = self._connect_hop(
                self._ssh.server, SSH_PASSWORD, SSH_PASSPHRASE, transport
            )
            self._transports.append(server_transport)
            report(self.stage, _hop_text(self._ssh.server), time.perf_counter() - started)
            self.stage = "Tunnel"
            started = time.perf_counter()
            probe = self._channel(server_transport, self._target, "the database")
            probe.close()
            self._listen(server_transport)
            host, port = self._target
            report(
                self.stage,
                f"{host}:{port} via {LOCAL_HOST}:{self.local_port}",
                time.perf_counter() - started,
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Stop listening and close every SSH connection of the chain."""
        server, self._server = self._server, None
        if server is not None:
            with contextlib.suppress(Exception):
                server.shutdown()
            with contextlib.suppress(Exception):
                server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)
        for transport in reversed(self._transports):
            with contextlib.suppress(Exception):
                transport.close()
        self._transports.clear()

    def __enter__(self) -> SshTunnel:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ one hop

    def _connect_hop(
        self, hop: SshHop, password_field: str, passphrase_field: str, via: Any
    ) -> Any:
        """A verified, authenticated transport to ``hop`` (through ``via`` for a jump)."""
        sock = via if via is not None else self._socket(hop)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = self._timeout
        transport.auth_timeout = self._timeout
        try:
            try:
                transport.start_client(timeout=self._timeout)
            except (paramiko.SSHException, EOFError, OSError) as error:
                raise SshUnreachable(
                    f"{hop.host}:{hop.port} did not complete the SSH handshake: {error}"
                ) from None
            self._verify_host(hop, transport.get_remote_server_key())
            self._authenticate(transport, hop, password_field, passphrase_field)
            transport.set_keepalive(30)
        except BaseException:
            with contextlib.suppress(Exception):
                transport.close()
            raise
        return transport

    def _socket(self, hop: SshHop) -> socket.socket:
        try:
            return socket.create_connection((hop.host, hop.port), self._timeout)
        except socket.gaierror as error:
            raise SshUnreachable(
                f"cannot resolve the SSH host '{hop.host}': {error.strerror or error}"
            ) from None
        except TimeoutError:
            raise SshUnreachable(
                f"{hop.host}:{hop.port} did not answer within {self._timeout:g}s "
                "(is a firewall dropping packets?)"
            ) from None
        except OSError as error:
            raise SshUnreachable(
                f"{hop.host}:{hop.port} refused the connection"
                + (f": {error.strerror}" if error.strerror else "")
            ) from None

    def _verify_host(self, hop: SshHop, key: Any) -> None:
        verdict, remembered = self._known.verdict(hop.host, hop.port, key)
        if verdict is Verdict.KNOWN:
            return
        if verdict is Verdict.CHANGED:
            raise SshHostKeyChanged(hop.host, hop.port, fingerprint(key), remembered)
        raise SshHostKeyUnknown(hop.host, hop.port, key.get_name(), fingerprint(key), key)

    # ------------------------------------------------------------------ authentication

    def _authenticate(
        self, transport: Any, hop: SshHop, password_field: str, passphrase_field: str
    ) -> None:
        user = hop.user or getpass.getuser()
        try:
            if hop.auth is SshAuth.PASSWORD:
                self._by_password(transport, user, self._secret(password_field, hop), hop)
            elif hop.auth is SshAuth.KEY:
                key = load_key(hop.key_file, self._secrets.get(passphrase_field), passphrase_field)
                self._offer(transport, user, [key], hop, f"the key {hop.key_file}")
            else:
                self._by_agent(transport, user, hop)
        except paramiko.BadAuthenticationType as error:
            raise SshAuthFailed(
                f"{hop.host} does not accept {hop.auth.value} login "
                f"(it accepts: {', '.join(error.allowed_types)})"
            ) from None
        except paramiko.AuthenticationException:
            raise SshAuthFailed(
                f"{hop.host} rejected the {hop.auth.value} login of {user}"
            ) from None
        except paramiko.SSHException as error:
            raise SshError(f"SSH login to {hop.host} failed: {error}") from None

    def _secret(self, field: str, hop: SshHop) -> str:
        value = self._secrets.get(field)
        if value is None:
            raise SshSecretRequired(f"the SSH password for {hop.host} is needed", field)
        return value

    @staticmethod
    def _by_password(transport: Any, user: str, password: str, hop: SshHop) -> None:
        try:
            transport.auth_password(user, password)
        except paramiko.BadAuthenticationType as error:
            if "keyboard-interactive" not in error.allowed_types:
                raise
            # Many servers only offer the password as a "keyboard-interactive" prompt.
            transport.auth_interactive(user, lambda _t, _i, prompts: [password] * len(prompts))

    @staticmethod
    def _offer(transport: Any, user: str, keys: list[Any], hop: SshHop, what: str) -> None:
        for key in keys:
            try:
                transport.auth_publickey(user, key)
                return
            except paramiko.AuthenticationException:
                continue
        raise SshAuthFailed(
            f"{hop.host} did not accept {what} for {user} (is the public key in authorized_keys?)"
        )

    def _by_agent(self, transport: Any, user: str, hop: SshHop) -> None:
        agent = paramiko.Agent()
        try:
            keys = list(agent.get_keys())
            if not keys:
                raise SshAuthFailed(
                    "ssh-agent holds no keys or cannot be reached "
                    "(check SSH_AUTH_SOCK, then run ssh-add)"
                )
            self._offer(transport, user, keys, hop, "any key of the ssh-agent")
        finally:
            with contextlib.suppress(Exception):
                agent.close()

    # ------------------------------------------------------------------ forwarding

    def _channel(self, transport: Any, target: tuple[str, int], what: str) -> Any:
        try:
            return transport.open_channel(
                "direct-tcpip", target, (LOCAL_HOST, 0), timeout=self._timeout
            )
        except paramiko.ChannelException as error:
            reasons = {
                1: "port forwarding is not allowed there (AllowTcpForwarding / PermitOpen)",
                2: "the connection was refused or the host is unreachable from there",
                4: "the SSH server is out of resources",
            }
            reason = reasons.get(int(error.code), str(error))
            raise SshTunnelError(
                f"the SSH server cannot reach {what} {target[0]}:{target[1]}: {reason}"
            ) from None
        except (paramiko.SSHException, OSError, EOFError) as error:
            raise SshTunnelError(f"cannot open a channel to {what}: {error}") from None

    def _listen(self, transport: Any) -> None:
        server = _Server((LOCAL_HOST, 0), _Forward)
        server.transport = transport
        server.target = self._target
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="ssh-tunnel",
            daemon=True,
            kwargs={"poll_interval": 0.2},
        )
        self._thread.start()


def _hop_text(hop: SshHop) -> str:
    return f"{hop.user + '@' if hop.user else ''}{hop.host}:{hop.port} ({hop.auth.value})"
