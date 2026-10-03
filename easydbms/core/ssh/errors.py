"""Why an SSH tunnel could not be opened (all of them are connection failures)."""

from __future__ import annotations

from typing import Any

from ..db.errors import ConnectionFailed


class SshError(ConnectionFailed):
    """The SSH tunnel could not be set up."""


class SshUnreachable(SshError):
    """The SSH server (or the jump host) cannot be reached: DNS, firewall, nothing listening."""


class SshAuthFailed(SshError):
    """The SSH server did not accept the credentials."""


class SshKeyError(SshError):
    """The private key file is missing, unreadable or not a key."""


class SshSecretRequired(SshError):
    """A password or key passphrase is needed but was not supplied (``field`` names it)."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


class SshTunnelError(SshError):
    """The SSH server is fine, but it cannot forward to the database host and port."""


class SshHostKeyUnknown(SshError):
    """The SSH server's key is not in any known-hosts file: the user has to decide to trust it."""

    def __init__(
        self, host: str, port: int, key_type: str, fingerprint: str, key: Any = None
    ) -> None:
        super().__init__(
            f"the identity of {host}:{port} is not known yet ({key_type} key {fingerprint})"
        )
        #: The server's public key, for ``KnownHosts.trust`` once the person confirmed it.
        self.key = key
        self.host = host
        self.port = port
        self.key_type = key_type
        self.fingerprint = fingerprint


class SshHostKeyChanged(SshError):
    """The SSH server presents a different key than the one remembered: possibly an attack."""

    def __init__(self, host: str, port: int, fingerprint: str, known: str) -> None:
        super().__init__(
            f"the key of {host}:{port} has CHANGED (now {fingerprint}, remembered {known}). "
            "Someone may be intercepting the connection."
        )
        self.host = host
        self.port = port
        self.fingerprint = fingerprint
        self.known = known
