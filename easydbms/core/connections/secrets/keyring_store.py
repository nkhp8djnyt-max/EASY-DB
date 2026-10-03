"""System keyring (Windows Credential Manager, macOS Keychain, Secret Service / KWallet)."""

from __future__ import annotations

import keyring
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError

from .base import PASSWORD, SERVICE_NAME, SecretStore, SecretStoreError, secret_key


def keyring_available(backend: KeyringBackend | None = None) -> bool:
    """Is there a real keyring? The ``fail`` backend of headless systems has priority 0."""
    chosen = backend or keyring.get_keyring()
    try:
        return float(chosen.priority) > 0
    except Exception:  # a backend that cannot even report its priority is unusable
        return False


class KeyringSecretStore(SecretStore):
    name = "system keyring"

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend = backend or keyring.get_keyring()

    def get(self, connection_id: str, field: str = PASSWORD) -> str | None:
        try:
            return self._backend.get_password(SERVICE_NAME, secret_key(connection_id, field))
        except KeyringError as error:
            raise SecretStoreError(f"keyring read failed: {error}") from error

    def set(self, connection_id: str, value: str, field: str = PASSWORD) -> None:
        try:
            self._backend.set_password(SERVICE_NAME, secret_key(connection_id, field), value)
        except KeyringError as error:
            raise SecretStoreError(f"keyring write failed: {error}") from error

    def delete(self, connection_id: str, field: str = PASSWORD) -> None:
        try:
            self._backend.delete_password(SERVICE_NAME, secret_key(connection_id, field))
        except keyring.errors.PasswordDeleteError:
            pass  # nothing stored under that key
        except KeyringError as error:
            raise SecretStoreError(f"keyring delete failed: {error}") from error
