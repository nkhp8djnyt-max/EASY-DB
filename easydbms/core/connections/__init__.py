"""Saved connections: models, URL handling, secrets and persistence."""

from .env import UnresolvedVariableError, expand, has_placeholders, resolve_config
from .models import (
    ConnectionColor,
    ConnectionConfig,
    FileConnection,
    ServerConnection,
    parse_config,
    replace,
)
from .secrets import (
    KeyringSecretStore,
    MemorySecretStore,
    SecretStore,
    SecretStoreError,
    VaultLockedError,
    VaultSecretStore,
    WrongPasswordError,
    choose_secret_store,
)
from .store import ConnectionNotFoundError, ConnectionStore
from .url_parser import (
    MEMORY_DATABASE,
    ConnectionUrlError,
    ParsedUrl,
    build_connection_url,
    parse_connection_url,
)

__all__ = [
    "MEMORY_DATABASE",
    "ConnectionColor",
    "ConnectionConfig",
    "ConnectionNotFoundError",
    "ConnectionStore",
    "ConnectionUrlError",
    "FileConnection",
    "KeyringSecretStore",
    "MemorySecretStore",
    "ParsedUrl",
    "SecretStore",
    "SecretStoreError",
    "ServerConnection",
    "UnresolvedVariableError",
    "VaultLockedError",
    "VaultSecretStore",
    "WrongPasswordError",
    "build_connection_url",
    "choose_secret_store",
    "expand",
    "has_placeholders",
    "parse_config",
    "parse_connection_url",
    "replace",
    "resolve_config",
]
