"""The "SSL / TLS" and "SSH tunnel" tabs of the connection form."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import (
    JUMP_PASSPHRASE,
    JUMP_PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    SshAuth,
    SshConfig,
    SshHop,
    SslConfig,
    SslMode,
)
from ..core.dialects import DialectId
from .i18n import tr


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setWordWrap(True)
    return label


class FileField(QWidget):
    """A path with a "Browse…" button."""

    edited = Signal()

    def __init__(self, placeholder: str = "", file_filter: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._filter = file_filter
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.button = QPushButton(tr("Browse…"))
        row.addWidget(self.edit, 1)
        row.addWidget(self.button)
        self.edit.textEdited.connect(lambda _text: self.edited.emit())
        self.button.clicked.connect(self._browse)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Choose a file"), self.text(), self._filter)
        if path:
            self.edit.setText(path)
            self.edited.emit()


# ---------------------------------------------------------------------------- SSL / TLS


def mode_hint(mode: SslMode | None) -> str:
    """The explanation shown under the mode box (translated)."""
    if mode is None:
        return tr("The driver decides: PostgreSQL tries TLS first, MySQL uses it when offered.")
    texts = {
        SslMode.DISABLE: tr("No encryption at all."),
        SslMode.ALLOW: tr("Plain first; TLS only if the server insists (PostgreSQL)."),
        SslMode.PREFER: tr("TLS when the server offers it, otherwise plain."),
        SslMode.REQUIRE: tr("Always encrypted, but the server's identity is not checked."),
        SslMode.VERIFY_CA: tr(
            "Encrypted; the certificate must be signed by the CA below (or a system CA)."
        ),
        SslMode.VERIFY_FULL: tr(
            "Like verify-ca, and the certificate must be issued for the host name."
        ),
    }
    return texts[mode]


class SslEditor(QWidget):
    edited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("Driver default"), None)
        for mode in SslMode:
            self.mode_combo.addItem(mode.value, mode.value)
        self.hint = _muted()
        pem = tr("PEM files (*.pem *.crt *.cer *.key);;All files (*)")
        self.ca_file = FileField("/path/to/ca.pem", pem)
        self.cert_file = FileField("/path/to/client.pem", pem)
        self.key_file = FileField("/path/to/client.key", pem)
        self.key_password = QLineEdit()
        self.key_password.setEchoMode(QLineEdit.EchoMode.Password)
        rows: list[tuple[str, QWidget]] = [
            (tr("Mode"), self.mode_combo),
            ("", self.hint),
            (tr("CA certificate"), self.ca_file),
            (tr("Client certificate"), self.cert_file),
            (tr("Client key"), self.key_file),
            (tr("Key passphrase"), self.key_password),
        ]
        for row, (text, widget) in enumerate(rows):
            if text:
                grid.addWidget(_muted(text), row * 2, 0)
            grid.addWidget(widget, row * 2 + (1 if text else 0), 0)
        grid.setRowStretch(len(rows) * 2, 1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        for field in (self.ca_file, self.cert_file, self.key_file):
            field.edited.connect(self.edited)
        self.key_password.textEdited.connect(lambda _text: self.edited.emit())
        self._on_mode()

    def _on_mode(self, *_: object) -> None:
        self.hint.setText(mode_hint(self.mode))
        self.edited.emit()

    @property
    def mode(self) -> SslMode | None:
        data = self.mode_combo.currentData()
        return SslMode(data) if data else None

    def set_dialect(self, dialect_id: DialectId) -> None:
        """``allow`` is a PostgreSQL mode; hide it for MySQL (it falls back to ``prefer``)."""
        index = self.mode_combo.findData(SslMode.ALLOW.value)
        model: Any = self.mode_combo.model()
        item = model.item(index)
        if item is not None:
            flags = item.flags()
            enabled = dialect_id is DialectId.POSTGRESQL
            item.setFlags(
                flags | Qt.ItemFlag.ItemIsEnabled if enabled else flags & ~Qt.ItemFlag.ItemIsEnabled
            )
        if dialect_id is not DialectId.POSTGRESQL and self.mode is SslMode.ALLOW:
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(SslMode.PREFER.value))

    def load(self, ssl: SslConfig, *, saved_key_password: bool = False) -> None:
        self.mode_combo.setCurrentIndex(
            max(0, self.mode_combo.findData(ssl.mode.value if ssl.mode else None))
        )
        self.ca_file.setText(ssl.ca_file)
        self.cert_file.setText(ssl.cert_file)
        self.key_file.setText(ssl.key_file)
        self.key_password.clear()
        self.key_password.setPlaceholderText(
            tr("A saved passphrase is used. Type to replace it.") if saved_key_password else ""
        )

    def read(self) -> dict[str, Any]:
        mode = self.mode
        return {
            "mode": mode.value if mode is not None else None,
            "ca_file": self.ca_file.text(),
            "cert_file": self.cert_file.text(),
            "key_file": self.key_file.text(),
        }

    @property
    def secrets(self) -> dict[str, str]:
        text = self.key_password.text()
        return {SSL_KEY_PASSWORD: text} if text else {}


# ---------------------------------------------------------------------------- SSH


class SshHopEditor(QGroupBox):
    """One SSH server: where, who, and how to log in."""

    edited = Signal()

    def __init__(
        self, title: str, password_field: str, passphrase_field: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(title, parent)
        self._password_field = password_field
        self._passphrase_field = passphrase_field
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("bastion.example.com")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.port_spin.setFixedWidth(90)
        self.user_edit = QLineEdit()
        self.auth_combo = QComboBox()
        self.auth_combo.setMinimumWidth(150)
        self.auth_combo.addItem(tr("ssh-agent"), SshAuth.AGENT.value)
        self.auth_combo.addItem(tr("Password"), SshAuth.PASSWORD.value)
        self.auth_combo.addItem(tr("Private key"), SshAuth.KEY.value)
        self.key_file = FileField("~/.ssh/id_ed25519", tr("All files (*)"))
        self.secret_label = _muted()
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_label = _muted(tr("Private key file"))
        cells: list[tuple[int, int, str, QWidget, int]] = [
            (0, 0, tr("Host"), self.host_edit, 1),
            (0, 1, tr("Port"), self.port_spin, 1),
            (2, 0, tr("User"), self.user_edit, 1),
            (2, 1, tr("Sign in with"), self.auth_combo, 1),
        ]
        for row, column, text, widget, span in cells:
            grid.addWidget(_muted(text), row, column, 1, span)
            grid.addWidget(widget, row + 1, column, 1, span)
        grid.addWidget(self.key_label, 4, 0, 1, 2)
        grid.addWidget(self.key_file, 5, 0, 1, 2)
        grid.addWidget(self.secret_label, 6, 0, 1, 2)
        grid.addWidget(self.secret_edit, 7, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        self.auth_combo.currentIndexChanged.connect(self._on_auth)
        for edit in (self.host_edit, self.user_edit, self.secret_edit):
            edit.textEdited.connect(lambda _text: self.edited.emit())
        self.port_spin.valueChanged.connect(lambda _value: self.edited.emit())
        self.key_file.edited.connect(self.edited)
        self._on_auth()

    @property
    def auth(self) -> SshAuth:
        data = self.auth_combo.currentData()
        return SshAuth(data) if data else SshAuth.AGENT

    def _on_auth(self, *_: object) -> None:
        auth = self.auth
        self.key_label.setVisible(auth is SshAuth.KEY)
        self.key_file.setVisible(auth is SshAuth.KEY)
        self.secret_label.setVisible(auth is not SshAuth.AGENT)
        self.secret_edit.setVisible(auth is not SshAuth.AGENT)
        self.secret_label.setText(
            tr("Key passphrase (if the key has one)") if auth is SshAuth.KEY else tr("Password")
        )
        self.edited.emit()

    @property
    def secret_field(self) -> str | None:
        if self.auth is SshAuth.PASSWORD:
            return self._password_field
        if self.auth is SshAuth.KEY:
            return self._passphrase_field
        return None

    @property
    def secrets(self) -> dict[str, str]:
        field = self.secret_field
        text = self.secret_edit.text()
        return {field: text} if field and text else {}

    @property
    def applicable_fields(self) -> list[str]:
        field = self.secret_field
        return [field] if field else []

    def load(self, hop: SshHop | None, *, saved: bool = False) -> None:
        self.host_edit.setText(hop.host if hop else "")
        self.port_spin.setValue(hop.port if hop else 22)
        self.user_edit.setText(hop.user if hop else "")
        self.auth_combo.setCurrentIndex(
            max(0, self.auth_combo.findData(hop.auth.value if hop else None))
        )
        self.key_file.setText(hop.key_file if hop else "")
        self.secret_edit.clear()
        self.secret_edit.setPlaceholderText(
            tr("A saved secret is used. Type to replace it.") if saved else ""
        )

    def read(self) -> dict[str, Any]:
        return {
            "host": self.host_edit.text().strip(),
            "port": self.port_spin.value(),
            "user": self.user_edit.text().strip(),
            "auth": self.auth.value,
            "key_file": self.key_file.text() if self.auth is SshAuth.KEY else "",
        }


class SshEditor(QWidget):
    edited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.enable_check = QCheckBox(tr("Connect through an SSH tunnel"))
        self.server = SshHopEditor(tr("SSH server"), SSH_PASSWORD, SSH_PASSPHRASE)
        self.jump_check = QCheckBox(tr("Reach the SSH server through a jump host (bastion)"))
        self.jump = SshHopEditor(tr("Jump host"), JUMP_PASSWORD, JUMP_PASSPHRASE)
        layout.addWidget(self.enable_check)
        layout.addWidget(self.server)
        layout.addWidget(self.jump_check)
        layout.addWidget(self.jump)
        layout.addStretch(1)
        self.enable_check.toggled.connect(self._on_toggled)
        self.jump_check.toggled.connect(self._on_toggled)
        for editor in (self.server, self.jump):
            editor.edited.connect(self.edited)
        self._on_toggled()

    def _on_toggled(self, *_: object) -> None:
        enabled = self.enable_check.isChecked()
        self.server.setEnabled(enabled)
        self.jump_check.setEnabled(enabled)
        self.jump.setVisible(self.jump_check.isChecked())
        self.jump.setEnabled(enabled)
        self.edited.emit()

    @property
    def enabled(self) -> bool:
        return self.enable_check.isChecked()

    def load(
        self, ssh: SshConfig | None, *, saved: set[str] | frozenset[str] = frozenset()
    ) -> None:
        self.enable_check.setChecked(ssh is not None)
        self.jump_check.setChecked(ssh is not None and ssh.jump is not None)
        self.server.load(
            ssh.server if ssh else None, saved=bool(saved & set(self.server.applicable_fields))
        )
        self.jump.load(
            ssh.jump if ssh else None, saved=bool(saved & set(self.jump.applicable_fields))
        )
        self._on_toggled()

    def read(self) -> dict[str, Any] | None:
        """The SSH settings as plain data, ``None`` when the tunnel is off."""
        if not self.enabled:
            return None
        return {
            "server": self.server.read(),
            "jump": self.jump.read() if self.jump_check.isChecked() else None,
        }

    @property
    def secrets(self) -> dict[str, str]:
        if not self.enabled:
            return {}
        found = dict(self.server.secrets)
        if self.jump_check.isChecked():
            found.update(self.jump.secrets)
        return found

    @property
    def applicable_fields(self) -> list[str]:
        if not self.enabled:
            return []
        fields = list(self.server.applicable_fields)
        if self.jump_check.isChecked():
            fields += self.jump.applicable_fields
        return fields
