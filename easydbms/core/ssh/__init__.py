"""SSH tunnels to the database: connection, host-key trust, keys."""

from .errors import (
    SshAuthFailed,
    SshError,
    SshHostKeyChanged,
    SshHostKeyUnknown,
    SshKeyError,
    SshSecretRequired,
    SshTunnelError,
    SshUnreachable,
)
from .hostkeys import KnownHosts, Verdict, fingerprint, host_label
from .keys import is_encrypted, load_key
from .tunnel import LOCAL_HOST, SshTunnel

__all__ = [
    "LOCAL_HOST",
    "KnownHosts",
    "SshAuthFailed",
    "SshError",
    "SshHostKeyChanged",
    "SshHostKeyUnknown",
    "SshKeyError",
    "SshSecretRequired",
    "SshTunnel",
    "SshTunnelError",
    "SshUnreachable",
    "Verdict",
    "fingerprint",
    "host_label",
    "is_encrypted",
    "load_key",
]
