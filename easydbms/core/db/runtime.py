"""Facts that exist only while a session is being connected, never saved with the connection."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Route:
    """Where to really connect: the local end of an SSH tunnel.

    The configured host stays the *name* of the server (TLS verifies the certificate against it
    and ``~/.pgpass`` is looked up by it); only the network connection goes to ``host:port``.
    """

    host: str
    port: int


@dataclass(frozen=True, slots=True)
class ConnectRuntime:
    route: Route | None = None
    #: Passphrase of the TLS client key file, when that file is encrypted.
    ssl_key_password: str | None = field(default=None, repr=False)
