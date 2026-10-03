"""In-memory secret store: for tests and for sessions that must not persist anything."""

from __future__ import annotations

from .base import PASSWORD, SecretStore, secret_key


class MemorySecretStore(SecretStore):
    name = "memory"

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, connection_id: str, field: str = PASSWORD) -> str | None:
        return self._values.get(secret_key(connection_id, field))

    def set(self, connection_id: str, value: str, field: str = PASSWORD) -> None:
        self._values[secret_key(connection_id, field)] = value

    def delete(self, connection_id: str, field: str = PASSWORD) -> None:
        self._values.pop(secret_key(connection_id, field), None)
