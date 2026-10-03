"""Where passwords live: anywhere but the connection config."""

from __future__ import annotations

from abc import ABC, abstractmethod

SERVICE_NAME = "easydbms"
PASSWORD = "password"
#: The other secrets a connection may have: SSH logins, key passphrases, the TLS client key.
SSH_PASSWORD = "ssh.password"
SSH_PASSPHRASE = "ssh.passphrase"
JUMP_PASSWORD = "jump.password"
JUMP_PASSPHRASE = "jump.passphrase"
SSL_KEY_PASSWORD = "ssl.key_password"
ALL_FIELDS = (
    PASSWORD,
    SSH_PASSWORD,
    SSH_PASSPHRASE,
    JUMP_PASSWORD,
    JUMP_PASSPHRASE,
    SSL_KEY_PASSWORD,
)


class SecretStoreError(Exception):
    """The secret store failed (backend error, corrupt vault, ...)."""


class VaultLockedError(SecretStoreError):
    """The vault must be unlocked with its master password first."""


class WrongPasswordError(SecretStoreError):
    """The master password does not open the vault."""


class SecretStore(ABC):
    """Key/value storage for per-connection secrets such as the database password."""

    #: Human readable backend name, shown in settings and in the password prompt.
    name: str

    @property
    def locked(self) -> bool:
        """``True`` while a master password is required before secrets can be read or written."""
        return False

    @abstractmethod
    def get(self, connection_id: str, field: str = PASSWORD) -> str | None: ...

    @abstractmethod
    def set(self, connection_id: str, value: str, field: str = PASSWORD) -> None: ...

    @abstractmethod
    def delete(self, connection_id: str, field: str = PASSWORD) -> None:
        """Remove a secret; removing one that does not exist is not an error."""

    def delete_all(self, connection_id: str) -> None:
        """Remove every secret of a connection (the password, SSH and key passphrases ...)."""
        for field in ALL_FIELDS:
            self.delete(connection_id, field)


def secret_key(connection_id: str, field: str) -> str:
    return f"{connection_id}:{field}"
