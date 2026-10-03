"""A small SSH server (paramiko) that authenticates and forwards ports: tunnel tests run against it.

It speaks real SSH on 127.0.0.1, so the application code under test is the production code:
password, key and keyboard-interactive logins, ``direct-tcpip`` forwarding (also to another
instance, i.e. a jump host), switchable host key, and a log of what the clients asked for.
"""

from __future__ import annotations

import contextlib
import select
import socket
import threading
from collections.abc import Sequence
from typing import Any

import paramiko

_AUTH_OK: int = int(paramiko.AUTH_SUCCESSFUL)
_AUTH_FAILED: int = int(paramiko.AUTH_FAILED)
_OPEN_OK: int = int(paramiko.OPEN_SUCCEEDED)
_OPEN_PROHIBITED: int = int(paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)
_OPEN_CONNECT_FAILED: int = int(paramiko.OPEN_FAILED_CONNECT_FAILED)


class _Interface(paramiko.ServerInterface):  # type: ignore[misc]
    def __init__(self, owner: FakeSshServer) -> None:
        self.owner = owner
        self.upstreams: dict[int, socket.socket] = {}

    def get_allowed_auths(self, username: str) -> str:
        return ",".join(self.owner.auth_methods)

    def check_auth_password(self, username: str, password: str) -> int:
        self.owner.attempts.append(("password", username))
        if "password" in self.owner.auth_methods and self.owner.users.get(username) == password:
            return _AUTH_OK
        return _AUTH_FAILED

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        self.owner.attempts.append(("publickey", username))
        for allowed in self.owner.authorized_keys:
            if allowed.asbytes() == key.asbytes():
                return _AUTH_OK
        return _AUTH_FAILED

    def check_auth_interactive(self, username: str, submethods: str) -> Any:
        self.owner.attempts.append(("keyboard-interactive", username))
        query = paramiko.InteractiveQuery("", "")
        query.add_prompt("Password: ", False)
        self._interactive_user = username
        return query

    def check_auth_interactive_response(self, responses: Sequence[str]) -> int:
        if self.owner.users.get(self._interactive_user) == (responses[0] if responses else None):
            return _AUTH_OK
        return _AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return _OPEN_OK if kind == "session" else _OPEN_PROHIBITED

    def check_channel_direct_tcpip_request(
        self, chanid: int, origin: tuple[str, int], destination: tuple[str, int]
    ) -> int:
        self.owner.forward_requests.append(destination)
        if not self.owner.allow_forwarding:
            return _OPEN_PROHIBITED
        try:  # like sshd: dial first, so an unreachable target fails the channel request
            self.upstreams[chanid] = socket.create_connection(destination, timeout=5)
        except OSError:
            return _OPEN_CONNECT_FAILED
        return _OPEN_OK


class FakeSshServer:
    def __init__(
        self,
        *,
        users: dict[str, str] | None = None,
        authorized_keys: Sequence[paramiko.PKey] = (),
        auth_methods: Sequence[str] = ("password", "publickey"),
        allow_forwarding: bool = True,
        host_key: paramiko.PKey | None = None,
    ) -> None:
        self.users = users or {}
        self.authorized_keys = list(authorized_keys)
        self.auth_methods = list(auth_methods)
        self.allow_forwarding = allow_forwarding
        self.host_key = host_key or paramiko.ECDSAKey.generate(bits=256)
        self.attempts: list[tuple[str, str]] = []
        self.forward_requests: list[tuple[str, int]] = []
        self.connections = 0
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(20)
        self.port: int = self._socket.getsockname()[1]
        self._stop = threading.Event()
        self._transports: list[paramiko.Transport] = []
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="fake-ssh")

    def start(self) -> FakeSshServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._socket.close()
        for transport in self._transports:
            with contextlib.suppress(Exception):
                transport.close()

    def __enter__(self) -> FakeSshServer:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def drop_connections(self) -> None:
        """Close every client connection (the server stays up)."""
        for transport in self._transports:
            with contextlib.suppress(Exception):
                transport.close()

    # ------------------------------------------------------------------ internals

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._socket.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        transport = paramiko.Transport(client)
        self._transports.append(transport)
        transport.add_server_key(self.host_key)
        interface = _Interface(self)
        try:
            transport.start_server(server=interface)
        except (paramiko.SSHException, EOFError, OSError):
            return
        while transport.is_active() and not self._stop.is_set():
            channel = transport.accept(timeout=0.5)
            if channel is None:
                continue
            upstream = interface.upstreams.pop(channel.get_id(), None)
            if upstream is not None:
                threading.Thread(
                    target=self._forward, args=(channel, upstream), daemon=True
                ).start()

    @staticmethod
    def _forward(channel: paramiko.Channel, upstream: socket.socket) -> None:
        try:
            while True:
                readable, _, _ = select.select([upstream, channel], [], [], 1.0)
                if upstream in readable:
                    data = upstream.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable:
                    data = channel.recv(32768)
                    if not data:
                        break
                    upstream.sendall(data)
        except (OSError, EOFError, paramiko.SSHException):
            pass
        finally:
            with contextlib.suppress(Exception):
                channel.close()
            with contextlib.suppress(Exception):
                upstream.close()


class EchoServer:
    """A TCP server that sends back whatever it receives: the "database" behind a tunnel."""

    def __init__(self) -> None:
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(20)
        self.port: int = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._loop, daemon=True, name="echo")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                client, _ = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._echo, args=(client,), daemon=True).start()

    @staticmethod
    def _echo(client: socket.socket) -> None:
        with client:
            while True:
                try:
                    data = client.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                client.sendall(data)

    def stop(self) -> None:
        with contextlib.suppress(OSError):
            self._socket.close()

    def __enter__(self) -> EchoServer:
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def closed_port() -> int:
    """A local port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
