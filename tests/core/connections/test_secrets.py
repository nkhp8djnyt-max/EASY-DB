from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import KeyringError, PasswordDeleteError

from sql_erd_studio.core.connections import (
    KeyringSecretStore,
    MemorySecretStore,
    SecretStore,
    SecretStoreError,
    VaultLockedError,
    VaultSecretStore,
    WrongPasswordError,
    choose_secret_store,
)
from sql_erd_studio.core.connections.secrets import keyring_available


class FakeKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[tuple[str, str], str] = {}
        self.broken = False

    def get_password(self, service: str, username: str) -> str | None:
        if self.broken:
            raise KeyringError("locked")
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.broken:
            raise KeyringError("locked")
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.broken:
            raise KeyringError("locked")
        try:
            del self.data[(service, username)]
        except KeyError:
            raise PasswordDeleteError("missing") from None


def vault(path: Path) -> VaultSecretStore:
    return VaultSecretStore(path, scrypt_n=2**10)  # cheap key derivation for tests


@pytest.fixture(params=["memory", "keyring", "vault"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> SecretStore:
    if request.param == "memory":
        return MemorySecretStore()
    if request.param == "keyring":
        return KeyringSecretStore(FakeKeyring())
    opened = vault(tmp_path / "vault.json")
    opened.unlock("master")
    return opened


def test_secret_store_contract(store: SecretStore) -> None:
    assert not store.locked
    assert store.get("c1") is None
    store.set("c1", "pw1")
    store.set("c2", "pw2")
    store.set("c1", "token", field="extra")
    assert store.get("c1") == "pw1"
    assert store.get("c2") == "pw2"
    assert store.get("c1", "extra") == "token"
    store.set("c1", "changed")
    assert store.get("c1") == "changed"
    store.delete("c1")
    assert store.get("c1") is None
    assert store.get("c2") == "pw2"
    store.delete("c1")  # deleting twice is fine
    store.delete_all("c2")
    assert store.get("c2") is None


def test_secrets_with_awkward_characters(store: SecretStore) -> None:
    value = "pä ss'w\"ord\n\\ ✓ 😀 ${NOT_EXPANDED}"
    store.set("c", value)
    assert store.get("c") == value


def test_keyring_errors_are_wrapped() -> None:
    backend = FakeKeyring()
    store = KeyringSecretStore(backend)
    backend.broken = True
    for call in (lambda: store.get("c"), lambda: store.set("c", "x"), lambda: store.delete("c")):
        with pytest.raises(SecretStoreError):
            call()


def test_keyring_availability() -> None:
    assert not keyring_available(fail.Keyring())
    assert keyring_available(FakeKeyring())


def test_choose_secret_store_prefers_the_keyring_and_falls_back_to_the_vault(
    tmp_path: Path,
) -> None:
    previous = keyring.get_keyring()
    try:
        keyring.set_keyring(FakeKeyring())
        assert isinstance(choose_secret_store(tmp_path / "v.json"), KeyringSecretStore)
        keyring.set_keyring(fail.Keyring())
        chosen = choose_secret_store(tmp_path / "v.json")
        assert isinstance(chosen, VaultSecretStore)
        assert chosen.locked
    finally:
        keyring.set_keyring(previous)


def test_vault_starts_locked_and_refuses_everything(tmp_path: Path) -> None:
    locked = vault(tmp_path / "vault.json")
    assert locked.locked
    assert not locked.exists
    for call in (lambda: locked.get("c"), lambda: locked.set("c", "x"), lambda: locked.delete("c")):
        with pytest.raises(VaultLockedError):
            call()


def test_vault_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    first = vault(path)
    first.unlock("master")
    first.set("c1", "pw1")
    assert path.exists()

    second = vault(path)
    assert second.locked
    second.unlock("master")
    assert second.get("c1") == "pw1"


def test_vault_wrong_password_and_relock(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    first = vault(path)
    first.unlock("master")
    first.set("c", "pw")
    first.lock()
    assert first.locked
    with pytest.raises(VaultLockedError):
        first.get("c")
    with pytest.raises(WrongPasswordError):
        first.unlock("not the master")
    assert first.locked
    first.unlock("master")
    assert first.get("c") == "pw"
    with pytest.raises(WrongPasswordError, match="empty"):
        vault(tmp_path / "other.json").unlock("")


def test_vault_file_is_encrypted_and_private(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    store = vault(path)
    store.unlock("master")
    store.set("conn-id-123", "hunter2-very-secret")
    raw = path.read_text()
    assert "hunter2" not in raw
    assert "conn-id-123" not in raw
    assert json.loads(raw)["version"] == 1
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_vault_uses_a_fresh_salt_per_vault(tmp_path: Path) -> None:
    a, b = vault(tmp_path / "a.json"), vault(tmp_path / "b.json")
    a.unlock("same")
    b.unlock("same")
    assert (
        json.loads((tmp_path / "a.json").read_text())["salt"]
        != json.loads((tmp_path / "b.json").read_text())["salt"]
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        '{"version": 1}',
        '{"version": 99, "salt": "AA==", "scrypt_n": 1024, "token": "x"}',
    ],
)
def test_damaged_vault_files_are_reported_not_overwritten(tmp_path: Path, content: str) -> None:
    path = tmp_path / "vault.json"
    path.write_text(content)
    with pytest.raises(SecretStoreError, match=r"damaged|unsupported"):
        vault(path).unlock("master")
    assert path.read_text() == content
