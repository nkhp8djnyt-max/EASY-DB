"""The main window: session panel on the left, the ERD pane (with the DB switcher) on the right."""

from __future__ import annotations

from collections.abc import Callable

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
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import ServerConnection
from ..core.services import Services
from ..core.session import ActiveChanged, Session, SessionState, SessionStateChanged
from .connection_dialog import ConnectionDialog
from .db_switcher import DbSwitcher
from .i18n import tr
from .runtime import BackgroundRunner, EventBridge
from .secrets_ui import ensure_unlocked
from .session_panel import SessionPanel
from .theme import apply_theme, current_tokens
from .workspace import QueryWorkspace

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

        query_menu = self.menuBar().addMenu(tr("&Query"))
        for text, shortcut, handler in (
            (tr("&Run"), "Ctrl+Return", lambda w: w.run_current()),
            (tr("Run &script"), "F5", lambda w: w.run_script()),
            (tr("S&top"), "Esc", lambda w: w.cancel()),
            (tr("&Format"), "Ctrl+Shift+F", lambda w: w.format_current()),
            (tr("&New tab"), "Ctrl+T", lambda w: w.new_tab()),
            (tr("&Close tab"), "Ctrl+W", lambda w: w.close_current_tab()),
        ):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, h=handler: self._with_workspace(h))
            query_menu.addAction(action)
        query_menu.addSeparator()
        limit_menu = query_menu.addMenu(tr("Row limit"))
        limits = QActionGroup(self)
        for count in (100, 1000, 10_000, 100_000):
            action = QAction(f"{count:,}", self, checkable=True)
            action.setChecked(self._services.settings.row_limit == count)
            action.triggered.connect(lambda _checked=False, c=count: self.set_row_limit(c))
            limits.addAction(action)
            limit_menu.addAction(action)

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
        self.left = QStackedWidget()
        self.workspaces = QStackedWidget()
        self._workspaces: dict[str, QueryWorkspace] = {}
        self.panel = SessionPanel()
        self.panel.manageRequested.connect(lambda: self.open_connections())
        self.panel.retryRequested.connect(self.activate)
        self.panel.editRequested.connect(lambda cid: self.open_connections(cid))
        self.panel.disconnectRequested.connect(self._services.manager.disconnect)
        self.panel.cancelRequested.connect(self._services.manager.disconnect)
        self.left.addWidget(self.panel)
        self.left.addWidget(self.workspaces)
        self.splitter.addWidget(self.left)
        self.splitter.addWidget(self._build_erd_pane())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([700, 540])
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
        for workspace in self._workspaces.values():
            workspace.refresh_theme()
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
        if isinstance(event, SessionStateChanged):
            workspace = self._workspaces.get(event.connection_id)
            if workspace is not None:
                session = self._services.manager.session(event.connection_id)
                workspace.set_session(session if event.state is SessionState.READY else None)
        if isinstance(event, ActiveChanged | SessionStateChanged):
            self._refresh()

    # ------------------------------------------------------------------ workspaces

    def current_workspace(self) -> QueryWorkspace | None:
        widget = self.workspaces.currentWidget()
        return widget if isinstance(widget, QueryWorkspace) else None

    def _with_workspace(self, action: Callable[[QueryWorkspace], None]) -> None:
        workspace = self.current_workspace()
        if workspace is not None and self.left.currentWidget() is self.workspaces:
            action(workspace)

    def _workspace_for(self, session: Session) -> QueryWorkspace:
        workspace = self._workspaces.get(session.id)
        if workspace is None:
            workspace = QueryWorkspace(
                session.config,
                self._services.tab_store,
                lambda: self._services.settings.row_limit,
            )
            self._workspaces[session.id] = workspace
            self.workspaces.addWidget(workspace)
        workspace.set_session(session)
        return workspace

    def _drop_stale_workspaces(self) -> None:
        known = {c.id for c in self._services.store.all()}
        for connection_id in [i for i in self._workspaces if i not in known]:
            workspace = self._workspaces.pop(connection_id)
            workspace.shutdown()
            self.workspaces.removeWidget(workspace)
            workspace.deleteLater()
            self._services.tab_store.forget(connection_id)

    def set_row_limit(self, count: int) -> None:
        self._services.settings.row_limit = count
        self._services.settings_store.save(self._services.settings)

    def _refresh(self) -> None:
        manager = self._services.manager
        session = manager.active
        if session is not None and self._services.store.find(session.id) is None:
            session = None  # its connection was deleted: nothing to show
        self.switcher.refresh()
        self.panel.show_session(session)
        self._drop_stale_workspaces()
        if session is not None and session.state is SessionState.READY:
            self.workspaces.setCurrentWidget(self._workspace_for(session))
            self.left.setCurrentWidget(self.workspaces)
        else:
            self.left.setCurrentWidget(self.panel)
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
            for workspace in self._workspaces.values():
                workspace.shutdown()
            self._save_state()
            self._runner.shutdown()
            self._services.close()
        super().closeEvent(event)
