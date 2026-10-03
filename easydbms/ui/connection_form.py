"""The connection form: fields, URL synchronisation, validation.

The form knows nothing about stores, secrets or threads; it turns user input into a validated
connection config and back, which keeps it easy to test.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, quote

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import (
    SSL_KEY_PASSWORD,
    ConnectionColor,
    ConnectionUrlError,
    FileConnection,
    ServerConnection,
    SslConfig,
    build_connection_url,
    load_pgpass,
    load_services,
    parse_config,
    parse_connection_url,
)
from ..core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect, DialectId
from .connection_cloud import CloudEditor
from .connection_security import SshEditor, SslEditor
from .i18n import tr
from .icons import dot_icon
from .theme import COLOR_HEX

DIALECT_LABELS: dict[DialectId, str] = {
    DialectId.POSTGRESQL: "PostgreSQL",
    DialectId.MYSQL: "MySQL / MariaDB",
    DialectId.SQLITE: "SQLite",
}
_DIALECTS: dict[DialectId, Dialect] = {
    DialectId.POSTGRESQL: POSTGRESQL,
    DialectId.MYSQL: MYSQL,
    DialectId.SQLITE: SQLITE,
}


def color_label(color: ConnectionColor) -> str:
    labels = {
        ConnectionColor.RED: tr("Red"),
        ConnectionColor.ORANGE: tr("Orange"),
        ConnectionColor.YELLOW: tr("Yellow"),
        ConnectionColor.GREEN: tr("Green"),
        ConnectionColor.TEAL: tr("Teal"),
        ConnectionColor.BLUE: tr("Blue"),
        ConnectionColor.PURPLE: tr("Purple"),
        ConnectionColor.GRAY: tr("Gray"),
    }
    return labels[color]


class FormError(ValueError):
    """The form does not describe a valid connection; the message is shown to the user."""


class ConnectionForm(QWidget):
    #: Any edit by the user (not emitted while the form is being loaded programmatically).
    changed = Signal()

    def __init__(self, secret_store_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing = False
        self._applied: dict[str, str] = {}
        self._build(secret_store_name)
        self._wire()
        self.load(None)

    # ------------------------------------------------------------------ construction

    def _build(self, secret_store_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        head = QGridLayout()
        head.setHorizontalSpacing(10)
        head.setVerticalSpacing(4)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr("My database"))
        self.color_combo = QComboBox()
        self.color_combo.addItem(tr("No colour"), None)
        for color in ConnectionColor:
            self.color_combo.addItem(dot_icon(COLOR_HEX[color]), color_label(color), color)
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.lineEdit().setPlaceholderText(tr("No group"))  # type: ignore[union-attr]
        for column, (text, widget) in enumerate(
            [
                (tr("Name"), self.name_edit),
                (tr("Colour"), self.color_combo),
                (tr("Group"), self.group_combo),
            ]
        ):
            label = QLabel(text)
            label.setProperty("muted", True)
            head.addWidget(label, 0, column)
            head.addWidget(widget, 1, column)
        head.setColumnStretch(0, 3)
        head.setColumnStretch(1, 1)
        head.setColumnStretch(2, 2)
        layout.addLayout(head)

        kinds = QHBoxLayout()
        kinds.setSpacing(18)
        self._dialect_group = QButtonGroup(self)
        self.dialect_buttons: dict[DialectId, QRadioButton] = {}
        for dialect_id, label_text in DIALECT_LABELS.items():
            button = QRadioButton(label_text)
            self._dialect_group.addButton(button)
            self.dialect_buttons[dialect_id] = button
            kinds.addWidget(button)
        kinds.addStretch(1)
        layout.addLayout(kinds)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_url_tab(), tr("URL"))
        self.tabs.addTab(self._build_details_tab(), tr("Host / Port"))
        self.ssl_editor = SslEditor()
        self.ssh_editor = SshEditor()
        self.tabs.addTab(self._scrolled(self.ssl_editor), tr("SSL / TLS"))
        self.tabs.addTab(self._scrolled(self.ssh_editor), tr("SSH tunnel"))
        self.cloud_editor = CloudEditor()
        self.tabs.addTab(self._scrolled(self.cloud_editor), tr("Cloud"))
        layout.addWidget(self.tabs)

        self.password_widget = QWidget()
        password_layout = QVBoxLayout(self.password_widget)
        password_layout.setContentsMargins(0, 0, 0, 0)
        password_layout.setSpacing(4)
        password_label = QLabel(tr("Password"))
        password_label.setProperty("muted", True)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_password_check = QCheckBox(
            tr("Save the password in the {store}", store=secret_store_name or tr("password store"))
        )
        self.save_password_check.setToolTip(
            tr("SSH passwords and key passphrases are kept the same way.")
        )
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.password_edit)
        password_layout.addWidget(self.save_password_check)
        layout.addWidget(self.password_widget)

        flags = QHBoxLayout()
        flags.setSpacing(22)
        self.read_only_check = QCheckBox(tr("Read-only"))
        self.production_check = QCheckBox(tr("Production database"))
        flags.addWidget(self.read_only_check)
        flags.addWidget(self.production_check)
        flags.addStretch(1)
        layout.addLayout(flags)

    def _build_url_tab(self) -> QWidget:
        page = QWidget()
        page.setProperty("panel", True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        label = QLabel(tr("Connection URL"))
        label.setProperty("muted", True)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("postgresql://user@host:5432/database?sslmode=require")
        self.url_error = QLabel()
        self.url_error.setProperty("error", True)
        self.url_error.setWordWrap(True)
        self.url_error.hide()
        hint = QLabel(tr("Paste a URL and the fields update. The password is kept separately."))
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(self.url_edit)
        layout.addWidget(self.url_error)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _build_details_tab(self) -> QWidget:
        page = QWidget()
        page.setProperty("panel", True)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(14, 14, 14, 14)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)

        server = QWidget()
        grid = QGridLayout(server)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("localhost")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setFixedWidth(110)
        self.user_edit = QLineEdit()
        self.database_edit = QLineEdit()
        self.params_edit = QLineEdit()
        self.params_edit.setPlaceholderText("connect_timeout=5&application_name=easydbms")
        self.service_combo = QComboBox()
        self.service_combo.setEditable(True)
        self.service_combo.lineEdit().setPlaceholderText(  # type: ignore[union-attr]
            tr("optional — a service from pg_service.conf")
        )
        self.service_label = QLabel(tr("Service"))
        self.pgpass_hint = QLabel()
        self.pgpass_hint.setProperty("muted", True)
        self.pgpass_hint.setWordWrap(True)
        cells = [
            (0, 0, tr("Host"), self.host_edit, 1),
            (0, 1, tr("Port"), self.port_spin, 1),
            (2, 0, tr("User"), self.user_edit, 2),
            (4, 0, tr("Database"), self.database_edit, 2),
            (6, 0, tr("Extra parameters"), self.params_edit, 2),
        ]
        for row, column, text, widget, span in cells:
            label = QLabel(text)
            label.setProperty("muted", True)
            grid.addWidget(label, row, column, 1, span)
            grid.addWidget(widget, row + 1, column, 1, span)
        self.service_label.setProperty("muted", True)
        grid.addWidget(self.service_label, 8, 0, 1, 2)
        grid.addWidget(self.service_combo, 9, 0, 1, 2)
        grid.addWidget(self.pgpass_hint, 10, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(11, 1)
        self.stack.addWidget(server)

        file_page = QWidget()
        file_layout = QVBoxLayout(file_page)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(4)
        path_label = QLabel(tr("Database file"))
        path_label.setProperty("muted", True)
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/path/to/database.sqlite")
        self.browse_button = QPushButton(tr("Open…"))
        self.new_file_button = QPushButton(tr("New…"))
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(self.browse_button)
        path_row.addWidget(self.new_file_button)
        self.create_check = QCheckBox(tr("Create the file if it does not exist"))
        file_layout.addWidget(path_label)
        file_layout.addLayout(path_row)
        file_layout.addWidget(self.create_check)
        file_layout.addStretch(1)
        self.stack.addWidget(file_page)
        return page

    @staticmethod
    def _scrolled(content: QWidget) -> QWidget:
        page = QWidget()
        page.setProperty("panel", True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        area.setWidget(content)
        layout.addWidget(area)
        return page

    def _wire(self) -> None:
        for button in self.dialect_buttons.values():
            button.toggled.connect(self._on_dialect_toggled)
        self.url_edit.textEdited.connect(self._on_url_typing)
        self.url_edit.editingFinished.connect(self._on_url_finished)
        for edit in (
            self.host_edit,
            self.user_edit,
            self.database_edit,
            self.params_edit,
            self.path_edit,
        ):
            edit.textEdited.connect(self._on_field_edited)
        self.port_spin.valueChanged.connect(self._on_field_edited)
        self.create_check.toggled.connect(self._on_field_edited)
        self.service_combo.editTextChanged.connect(self._on_field_edited)
        self.ssl_editor.edited.connect(self._on_field_edited)
        self.ssh_editor.edited.connect(self._emit_changed)
        self.cloud_editor.edited.connect(self._on_cloud_edited)
        self.cloud_editor.chosen.connect(self._on_provider_chosen)
        self.name_edit.textEdited.connect(self._emit_changed)
        self.group_combo.editTextChanged.connect(self._emit_changed)
        self.color_combo.currentIndexChanged.connect(self._emit_changed)
        self.password_edit.textEdited.connect(self._emit_changed)
        for check in (self.save_password_check, self.read_only_check, self.production_check):
            check.toggled.connect(self._emit_changed)
        self.browse_button.clicked.connect(self._browse)
        self.new_file_button.clicked.connect(self._new_file)

    # ------------------------------------------------------------------ public API

    @property
    def dialect_id(self) -> DialectId:
        for dialect_id, button in self.dialect_buttons.items():
            if button.isChecked():
                return dialect_id
        return DialectId.POSTGRESQL

    @property
    def password(self) -> str:
        return self.password_edit.text()

    @property
    def save_password(self) -> bool:
        return self.save_password_check.isChecked()

    @property
    def secrets(self) -> dict[str, str]:
        """The SSH passwords / passphrases and TLS key passphrase typed into the form."""
        if self.dialect_id is DialectId.SQLITE:
            return {}
        return {**self.ssh_editor.secrets, **self.ssl_editor.secrets}

    @property
    def applicable_secret_fields(self) -> list[str]:
        """The secret fields (besides the password) this configuration can use."""
        if self.dialect_id is DialectId.SQLITE:
            return []
        fields = self.ssh_editor.applicable_fields
        if self.ssl_editor.key_file.text():
            fields.append(SSL_KEY_PASSWORD)
        return fields

    def set_groups(self, groups: list[str]) -> None:
        current = self.group_combo.currentText()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItems(groups)
        self.group_combo.setCurrentText(current)
        self.group_combo.blockSignals(False)

    def load(
        self,
        config: ServerConnection | FileConnection | None,
        *,
        has_saved_password: bool = False,
        saved_secrets: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        """Fill the form from ``config`` (``None`` = a blank new connection)."""
        self._syncing = True
        try:
            self.name_edit.setText(config.name if config else "")
            self.color_combo.setCurrentIndex(
                max(0, self.color_combo.findData(config.color if config else None))
            )
            self.group_combo.setCurrentText(config.group if config else "")
            self.read_only_check.setChecked(config.read_only if config else False)
            self.production_check.setChecked(config.production if config else False)
            self.password_edit.clear()
            self.url_error.hide()
            self.url_edit.setProperty("invalid", False)
            self._restyle(self.url_edit)
            if isinstance(config, FileConnection):
                self._select_dialect(DialectId.SQLITE)
                self.path_edit.setText(config.path)
                self.create_check.setChecked(config.create_if_missing)
                self.host_edit.clear()
                self.user_edit.clear()
                self.database_edit.clear()
                self.params_edit.clear()
                self.port_spin.setValue(0)
                self.save_password_check.setChecked(False)
                self.service_combo.setEditText("")
                self.ssl_editor.load(SslConfig())
                self.ssh_editor.load(None)
                self.cloud_editor.load(None)
            else:
                dialect_id = config.dialect if config else DialectId.POSTGRESQL
                self._select_dialect(DialectId(dialect_id))
                self.host_edit.setText(config.host if config else "localhost")
                self.port_spin.setValue(config.port or 0 if config else 0)
                self.user_edit.setText(config.user if config else "")
                self.database_edit.setText(config.database if config else "")
                self.params_edit.setText(_encode_options(config.options) if config else "")
                self.path_edit.clear()
                self.create_check.setChecked(False)
                self.save_password_check.setChecked(config.save_password if config else True)
                self._fill_services()
                self.service_combo.setEditText(config.service if config else "")
                self.ssl_editor.load(
                    config.ssl if config else SslConfig(),
                    saved_key_password=SSL_KEY_PASSWORD in saved_secrets,
                )
                self.ssh_editor.load(config.ssh if config else None, saved=saved_secrets)
                self.cloud_editor.load(config.provider if config else None)
            self._apply_dialect_ui()
            self.password_edit.setPlaceholderText(
                tr("A saved password is used. Type to replace it.") if has_saved_password else ""
            )
            self._refresh_url()
            self._refresh_pgpass_hint()
        finally:
            self._syncing = False

    def read_config(self, connection_id: str) -> ServerConnection | FileConnection:
        """The connection described by the form; raises :class:`FormError` with a user message."""
        data = self._collect(connection_id)
        name = str(data["name"])
        if not name.strip():
            raise FormError(tr("Give the connection a name."))
        if data["kind"] == "file":
            if not str(data["path"]).strip():
                raise FormError(tr("Choose the database file."))
        elif not str(data["host"]).strip() and not str(data["service"]).strip():
            raise FormError(tr("Enter the host name (or a service)."))
        elif isinstance(data.get("ssh"), dict):
            ssh = data["ssh"]
            assert isinstance(ssh, dict)
            if not str(ssh["server"]["host"]).strip():
                raise FormError(tr("Enter the SSH server host name."))
            if isinstance(ssh["jump"], dict) and not str(ssh["jump"]["host"]).strip():
                raise FormError(tr("Enter the jump host name."))
        try:
            return parse_config(data)
        except ValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise FormError(f"{where}: {first['msg']}") from None

    # ------------------------------------------------------------------ internals

    def _collect(self, connection_id: str) -> dict[str, object]:
        common: dict[str, object] = {
            "id": connection_id,
            "name": self.name_edit.text().strip(),
            "color": self.color_combo.currentData(),
            "group": self.group_combo.currentText().strip(),
            "read_only": self.read_only_check.isChecked(),
            "production": self.production_check.isChecked(),
        }
        if self.dialect_id is DialectId.SQLITE:
            return {
                **common,
                "kind": "file",
                "path": self.path_edit.text().strip(),
                "create_if_missing": self.create_check.isChecked(),
            }
        port = self.port_spin.value()
        return {
            **common,
            "kind": "server",
            "dialect": self.dialect_id.value,
            "host": self.host_edit.text().strip(),
            "port": port or None,
            "user": self.user_edit.text().strip(),
            "database": self.database_edit.text().strip(),
            "options": dict(parse_qsl(self.params_edit.text().strip(), keep_blank_values=True)),
            "save_password": self.save_password_check.isChecked(),
            "service": self.service_combo.currentText().strip()
            if self.dialect_id is DialectId.POSTGRESQL
            else "",
            "ssl": self.ssl_editor.read(),
            "ssh": self.ssh_editor.read(),
            "provider": self.cloud_editor.read(),
        }

    def _draft(self) -> ServerConnection | FileConnection | None:
        """The form as a config for URL generation; ``None`` while it is incomplete."""
        try:
            data = self._collect("draft")
            data["name"] = data["name"] or "draft"
            return parse_config(data)
        except (ValidationError, ValueError):
            return None

    def _select_dialect(self, dialect_id: DialectId) -> None:
        self.dialect_buttons[dialect_id].setChecked(True)

    def _apply_dialect_ui(self) -> None:
        dialect = _DIALECTS[self.dialect_id]
        is_file = dialect.file_based
        self.stack.setCurrentIndex(1 if is_file else 0)
        self.tabs.setTabText(1, tr("File") if is_file else tr("Host / Port"))
        self.password_widget.setVisible(not is_file and not self.cloud_editor.uses_token)
        is_postgres = self.dialect_id is DialectId.POSTGRESQL
        for widget in (self.service_label, self.service_combo, self.pgpass_hint):
            widget.setVisible(is_postgres)
        self.tabs.setTabVisible(2, not is_file)
        self.tabs.setTabVisible(3, not is_file)
        self.tabs.setTabVisible(4, not is_file)
        self.ssl_editor.set_dialect(self.dialect_id)
        self.cloud_editor.set_dialect(self.dialect_id)
        if not is_file and dialect.default_port is not None:
            self.port_spin.setSpecialValueText(tr("default ({port})", port=dialect.default_port))
        self.url_edit.setPlaceholderText(
            "sqlite:///path/to/database.sqlite"
            if is_file
            else f"{dialect.url_schemes[0]}://user@host:{dialect.default_port}/database"
        )

    def _refresh_url(self) -> None:
        draft = self._draft()
        if draft is not None:
            self.url_edit.setText(build_connection_url(draft))

    def _emit_changed(self, *_: object) -> None:
        if not self._syncing:
            self.changed.emit()

    def _on_dialect_toggled(self, checked: bool) -> None:
        if not checked or self._syncing:
            return
        self._apply_dialect_ui()
        self._refresh_url()
        self.changed.emit()

    def _on_field_edited(self, *_: object) -> None:
        if self._syncing:
            return
        self._refresh_url()
        self._refresh_pgpass_hint()
        self.url_error.hide()
        self.changed.emit()

    def _on_cloud_edited(self) -> None:
        if self._syncing:
            return
        self._apply_provider_defaults(replace_host=True)
        self.password_widget.setVisible(
            self.dialect_id is not DialectId.SQLITE and not self.cloud_editor.uses_token
        )
        self.changed.emit()

    def _on_provider_chosen(self, _kind: str) -> None:
        """A hosted service was picked: fill what its documentation prescribes."""
        if self._syncing:
            return
        self._applied.clear()
        self._apply_provider_defaults(replace_host=False)
        self._apply_dialect_ui()
        self._refresh_url()

    def _apply_provider_defaults(self, *, replace_host: bool = False) -> None:
        """Put the provider's defaults into fields the person has not filled in yet."""
        provider = self.cloud_editor.provider
        if provider is None:
            return
        defaults = provider.defaults(self.cloud_editor.params, self.dialect_id)
        self._syncing = True
        try:
            if defaults.dialect is not None and defaults.dialect is not self.dialect_id:
                self._select_dialect(defaults.dialect)
                self._apply_dialect_ui()
            for key, value, edit in (
                ("host", defaults.host, self.host_edit),
                ("user", defaults.user, self.user_edit),
                ("database", defaults.database, self.database_edit),
            ):
                typed = edit.text().strip()
                # an empty field is filled; one this method filled before follows the settings
                if value and (
                    not typed
                    or (key == "host" and typed == "localhost")
                    or typed == self._applied.get(key)
                ):
                    edit.setText(value)
                    self._applied[key] = value
            if defaults.port and not self.port_spin.value():
                self.port_spin.setValue(defaults.port)
            if defaults.ssl_mode is not None and self.ssl_editor.mode is None:
                index = self.ssl_editor.mode_combo.findData(defaults.ssl_mode.value)
                self.ssl_editor.mode_combo.setCurrentIndex(max(0, index))
        finally:
            self._syncing = False
        self._refresh_url()
        self._refresh_pgpass_hint()

    def _fill_services(self) -> None:
        """Offer the services found in ``pg_service.conf`` (the box stays free text)."""
        current = self.service_combo.currentText()
        self.service_combo.blockSignals(True)
        self.service_combo.clear()
        self.service_combo.addItems(sorted(load_services(os.environ)))
        self.service_combo.setEditText(current)
        self.service_combo.blockSignals(False)

    def _refresh_pgpass_hint(self) -> None:
        """Say whether ``~/.pgpass`` already knows this connection's password."""
        text = ""
        if self.dialect_id is DialectId.POSTGRESQL:
            pgpass = load_pgpass(os.environ)
            if pgpass is not None:
                host = self.host_edit.text().strip()
                found = pgpass.lookup(
                    host,
                    self.port_spin.value() or None,
                    self.database_edit.text().strip() or self.user_edit.text().strip(),
                    self.user_edit.text().strip(),
                )
                if pgpass.problem:
                    text = pgpass.problem
                elif found is not None:
                    text = tr(
                        "{file} has a password for this connection (line {line}); "
                        "it is used when none is saved.",
                        file=str(found.path),
                        line=found.line,
                    )
                elif host:
                    text = tr("{file} has no line for this connection.", file=str(pgpass.path))
        self.pgpass_hint.setText(text)

    def _on_url_typing(self, text: str) -> None:
        if self._apply_url(text, show_error=False):
            self.changed.emit()

    def _on_url_finished(self) -> None:
        text = self.url_edit.text()
        if not text.strip():
            self._set_url_error(None)
            return
        if self._apply_url(text, show_error=True):
            self._syncing = True
            try:
                self._refresh_url()  # canonical form, password removed
            finally:
                self._syncing = False

    def _apply_url(self, text: str, *, show_error: bool) -> bool:
        if not text.strip():
            return False
        try:
            parsed = parse_connection_url(text)
        except ConnectionUrlError as error:
            if show_error:
                self._set_url_error(str(error))
            return False
        self._set_url_error(None)
        config = parsed.config
        self._syncing = True
        try:
            if isinstance(config, FileConnection):
                self._select_dialect(DialectId.SQLITE)
                self.path_edit.setText(config.path)
                self.read_only_check.setChecked(config.read_only)
            else:
                self._select_dialect(DialectId(config.dialect))
                self.host_edit.setText(config.host)
                self.port_spin.setValue(config.port or 0)
                self.user_edit.setText(config.user)
                self.database_edit.setText(config.database)
                self.params_edit.setText(_encode_options(config.options))
                self.ssl_editor.load(config.ssl)
                if parsed.password is not None:
                    self.password_edit.setText(parsed.password)
            if not self.name_edit.text().strip():
                self.name_edit.setText(config.name)
            self._apply_dialect_ui()
        finally:
            self._syncing = False
        return True

    def _set_url_error(self, message: str | None) -> None:
        self.url_error.setText(message or "")
        self.url_error.setVisible(bool(message))
        self.url_edit.setProperty("invalid", bool(message))
        self._restyle(self.url_edit)

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Open database file"),
            self.path_edit.text(),
            tr("SQLite databases (*.db *.sqlite *.sqlite3 *.db3);;All files (*)"),
        )
        if path:
            self.path_edit.setText(path)
            self.create_check.setChecked(False)
            self._on_field_edited()

    def _new_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("New database file"),
            self.path_edit.text(),
            tr("SQLite databases (*.db *.sqlite *.sqlite3 *.db3);;All files (*)"),
        )
        if path:
            self.path_edit.setText(path)
            self.create_check.setChecked(True)
            self._on_field_edited()


def _encode_options(options: dict[str, str]) -> str:
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in options.items())
