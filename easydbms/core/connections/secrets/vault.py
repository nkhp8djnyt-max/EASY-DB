"""Encrypted file vault: the fallback where no system keyring exists (headless Linux, containers).

Secrets are kept in one JSON document encrypted with Fernet. The key is derived from a master
password with scrypt, using a random salt stored next to the ciphertext. The vault starts locked;
``unlock`` creates it on first use and opens it afterwards.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .base import (
    PASSWORD,
    SecretStore,
    SecretStoreError,
    VaultLockedError,
    WrongPasswordError,
    secret_key,
)

_FORMAT_VERSION = 1
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1


class VaultSecretStore(SecretStore):
    name = "encrypted vault"

    def __init__(self, path: Path, *, scrypt_n: int = _SCRYPT_N) -> None:
        self._path = path
        self._scrypt_n = scrypt_n
        self._lock = threading.RLock()
        self._fernet: Fernet | None = None
        self._salt: bytes | None = None
        self._secrets: dict[str, str] = {}

    @property
    def exists(self) -> bool:
        return self._path.exists()

    @property
    def locked(self) -> bool:
        return self._fernet is None

    def unlock(self, master_password: str) -> None:
        """Open the vault (creating an empty one if there is none yet)."""
        if not master_password:
            raise WrongPasswordError("the master password must not be empty")
        with self._lock:
            if self._path.exists():
                self._open_existing(master_password)
            else:
                self._salt = secrets.token_bytes(16)
                self._fernet = Fernet(self._derive(master_password, self._salt, self._scrypt_n))
                self._secrets = {}
                self._save()

    def lock(self) -> None:
        with self._lock:
            self._fernet = None
            self._secrets = {}

    def get(self, connection_id: str, field: str = PASSWORD) -> str | None:
        with self._lock:
            self._require_unlocked()
            return self._secrets.get(secret_key(connection_id, field))

    def set(self, connection_id: str, value: str, field: str = PASSWORD) -> None:
        with self._lock:
            self._require_unlocked()
            self._secrets[secret_key(connection_id, field)] = value
            self._save()

    def delete(self, connection_id: str, field: str = PASSWORD) -> None:
        with self._lock:
            self._require_unlocked()
            if self._secrets.pop(secret_key(connection_id, field), None) is not None:
                self._save()

    # ------------------------------------------------------------------ internals

    def _require_unlocked(self) -> None:
        if self._fernet is None:
            raise VaultLockedError("the vault is locked")

    @staticmethod
    def _derive(master_password: str, salt: bytes, n: int) -> bytes:
        kdf = Scrypt(salt=salt, length=32, n=n, r=_SCRYPT_R, p=_SCRYPT_P)
        return base64.urlsafe_b64encode(kdf.derive(master_password.encode("utf-8")))

    def _open_existing(self, master_password: str) -> None:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            salt = base64.b64decode(document["salt"])
            n = int(document["scrypt_n"])
            token = document["token"].encode("ascii")
            if document["version"] != _FORMAT_VERSION:
                raise SecretStoreError(f"unsupported vault version {document['version']}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise SecretStoreError(f"the vault file is damaged: {error}") from error
        fernet = Fernet(self._derive(master_password, salt, n))
        try:
            payload = fernet.decrypt(token)
        except InvalidToken:
            raise WrongPasswordError("wrong master password") from None
        self._salt, self._scrypt_n = salt, n
        self._fernet = fernet
        self._secrets = json.loads(payload)

    def _save(self) -> None:
        assert self._fernet is not None
        assert self._salt is not None
        token = self._fernet.encrypt(json.dumps(self._secrets).encode("utf-8"))
        document = {
            "version": _FORMAT_VERSION,
            "salt": base64.b64encode(self._salt).decode("ascii"),
            "scrypt_n": self._scrypt_n,
            "token": token.decode("ascii"),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        try:
            os.replace(temporary, self._path)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
