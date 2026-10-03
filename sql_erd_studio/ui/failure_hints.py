"""Plain-language advice for connection failures."""

from __future__ import annotations

from ..core.connections import SecretStoreError, UnresolvedVariableError
from ..core.db import (
    AuthFailed,
    ConnectTimeout,
    DatabaseFileError,
    DatabaseNotFound,
    DnsError,
    InvalidOptionError,
    PortClosed,
    SslError,
)
from .i18n import tr


def hint_for(error: BaseException | None) -> str:
    """One sentence on what to check, or an empty string when there is nothing useful to add."""
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
    if isinstance(error, SslError):
        return tr("TLS negotiation failed. Check the sslmode or certificate parameters.")
    if isinstance(error, DatabaseFileError):
        return tr("Check the path of the database file.")
    if isinstance(error, InvalidOptionError):
        return tr("Check the extra parameters of the connection.")
    if isinstance(error, UnresolvedVariableError):
        return tr("Set the missing environment variables and try again.")
    if isinstance(error, SecretStoreError):
        return tr("The saved password could not be read. Unlock the password store first.")
    return ""
