"""The "Connections" dialog: saved connections on the left, the editable form on the right."""

from __future__ import annotations

import contextlib
import uuid

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import (
    ALL_FIELDS,
    PASSWORD,
    ConnectionStore,
    FileConnection,
    SecretStore,
    SecretStoreError,
    ServerConnection,
)
from ..core.db import ConnectionCheck
from ..core.dialects import DialectId
from ..core.session import ConnectionManager
from ..core.ssh import SshHostKeyUnknown
from .connection_form import DIALECT_LABELS, ConnectionForm, FormError
from .failure_hints import hint_for
from .i18n import tr
from .runtime import BackgroundRunner
from .secrets_ui import ensure_unlocked, store_label
from .theme import color_hex, current_tokens

ROLE_ID = Qt.ItemDataRole.UserRole
ROLE_SUBTITLE = Qt.ItemDataRole.UserRole + 1
ROLE_COLOR = Qt.ItemDataRole.UserRole + 2
ROLE_HEADER = Qt.ItemDataRole.UserRole + 3


def _step_label(name: str) -> str:
    labels = {
        "DNS": tr("DNS lookup"),
        "TCP": tr("Port reachable"),
        "Login": tr("Sign in"),
        "Query": tr("Test query"),
        "File": tr("Database file"),
        "Open": tr("Open file"),
        "Settings": tr("Settings"),
        "Service": tr("Service file"),
        "Password": tr("Password source"),
        "SSH jump": tr("SSH jump host"),
        "SSH": tr("SSH login"),
        "Tunnel": tr("SSH tunnel"),
        "TLS files": tr("TLS files"),
        "TLS": tr("Encryption"),
    }
    return labels.get(name, name)


def _scaled(font: QFont, factor: float, *, bold: bool = False) -> QFont:
    """``font`` scaled by ``factor`` (the stylesheet sets pixel sizes, so points may be unset)."""
    scaled = QFont(font)
    if font.pixelSize() > 0:
        scaled.setPixelSize(max(1, round(font.pixelSize() * factor)))
    elif font.pointSizeF() > 0:
        scaled.setPointSizeF(font.pointSizeF() * factor)
    scaled.setBold(bold)
    return scaled


class _ConnectionDelegate(QStyledItemDelegate):
    """Two-line rows: colour dot, name, then dialect and address in muted text."""

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(option.rect.width(), 28 if index.data(ROLE_HEADER) else 48)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        tokens = current_tokens()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect: QRect = option.rect
        if index.data(ROLE_HEADER):
            painter.setFont(_scaled(option.font, 0.85, bold=True))
            painter.setPen(QColor(tokens.text_muted))
            painter.drawText(
                rect.adjusted(10, 8, -6, 0), Qt.AlignmentFlag.AlignLeft, index.data().upper()
            )
            painter.restore()
            return
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect.adjusted(2, 1, -2, -1), QColor(tokens.selection))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect.adjusted(2, 1, -2, -1), QColor(tokens.hover))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(index.data(ROLE_COLOR) or tokens.text_muted))
        painter.drawEllipse(rect.left() + 12, rect.center().y() - 5, 10, 10)
        painter.setFont(_scaled(option.font, 1.0, bold=True))
        painter.setPen(QColor(tokens.text))
        painter.drawText(
            rect.adjusted(32, 7, -8, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            index.data(),
        )
        painter.setFont(_scaled(option.font, 0.9))
        painter.setPen(QColor(tokens.text_muted))
        metrics = painter.fontMetrics()
        subtitle = metrics.elidedText(
            index.data(ROLE_SUBTITLE) or "", Qt.TextElideMode.ElideMiddle, rect.width() - 42
        )
        painter.drawText(
            rect.adjusted(32, 0, -8, -7),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            subtitle,
        )
        painter.restore()


class TestReport(QFrame):
    """Step-by-step result of "Test connection"."""

    __test__ = False  # not a pytest test class despite the name

    #: The user wants to trust the SSH server whose key the test reported (a ``SshHostKeyUnknown``).
    trustRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 10, 14, 10)
        self._layout.setSpacing(4)
        self.hide()

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()  # deleteLater() alone leaves it painted until the loop runs
                widget.setParent(None)
                widget.deleteLater()

    def _line(self, glyph: str, color: str, text: str, muted: str = "") -> None:
        row = QHBoxLayout()
        mark = QLabel(glyph)
        mark.setStyleSheet(f"color: {color}; font-weight: 700;")
        mark.setFixedWidth(16)
        label = QLabel(text)
        label.setWordWrap(True)
        row.addWidget(mark)
        row.addWidget(label, 1)
        if muted:
            extra = QLabel(muted)
            extra.setProperty("muted", True)
            row.addWidget(extra)
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        container.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self._layout.addWidget(container)

    def show_running(self) -> None:
        self._clear()
        self._line("…", current_tokens().text_muted, tr("Testing the connection…"))
        self.show()

    def show_result(self, check: ConnectionCheck) -> None:
        tokens = current_tokens()
        self._clear()
        for step in check.steps:
            name = _step_label(step.name)
            self._line(
                "✓" if step.ok else "✗",
                tokens.success if step.ok else tokens.danger,
                f"{name} — {step.detail}",
                f"{step.duration * 1000:.0f} ms",
            )
        if check.ok:
            self._line("●", tokens.success, tr("Connection works."))
        else:
            hint = hint_for(check.failure)
            if hint:
                self._line("→", tokens.warning, hint)
            if isinstance(check.failure, SshHostKeyUnknown):
                self._trust_button(check.failure)
        self.show()

    def _trust_button(self, error: SshHostKeyUnknown) -> None:
        self.trust_button = QPushButton(tr("Trust this server…"))
        self.trust_button.clicked.connect(lambda: self.trustRequested.emit(error))
        row = QHBoxLayout()
        row.addWidget(self.trust_button)
        row.addStretch(1)
        container = QWidget()
        container.setLayout(row)
        row.setContentsMargins(0, 4, 0, 0)
        self._layout.addWidget(container)

    def show_error(self, error: Exception) -> None:
        tokens = current_tokens()
        self._clear()
        self._line("✗", tokens.danger, str(error))
        hint = hint_for(error)
        if hint:
            self._line("→", tokens.warning, hint)
        self.show()


class ConnectionDialog(QDialog):
    #: ``(connection id, password typed for this run or None, other typed secrets as a dict)``;
    #: the dialog closes after it.
    connectRequested = Signal(str, object, object)
    #: Saved, duplicated or deleted something: refresh anything listing connections.
    connectionsChanged = Signal()

    def __init__(
        self,
        store: ConnectionStore,
        secrets: SecretStore,
        manager: ConnectionManager,
        runner: BackgroundRunner,
        parent: QWidget | None = None,
        select_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._secrets = secrets
        self._manager = manager
        self._runner = runner
        self._current_id: str | None = None
        self._new_id = uuid.uuid4().hex
        self._loading = False
        self._dirty = False
        self._test_job: int | None = None
        self.setWindowTitle(tr("Connections"))
        self.setMinimumSize(940, 680)
        self._build()
        self._reload_list(select_id)

    # ------------------------------------------------------------------ construction

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(8)
        title = QLabel(tr("Saved connections"))
        title.setProperty("heading", True)
        self.list = QListWidget()
        self.list.setItemDelegate(_ConnectionDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setMinimumWidth(270)
        buttons = QHBoxLayout()
        self.new_button = QPushButton(tr("+ New"))
        self.duplicate_button = QPushButton(tr("Duplicate"))
        self.delete_button = QPushButton(tr("Delete"))
        self.delete_button.setProperty("danger", True)
        for button in (self.new_button, self.duplicate_button, self.delete_button):
            buttons.addWidget(button)
        left.addWidget(title)
        left.addWidget(self.list, 1)
        left.addLayout(buttons)
        root.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.form = ConnectionForm(store_label(self._secrets))
        right.addWidget(self.form)
        self.report = TestReport()
        right.addWidget(self.report)
        right.addStretch(1)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()
        right.addWidget(self.message)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.test_button = QPushButton(tr("Test"))
        self.save_button = QPushButton(tr("Save"))
        self.connect_button = QPushButton(tr("Connect"))
        self.connect_button.setProperty("primary", True)
        self.close_button = QPushButton(tr("Close"))
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        actions.addWidget(self.close_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.connect_button)
        right.addLayout(actions)
        root.addLayout(right, 1)

        self.new_button.clicked.connect(self._new)
        self.duplicate_button.clicked.connect(self._duplicate)
        self.delete_button.clicked.connect(self._delete)
        self.test_button.clicked.connect(self._test)
        self.save_button.clicked.connect(lambda: self._save())
        self.connect_button.clicked.connect(self._connect)
        self.close_button.clicked.connect(self.close)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.form.changed.connect(self._on_form_changed)
        self.report.trustRequested.connect(self._trust_host)

    # ------------------------------------------------------------------ list handling

    def _reload_list(self, select_id: str | None = None) -> None:
        self._loading = True
        self.list.clear()
        by_group: dict[str, list[ServerConnection | FileConnection]] = {}
        for config in sorted(self._store.all(), key=lambda c: c.name.lower()):
            by_group.setdefault(config.group, []).append(config)
        groups = sorted(by_group, key=lambda g: (g == "", g.lower()))
        show_headers = len(groups) > 1 or (groups and groups[0] != "")
        for group in groups:
            if show_headers:
                header = QListWidgetItem(group or tr("No group"))
                header.setData(ROLE_HEADER, True)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(header)
            for config in by_group[group]:
                item = QListWidgetItem(config.name)
                item.setData(ROLE_ID, config.id)
                item.setData(ROLE_COLOR, color_hex(config.display_color, ""))
                item.setData(
                    ROLE_SUBTITLE,
                    f"{DIALECT_LABELS[DialectId(config.dialect)]} · {config.subtitle}",
                )
                self.list.addItem(item)
        self.form.set_groups([g for g in groups if g])
        self._loading = False
        wanted = select_id or self._current_id
        row = self._row_of(wanted) if wanted else -1
        if row >= 0:
            self.list.setCurrentRow(row)
        elif self._store.all() and not wanted:
            first = next(i for i in range(self.list.count()) if self.list.item(i).data(ROLE_ID))
            self.list.setCurrentRow(first)
        else:
            self._load(None)

    def _row_of(self, connection_id: str | None) -> int:
        for row in range(self.list.count()):
            if self.list.item(row).data(ROLE_ID) == connection_id:
                return row
        return -1

    def _on_current_changed(
        self, current: QListWidgetItem | None, _: QListWidgetItem | None
    ) -> None:
        if self._loading or current is None or not current.data(ROLE_ID):
            return
        wanted = str(current.data(ROLE_ID))
        if wanted == self._current_id:
            return
        if not self._confirm_discard():
            self._loading = True
            self.list.setCurrentRow(self._row_of(self._current_id))
            self._loading = False
            return
        self._load(self._store.get(wanted))

    def _load(self, config: ServerConnection | FileConnection | None) -> None:
        self._current_id = config.id if config else None
        if config is None:
            self._new_id = uuid.uuid4().hex
        has_secret = bool(
            isinstance(config, ServerConnection)
            and config.save_password
            and self._secret_exists(config.id)
        )
        saved: set[str] = set()
        if isinstance(config, ServerConnection) and config.save_password:
            saved = {f for f in ALL_FIELDS if f != PASSWORD and self._secret_exists(config.id, f)}
        self.form.load(config, has_saved_password=has_secret, saved_secrets=saved)
        self.report.hide()
        self._show_message(None)
        self._dirty = False
        self._update_buttons()

    def _secret_exists(self, connection_id: str, field: str = PASSWORD) -> bool:
        if self._secrets.locked:
            return False
        try:
            return self._secrets.get(connection_id, field) is not None
        except SecretStoreError:
            return False

    def _update_buttons(self) -> None:
        saved = self._current_id is not None
        self.duplicate_button.setEnabled(saved)
        self.delete_button.setEnabled(saved)

    # ------------------------------------------------------------------ actions

    def _on_form_changed(self) -> None:
        self._dirty = True
        self._show_message(None)

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            tr("Unsaved changes"),
            tr("The connection has unsaved changes. Save them?"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save(reload=False) is not None
        return True

    def _new(self) -> None:
        if not self._confirm_discard():
            return
        self._loading = True
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self._loading = False
        self._load(None)
        self.form.name_edit.setFocus()

    def _duplicate(self) -> None:
        if self._current_id is None or not self._confirm_discard():
            return
        copy = self._store.duplicate(self._current_id)
        self.connectionsChanged.emit()
        self._reload_list(copy.id)

    def _delete(self) -> None:
        if self._current_id is None:
            return
        config = self._store.get(self._current_id)
        answer = QMessageBox.question(
            self,
            tr("Delete connection"),
            tr(
                "Delete the connection “{name}”? Its saved password is removed too.",
                name=config.name,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._manager.forget(config.id)
        if (
            isinstance(config, ServerConnection)
            and config.save_password
            and (not self._secrets.locked or ensure_unlocked(self._secrets, self))
        ):
            with contextlib.suppress(SecretStoreError):
                self._secrets.delete_all(config.id)
        self._store.remove(config.id)
        self._current_id = None
        self._dirty = False
        self.connectionsChanged.emit()
        self._reload_list()

    def _read(self) -> ServerConnection | FileConnection | None:
        try:
            return self.form.read_config(self._current_id or self._new_id)
        except FormError as error:
            self._show_message(str(error), error=True)
            return None

    def _save(self, *, reload: bool = True) -> ServerConnection | FileConnection | None:
        config = self._read()
        if config is None:
            return None
        previous = self._store.find(config.id)
        if isinstance(config, ServerConnection):
            applicable = set(self.form.applicable_secret_fields)
            typed = {f: v for f, v in self.form.secrets.items() if f in applicable}
            if self.form.password:
                typed[PASSWORD] = self.form.password
            if config.save_password and typed:
                if not ensure_unlocked(self._secrets, self):
                    self._show_message(
                        tr("The password was not saved: the password store is locked."), error=True
                    )
                    return None
                for field, value in typed.items():
                    self._secrets.set(config.id, value, field)
            elif (
                not config.save_password
                and isinstance(previous, ServerConnection)
                and previous.save_password
            ):
                if ensure_unlocked(self._secrets, self):
                    self._secrets.delete_all(config.id)
            if config.save_password and not self._secrets.locked:
                for field in ALL_FIELDS:  # secrets of a login method that is no longer used
                    if field != PASSWORD and field not in applicable:
                        with contextlib.suppress(SecretStoreError):
                            self._secrets.delete(config.id, field)
        self._store.save(config)
        if previous is not None and previous != config:
            self._manager.forget(config.id)  # reconnect with the new settings next time
        self._current_id = config.id
        self._dirty = False
        self.connectionsChanged.emit()
        if reload:
            self._reload_list(config.id)
            self._load(config)
            self._show_message(tr("Saved."))
        return config

    def _connect(self) -> None:
        config = self._save(reload=False)
        if config is None:
            return
        typed = self.form.password
        keep = isinstance(config, ServerConnection) and not config.save_password
        applicable = set(self.form.applicable_secret_fields)
        secrets = {f: v for f, v in self.form.secrets.items() if f in applicable} if keep else {}
        self.connectRequested.emit(config.id, (typed if keep else None) or None, secrets)
        self.accept()

    def _test(self) -> None:
        config = self._read()
        if config is None:
            return
        proceed, password, secrets = self._credentials_for_test(config)
        if not proceed:
            return
        self._set_busy(True)
        self.report.show_running()

        def work() -> ConnectionCheck:
            return self._manager.test_connection(config, password, secrets)

        self._test_job = self._runner.submit(work, self._on_test_done)

    def _credentials_for_test(
        self, config: ServerConnection | FileConnection
    ) -> tuple[bool, str | None, dict[str, str]]:
        """``(proceed, password, secrets)``; ``proceed`` is ``False`` if unlocking was declined."""
        if isinstance(config, FileConnection):
            return True, None, {}
        applicable = set(self.form.applicable_secret_fields)
        secrets: dict[str, str] = {}
        password: str | None = None
        if config.save_password and self._store.find(config.id) is not None:
            if not ensure_unlocked(self._secrets, self):
                return False, None, {}
            try:
                password = self._secrets.get(config.id)
                for field in applicable:
                    saved = self._secrets.get(config.id, field)
                    if saved:
                        secrets[field] = saved
            except SecretStoreError:
                pass
        secrets.update({f: v for f, v in self.form.secrets.items() if f in applicable})
        return True, self.form.password or password, secrets

    def _trust_host(self, error: object) -> None:
        """The report found an SSH server nobody trusted yet: ask, remember, test again."""
        if not isinstance(error, SshHostKeyUnknown):
            return
        answer = QMessageBox.question(
            self,
            tr("Trust this SSH server?"),
            tr(
                "The identity of {host}:{port} is not known yet.\n\n"
                "{key_type} key fingerprint:\n{fingerprint}\n\n"
                "Compare it with the one your administrator gave you. Trust this server?",
                host=error.host,
                port=error.port,
                key_type=error.key_type,
                fingerprint=error.fingerprint,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._manager.trust_host_key(error)
            self._test()

    def _on_test_done(self, check: object, error: Exception | None) -> None:
        self._test_job = None
        self._set_busy(False)
        if error is not None:
            self.report.show_error(error)
        elif isinstance(check, ConnectionCheck):
            self.report.show_result(check)

    def _set_busy(self, busy: bool) -> None:
        for button in (self.test_button, self.save_button, self.connect_button):
            button.setEnabled(not busy)

    def _show_message(self, text: str | None, *, error: bool = False) -> None:
        self.message.setText(text or "")
        self.message.setProperty("error", error)
        self.message.setProperty("muted", not error)
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setVisible(bool(text))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._test_job is not None:
            self._runner.cancel(self._test_job)
        super().closeEvent(event)

    def reject(self) -> None:
        if self._confirm_discard():
            super().reject()
