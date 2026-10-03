"""Asking the user for the vault's master password."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QWidget

from ..core.connections import SecretStore, VaultSecretStore, WrongPasswordError
from .i18n import tr

MAX_ATTEMPTS = 3


def store_label(secrets: SecretStore) -> str:
    """The password store's name in the user's language."""
    labels = {
        "system keyring": tr("system keyring"),
        "encrypted vault": tr("encrypted vault"),
        "memory": tr("memory (until the app is closed)"),
    }
    return labels.get(secrets.name, secrets.name)


def ensure_unlocked(secrets: SecretStore, parent: QWidget | None = None) -> bool:
    """Make sure ``secrets`` can be read and written; ``False`` if the user gave up."""
    if not secrets.locked:
        return True
    if not isinstance(secrets, VaultSecretStore):
        return False
    return _create(secrets, parent) if not secrets.exists else _unlock(secrets, parent)


def _ask(parent: QWidget | None, title: str, label: str) -> str | None:
    text, accepted = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Password)
    return text if accepted else None


def _create(vault: VaultSecretStore, parent: QWidget | None) -> bool:
    intro = tr(
        "No system keyring was found, so passwords are kept in an encrypted file protected by "
        "a master password.\n\nChoose a master password:"
    )
    first = _ask(parent, tr("Create master password"), intro)
    if first is None:
        return False
    second = _ask(parent, tr("Create master password"), tr("Repeat the master password:"))
    if second is None:
        return False
    if first != second or not first:
        QMessageBox.warning(parent, tr("Create master password"), tr("The passwords do not match."))
        return False
    vault.unlock(first)
    return True


def _unlock(vault: VaultSecretStore, parent: QWidget | None) -> bool:
    label = tr("Master password for the saved passwords:")
    for attempt in range(MAX_ATTEMPTS):
        password = _ask(parent, tr("Unlock saved passwords"), label)
        if password is None:
            return False
        try:
            vault.unlock(password)
        except WrongPasswordError:
            if attempt < MAX_ATTEMPTS - 1:
                label = tr("Wrong master password. Try again:")
            continue
        return True
    QMessageBox.warning(parent, tr("Unlock saved passwords"), tr("Wrong master password."))
    return False
