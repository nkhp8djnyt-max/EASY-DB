"""Plain-language advice for connection failures."""

from __future__ import annotations

from ..core.connections import SecretStoreError, ServiceNotFoundError, UnresolvedVariableError
from ..core.connections.providers import ProviderAuthError, ProviderError, ProviderUnavailable
from ..core.db import (
    AuthFailed,
    ConnectTimeout,
    DatabaseFileError,
    DatabaseNotFound,
    DnsError,
    InvalidConnection,
    InvalidOptionError,
    PortClosed,
    SslError,
    SslFileError,
)
from ..core.ssh import (
    SshAuthFailed,
    SshHostKeyChanged,
    SshHostKeyUnknown,
    SshKeyError,
    SshSecretRequired,
    SshTunnelError,
    SshUnreachable,
)
from .i18n import tr


def hint_for(error: BaseException | None) -> str:
    """One sentence on what to check, or an empty string when there is nothing useful to add."""
    if isinstance(error, ProviderUnavailable):
        return tr(
            "Install the provider's package: pip install 'easydbms[{extra}]'", extra=error.extra
        )
    if isinstance(error, ProviderAuthError):
        return tr("Sign in to the cloud account (CLI or environment variables) and try again.")
    if isinstance(error, ProviderError):
        return tr("Check the settings in the Cloud tab.")
    if isinstance(error, InvalidConnection):
        return hint_for(error.cause)
    if isinstance(error, DnsError):
        return tr("Check the host name, your network connection and VPN.")
    if isinstance(error, PortClosed):
        return tr(
            "The host is reachable but nothing accepts connections on this port. "
            "Check that the server is running and that the port is correct."
        )
    if isinstance(error, ConnectTimeout):
        return tr("The server did not answer in time. A firewall may be dropping the connection.")
    if isinstance(error, AuthFailed):
        return tr("Check the user name and the password.")
    if isinstance(error, DatabaseNotFound):
        return tr("The server is reachable, but it has no database with this name.")
    if isinstance(error, SslFileError):
        return tr("Fix the certificate or key file named above in the SSL / TLS tab.")
    if isinstance(error, SslError):
        return tr(
            "TLS negotiation failed. Check the mode and the certificates in the SSL / TLS tab."
        )
    if isinstance(error, SshHostKeyUnknown):
        return tr(
            "This SSH server has not been seen before. Compare its fingerprint with the "
            "administrator's, then trust it."
        )
    if isinstance(error, SshHostKeyChanged):
        return tr(
            "The SSH server's key is not the one you trusted. If nobody reinstalled the server, "
            "do not connect: somebody may be intercepting the connection."
        )
    if isinstance(error, SshSecretRequired):
        return tr("Type the SSH password or key passphrase (SSH tunnel tab) and try again.")
    if isinstance(error, SshAuthFailed):
        return tr("The SSH server refused the login. Check the user, the password or the key.")
    if isinstance(error, SshKeyError):
        return tr("Check the SSH private key file and its passphrase.")
    if isinstance(error, SshTunnelError):
        return tr(
            "The SSH login worked, but the SSH server cannot reach the database host and port. "
            "Check them from the SSH server's point of view."
        )
    if isinstance(error, SshUnreachable):
        return tr("Check the SSH host name and port, your network connection and VPN.")
    if isinstance(error, ServiceNotFoundError):
        return tr("Define the service in ~/.pg_service.conf or choose another one.")
    if isinstance(error, DatabaseFileError):
        return tr("Check the path of the database file.")
    if isinstance(error, InvalidOptionError):
        return tr("Check the extra parameters of the connection.")
    if isinstance(error, UnresolvedVariableError):
        return tr("Set the missing environment variables and try again.")
    if isinstance(error, SecretStoreError):
        return tr("The saved password could not be read. Unlock the password store first.")
    return ""
