"""The main window: session panel on the left, the ERD pane (with the DB switcher) on the right."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import ServerConnection
from ..core.services import Services
from ..core.session import ActiveChanged, SessionState, SessionStateChanged
from .connection_dialog import ConnectionDialog
from .db_switcher import DbSwitcher
from .i18n import tr
from .runtime import BackgroundRunner, EventBridge
from .secrets_ui import ensure_unlocked
from .session_panel import SessionPanel
from .theme import apply_theme, current_tokens

APP_TITLE = "SQL ERD Studio"
_GEOMETRY_KEY = "window/geometry"
_SPLITTER_KEY = "window/splitter"


class MainWindow(QMainWindow):
    def __init__(self, services: Services, bridge: EventBridge, runner: BackgroundRunner) -> None:
        super().__init__()
        self._services = services
        self._runner = runner
        self._closed = False
        self.setWindowTitle(APP_TITLE)
        self.resize(1240, 780)
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self.statusBar().addWidget(self._status_label, 1)
        bridge.posted.connect(self._on_event)
        self._restore_state()
        self._refresh()

    # ------------------------------------------------------------------ construction

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu(tr("&File"))
        connections = QAction(tr("&Connections…"), self)
        connections.setShortcut(QKeySequence("Ctrl+Shift+C"))
        connections.triggered.connect(lambda: self.open_connections())
        quit_action = QAction(tr("&Quit"), self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(connections)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        view_menu = self.menuBar().addMenu(tr("&View"))
        themes = QActionGroup(self)
        for name, label in (("dark", tr("Dark theme")), ("light", tr("Light theme"))):
            action = QAction(label, self, checkable=True)
            action.setChecked(self._services.settings.theme == name)
            action.triggered.connect(lambda _checked=False, n=name: self.set_theme(n))
            themes.addAction(action)
            view_menu.addAction(action)
        view_menu.addSeparator()
        language_menu = view_menu.addMenu(tr("Language"))
        languages = QActionGroup(self)
        for code, label in (("auto", tr("System default")), ("ru", "Русский"), ("en", "English")):
            action = QAction(label, self, checkable=True)
            action.setChecked(self._services.settings.language == code)
            action.triggered.connect(lambda _checked=False, c=code: self.set_language(c))
            languages.addAction(action)
            language_menu.addAction(action)

    def _build_toolbar(self) -> None:
        bar = QToolBar()
        bar.setMovable(False)
        title = QLabel(APP_TITLE)
        title.setProperty("heading", True)
        bar.addWidget(title)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        bar.addWidget(spacer)
        button = QPushButton(tr("Connections…"))
        button.clicked.connect(lambda: self.open_connections())
        bar.addWidget(button)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

    def _build_body(self) -> None:
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self._banner = QLabel()
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setFixedHeight(22)
        self._banner.hide()
        column.addWidget(self._banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.panel = SessionPanel()
        self.panel.manageRequested.connect(lambda: self.open_connections())
        self.panel.retryRequested.connect(self.activate)
        self.panel.editRequested.connect(lambda cid: self.open_connections(cid))
        self.panel.disconnectRequested.connect(self._services.manager.disconnect)
        self.panel.cancelRequested.connect(self._services.manager.disconnect)
        self.splitter.addWidget(self.panel)
        self.splitter.addWidget(self._build_erd_pane())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([560, 680])
        column.addWidget(self.splitter, 1)
        self.setCentralWidget(container)
        self._status_label = QLabel()

    def _build_erd_pane(self) -> QWidget:
        pane = QWidget()
        pane.setProperty("panel", True)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        self.switcher = DbSwitcher(self._services.store, self._services.manager)
        self.switcher.connectionChosen.connect(self.activate)
        self.switcher.manageRequested.connect(lambda: self.open_connections())
        header.addWidget(self.switcher)
        header.addStretch(1)
        layout.addLayout(header)
        self._erd_hint = QLabel(tr("The database diagram will appear here."))
        self._erd_hint.setProperty("muted", True)
        self._erd_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._erd_hint, 1)
        return pane

    # ------------------------------------------------------------------ actions

    def open_connections(self, select_id: str | None = None) -> None:
        dialog = ConnectionDialog(
            self._services.store,
            self._services.secrets,
            self._services.manager,
            self._runner,
            self,
            select_id or self._services.manager.active_id,
        )
        dialog.connectRequested.connect(self._connect_from_dialog)
        dialog.connectionsChanged.connect(self._refresh)
        dialog.exec()
        self._refresh()

    def activate(self, connection_id: str) -> None:
        """Make a saved connection active, asking for what it needs first (non-blocking after)."""
        self._activate(connection_id, None)

    def _connect_from_dialog(self, connection_id: str, password: object) -> None:
        self._activate(connection_id, password if isinstance(password, str) else None)

    def _activate(self, connection_id: str, password: str | None) -> None:
        config = self._services.store.find(connection_id)
        if config is None:
            return
        manager = self._services.manager
        if isinstance(config, ServerConnection):
            if config.save_password and not ensure_unlocked(self._services.secrets, self):
                self._status_label.setText(tr("The password store stayed locked."))
                return
            if password is None and manager.needs_password(connection_id):
                typed, accepted = QInputDialog.getText(
                    self,
                    tr("Password required"),
                    tr("Password for {name}:", name=config.name),
                    QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    return
                password = typed
        manager.activate(connection_id, password)

    def set_theme(self, name: str) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, name)
        self._services.settings.theme = name  # type: ignore[assignment]
        self._services.settings_store.save(self._services.settings)
        self._refresh()

    def set_language(self, code: str) -> None:
        self._services.settings.language = code  # type: ignore[assignment]
        self._services.settings_store.save(self._services.settings)
        QMessageBox.information(
            self, tr("Language"), tr("The language changes the next time the application starts.")
        )

    def show_startup_notices(self) -> None:
        """Tell the user about config files that had to be set aside."""
        for recovered in (
            self._services.store.recovered_from,
            self._services.settings_store.recovered_from,
        ):
            if recovered is not None:
                QMessageBox.warning(
                    self,
                    tr("A configuration file was damaged"),
                    tr(
                        "A configuration file could not be read. It was kept as:\n{path}\n"
                        "Valid entries were restored.",
                        path=str(recovered),
                    ),
                )

    # ------------------------------------------------------------------ state

    def _on_event(self, event: object) -> None:
        if isinstance(event, ActiveChanged | SessionStateChanged):
            self._refresh()

    def _refresh(self) -> None:
        manager = self._services.manager
        session = manager.active
        self.switcher.refresh()
        self.panel.show_session(session)
        tokens = current_tokens()
        if session is None:
            self.setWindowTitle(APP_TITLE)
            self._status_label.setText(tr("No connection"))
            self._banner.hide()
            return
        config = session.config
        self.setWindowTitle(f"{config.name} — {APP_TITLE}")
        self._status_label.setText(self._status_text(session.state, config.name))
        if config.production:
            self._banner.setText(tr("PRODUCTION") + f" — {config.name}")
            self._banner.setStyleSheet(
                f"background-color: {tokens.danger}; color: white; font-weight: 700;"
            )
            self._banner.show()
        else:
            self._banner.hide()

    @staticmethod
    def _status_text(state: SessionState, name: str) -> str:
        return {
            SessionState.READY: tr("Connected to {name}", name=name),
            SessionState.CONNECTING: tr("Connecting to {name}…", name=name),
            SessionState.ERROR: tr("Could not connect to {name}", name=name),
            SessionState.DISCONNECTED: tr("{name} is not connected", name=name),
        }[state]

    def _restore_state(self) -> None:
        db = self._services.db
        geometry = db.get_state(_GEOMETRY_KEY)
        if isinstance(geometry, str):
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        sizes = db.get_state(_SPLITTER_KEY)
        if isinstance(sizes, list) and len(sizes) == 2 and all(isinstance(s, int) for s in sizes):
            self.splitter.setSizes(sizes)

    def _save_state(self) -> None:
        db = self._services.db
        db.set_state(_GEOMETRY_KEY, bytes(self.saveGeometry().toBase64().data()).decode("ascii"))
        db.set_state(_SPLITTER_KEY, self.splitter.sizes())

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closed:  # a second close request must not touch the closed services
            self._closed = True
            self._save_state()
            self._runner.shutdown()
            self._services.close()
        super().closeEvent(event)
