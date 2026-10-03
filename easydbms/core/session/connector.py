"""Everything that happens between "connect" and the first driver call.

``prepare`` resolves ``${ENV}``, fills the connection from ``pg_service.conf`` / ``~/.pgpass``,
opens the SSH tunnel and returns what the client needs (config, password, :class:`ConnectRuntime`).
``check_connection`` does the same step by step for the "Test connection" button and then lets the
client test the database itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..connections import (
    SSL_KEY_PASSWORD,
    FileConnection,
    ServerConnection,
    complete,
    resolve_config,
)
from ..connections.providers import Token, get_provider
from ..db import (
    CheckStep,
    ConnectionCheck,
    ConnectionFailed,
    ConnectRuntime,
    DatabaseClient,
    DbError,
    InvalidConnection,
    Route,
    create_client,
)
from ..ssh import LOCAL_HOST, KnownHosts, SshError, SshTunnel

#: ``(config, password, runtime)`` -> an unconnected client.
ClientFactory = Callable[
    [ServerConnection | FileConnection, str | None, ConnectRuntime], DatabaseClient
]
StepSink = Callable[[CheckStep], None]


@dataclass(slots=True)
class Prepared:
    """A connection ready to hand to a client; ``close`` shuts the tunnel (if any)."""

    config: ServerConnection | FileConnection
    password: str | None
    runtime: ConnectRuntime
    tunnel: SshTunnel | None = None
    steps: list[CheckStep] = field(default_factory=list)
    #: The cloud token that ``password`` came from, and how to get a new one when it runs out.
    token: Token | None = None
    renew: Callable[[], Token] | None = None

    def current_password(self) -> str | None:
        """The password to connect with now: a cloud token is renewed once it is about to expire
        (a session opens its second connection long after the first)."""
        if self.token is not None and self.renew is not None and not self.token.valid():
            self.token = self.renew()
            self.password = self.token.password
        return self.password

    def close(self) -> None:
        if self.tunnel is not None:
            self.tunnel.close()


def prepare(
    config: ServerConnection | FileConnection,
    password: str | None,
    secrets: Mapping[str, str],
    environ: Mapping[str, str],
    known_hosts: KnownHosts | None,
    *,
    on_step: StepSink | None = None,
    timeout: float = 10.0,
) -> Prepared:
    """Get ``config`` ready to connect (``DbError`` / ``ValueError`` if not; nothing stays open).

    ``secrets`` holds the SSH passwords / passphrases and the TLS key passphrase by field name.
    """
    steps: list[CheckStep] = []

    def report(step: CheckStep) -> None:
        steps.append(step)
        if on_step is not None:
            on_step(step)

    resolved, resolved_password = resolve_config(config, password, environ)
    if isinstance(resolved, FileConnection):
        return Prepared(resolved, resolved_password, ConnectRuntime(), None, steps)
    completed = complete(resolved, resolved_password, secrets.get(SSL_KEY_PASSWORD), environ)
    for name, detail in completed.notes:
        report(CheckStep(name, True, detail, 0.0))
    server = completed.config
    runtime = ConnectRuntime(ssl_key_password=completed.ssl_key_password)
    token: Token | None = None
    renew: Callable[[], Token] | None = None
    password = completed.password
    if server.provider is not None and get_provider(server.provider.kind).uses_token:
        provider = get_provider(server.provider.kind)
        params = server.provider.params

        def renew() -> Token:
            return provider.token(server, params, environ)

        started = time.perf_counter()
        try:
            token = renew()
        except (DbError, ValueError) as error:
            report(CheckStep("Cloud", False, str(error), time.perf_counter() - started))
            raise
        password = token.password
        report(
            CheckStep(
                "Cloud",
                True,
                f"{provider.title} — {provider.describe(token)}",
                time.perf_counter() - started,
            )
        )
    if server.ssh is None:
        return Prepared(server, password, runtime, None, steps, token, renew)
    if known_hosts is None:
        raise SshError("no known-hosts store is configured, so SSH servers cannot be verified")
    port = server.effective_port
    assert port is not None  # server dialects have a default port
    tunnel = SshTunnel(server.ssh, (server.host, port), secrets, known_hosts, timeout=timeout)
    started = time.perf_counter()

    def opened(name: str, detail: str, seconds: float) -> None:
        report(CheckStep(name, True, detail, seconds))

    try:
        tunnel.open(opened)
    except (DbError, ValueError) as error:
        report(CheckStep(tunnel.stage or "SSH", False, str(error), time.perf_counter() - started))
        raise
    runtime = ConnectRuntime(Route(LOCAL_HOST, tunnel.local_port), completed.ssl_key_password)
    return Prepared(server, password, runtime, tunnel, steps, token, renew)


def check_connection(
    config: ServerConnection | FileConnection,
    password: str | None,
    secrets: Mapping[str, str],
    environ: Mapping[str, str],
    known_hosts: KnownHosts | None,
    client_factory: ClientFactory = create_client,
    *,
    timeout: float = 10.0,
) -> ConnectionCheck:
    """The "Test connection" report: tunnel and file steps first, then the client's own steps."""
    steps: list[CheckStep] = []
    try:
        prepared = prepare(
            config, password, secrets, environ, known_hosts, on_step=steps.append, timeout=timeout
        )
    except (DbError, ValueError) as error:
        failure = (
            error if isinstance(error, ConnectionFailed) else InvalidConnection(str(error), error)
        )
        if not steps or steps[-1].ok:  # nothing reported the failure yet (e.g. a missing variable)
            name = "Service" if "service" in str(error) else "Settings"
            steps.append(CheckStep(name, False, str(error), 0.0))
        return ConnectionCheck(tuple(steps), None, failure)
    try:
        check = client_factory(prepared.config, prepared.password, prepared.runtime).test()
    finally:
        prepared.close()
    return ConnectionCheck((*steps, *check.steps), check.server_version, check.failure)


__all__ = ["ClientFactory", "Prepared", "StepSink", "check_connection", "prepare"]
