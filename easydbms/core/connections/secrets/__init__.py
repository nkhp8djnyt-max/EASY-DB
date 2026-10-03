"""Password storage backends."""

from pathlib import Path

from .base import (
    ALL_FIELDS,
    JUMP_PASSPHRASE,
    JUMP_PASSWORD,
    PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    SecretStore,
    SecretStoreError,
    VaultLockedError,
    WrongPasswordError,
)
from .keyring_store import KeyringSecretStore, keyring_available
from .memory import MemorySecretStore
from .vault import VaultSecretStore


def choose_secret_store(vault_path: Path) -> SecretStore:
    """The system keyring when there is one, otherwise the encrypted vault (starts locked)."""
    if keyring_available():
        return KeyringSecretStore()
    return VaultSecretStore(vault_path)


__all__ = [
    "ALL_FIELDS",
    "JUMP_PASSPHRASE",
    "JUMP_PASSWORD",
    "PASSWORD",
    "SSH_PASSPHRASE",
    "SSH_PASSWORD",
    "SSL_KEY_PASSWORD",
    "KeyringSecretStore",
    "MemorySecretStore",
    "SecretStore",
    "SecretStoreError",
    "VaultLockedError",
    "VaultSecretStore",
    "WrongPasswordError",
    "choose_secret_store",
    "keyring_available",
]
