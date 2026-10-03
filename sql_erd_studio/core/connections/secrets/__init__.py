"""Password storage backends."""

from pathlib import Path

from .base import (
    PASSWORD,
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
    "PASSWORD",
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
