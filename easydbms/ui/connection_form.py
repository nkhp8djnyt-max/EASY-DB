"""The connection form: fields, URL synchronisation, validation.

The form knows nothing about stores, secrets or threads; it turns user input into a validated
connection config and back, which keeps it easy to test.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import (
    ConnectionColor,
    ConnectionUrlError,
    FileConnection,
    ServerConnection,
    build_connection_url,
    parse_config,
    parse_connection_url,
)
from ..core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect, DialectId
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
        self.params_edit.setPlaceholderText("sslmode=require&connect_timeout=5")
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
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(8, 1)
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
            self._apply_dialect_ui()
            self.password_edit.setPlaceholderText(
                tr("A saved password is used. Type to replace it.") if has_saved_password else ""
            )
            self._refresh_url()
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
        elif not str(data["host"]).strip():
            raise FormError(tr("Enter the host name."))
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
        self.password_widget.setVisible(not is_file)
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
        self.url_error.hide()
        self.changed.emit()

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
