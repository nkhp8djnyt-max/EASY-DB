"""The main window: session panel on the left, the ERD pane (with the DB switcher) on the right."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QGuiApplication, QKeySequence
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import (
    JUMP_PASSPHRASE,
    JUMP_PASSWORD,
    PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    ServerConnection,
)
from ..core.schema import Table
from ..core.services import Services
from ..core.session import (
    ActiveChanged,
    SchemaChanged,
    Session,
    SessionState,
    SessionStateChanged,
)
from ..core.ssh import SshHostKeyUnknown
from .connection_dialog import ConnectionDialog
from .db_switcher import DbSwitcher
from .erd import ErdPane
from .i18n import tr
from .quick_open import QuickOpenDialog
from .runtime import BackgroundRunner, EventBridge
from .secrets_ui import ensure_unlocked
from .session_panel import SessionPanel
from .theme import apply_theme, current_tokens
from .workspace import QueryWorkspace

APP_TITLE = "EasyDBMS"
_GEOMETRY_KEY = "window/geometry"
_SPLITTER_KEY = "window/splitter"
_ERD_COLLAPSED_KEY = "window/erd_collapsed"
_COLLAPSED_WIDTH = 300


def _secret_prompt(field: str, name: str) -> str:
    """What to ask for when the secret ``field`` of connection ``name`` is missing."""
    prompts = {
        PASSWORD: tr("Password for {name}:", name=name),
        SSH_PASSWORD: tr("SSH password for {name}:", name=name),
        SSH_PASSPHRASE: tr("Passphrase of the SSH key for {name}:", name=name),
        JUMP_PASSWORD: tr("Jump host password for {name}:", name=name),
        JUMP_PASSPHRASE: tr("Passphrase of the jump host key for {name}:", name=name),
        SSL_KEY_PASSWORD: tr("Passphrase of the TLS client key for {name}:", name=name),
    }
    return prompts.get(field, tr("Secret for {name}:", name=name))


class MainWindow(QMainWindow):
    def __init__(self, services: Services, bridge: EventBridge, runner: BackgroundRunner) -> None:
        super().__init__()
        self._services = services
        self._runner = runner
        self._closed = False
        self._erd_collapsed = False
        self.setWindowTitle(APP_TITLE)
        self.resize(1240, 780)
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self.statusBar().addWidget(self._status_label, 1)
        bridge.posted.connect(self._on_event)
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
        self._restore_state()
        known = {c.id for c in services.store.all()}
        services.usage_store.prune(known)
        services.edit_keys.prune(known)
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
        for text, shortcut, handler in (
            (tr("&Save query…"), "Ctrl+S", lambda w: w.save_current_query()),
            (tr("&History…"), "Ctrl+H", lambda w: w.show_library("history")),
            (tr("Sa&ved queries…"), "Ctrl+Shift+H", lambda w: w.show_library("saved")),
        ):
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, h=handler: self._with_workspace(h))
            query_menu.addAction(action)
        query_menu.addSeparator()
        apply_action = QAction(tr("Apply &changes"), self)
        apply_action.setShortcut(QKeySequence("Alt+S"))
        apply_action.triggered.connect(lambda: self._with_workspace(lambda w: w.apply_changes()))
        query_menu.addAction(apply_action)
        suggest = QAction(tr("&Autocomplete"), self)
        suggest.setShortcut(QKeySequence("Ctrl+Space"))
        suggest.triggered.connect(lambda: self._with_workspace(lambda w: w.complete()))
        query_menu.addAction(suggest)
        case_menu = query_menu.addMenu(tr("Keyword case"))
        cases = QActionGroup(self)
        for value, label in (
            ("upper", tr("UPPER CASE (SELECT)")),
            ("lower", tr("lower case (select)")),
            ("preserve", tr("As typed")),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(self._services.settings.keyword_case == value)
            action.triggered.connect(lambda _checked=False, v=value: self.set_keyword_case(v))
            cases.addAction(action)
            case_menu.addAction(action)
        limit_menu = query_menu.addMenu(tr("Row limit"))
        limits = QActionGroup(self)
        for count in (100, 1000, 10_000, 100_000):
            action = QAction(f"{count:,}", self, checkable=True)
            action.setChecked(self._services.settings.row_limit == count)
            action.triggered.connect(lambda _checked=False, c=count: self.set_row_limit(c))
            limits.addAction(action)
            limit_menu.addAction(action)

        database_menu = self.menuBar().addMenu(tr("&Database"))
        for label, keys, callback in (
            (tr("&Go to table…"), "Ctrl+P", self.go_to_table),
            (tr("&Refresh structure"), "Ctrl+Shift+R", self.refresh_schema),
            (tr("&Fit diagram"), "", self.fit_diagram),
            (tr("&Reset diagram layout"), "", self.reset_diagram),
            (tr("&Export diagram…"), "Ctrl+E", self.export_diagram),
        ):
            db_action = QAction(label, self)
            if keys:
                db_action.setShortcut(QKeySequence(keys))
            db_action.triggered.connect(lambda _checked=False, cb=callback: cb())
            database_menu.addAction(db_action)
        database_menu.addSeparator()
        self.toggle_diagram_action = QAction(tr("Show the &diagram"), self, checkable=True)
        self.toggle_diagram_action.setChecked(True)
        self.toggle_diagram_action.triggered.connect(
            lambda checked: self.set_erd_collapsed(not checked)
        )
        database_menu.addAction(self.toggle_diagram_action)

        view_menu = self.menuBar().addMenu(tr("&View"))
        themes = QActionGroup(self)
        for name, label in (
            ("system", tr("System theme")),
            ("dark", tr("Dark theme")),
            ("light", tr("Light theme")),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(self._services.settings.theme == name)
            action.triggered.connect(lambda _checked=False, n=name: self.set_theme(n))
            themes.addAction(action)
            view_menu.addAction(action)
        view_menu.addSeparator()
        language_menu = view_menu.addMenu(tr("Language"))
        languages = QActionGroup(self)
        for code, label in (
            ("auto", tr("System default")),
            ("uk", "Українська"),
            ("en", "English"),
        ):
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
        self.panel.trustRequested.connect(self._trust_host)
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
        self.collapse_button = QToolButton()
        self.collapse_button.setText("⏵")
        self.collapse_button.setToolTip(tr("Hide the diagram"))
        self.collapse_button.setProperty("flat", True)
        self.collapse_button.clicked.connect(
            lambda: self.set_erd_collapsed(not self._erd_collapsed)
        )
        header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.erd_stack = QStackedWidget()
        self._erd_panes: dict[str, ErdPane] = {}
        self._erd_hint = QLabel(tr("The database diagram will appear here."))
        self._erd_hint.setProperty("muted", True)
        self._erd_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._erd_hint.setWordWrap(True)
        self.erd_stack.addWidget(self._erd_hint)
        layout.addWidget(self.erd_stack, 1)
        self._erd_pane_widget = pane
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

    def _connect_from_dialog(
        self, connection_id: str, password: object, secrets: object = None
    ) -> None:
        self._activate(
            connection_id,
            password if isinstance(password, str) else None,
            dict(secrets) if isinstance(secrets, dict) else None,
        )

    def _activate(
        self, connection_id: str, password: str | None, secrets: dict[str, str] | None = None
    ) -> None:
        config = self._services.store.find(connection_id)
        if config is None:
            return
        manager = self._services.manager
        typed_secrets = dict(secrets or {})
        if isinstance(config, ServerConnection):
            if config.save_password and not ensure_unlocked(self._services.secrets, self):
                self._status_label.setText(tr("The password store stayed locked."))
                return
            if password is not None:
                manager.remember_password(connection_id, password)
            for field, value in typed_secrets.items():
                manager.remember_secret(connection_id, field, value)
            for field in manager.missing_secrets(connection_id):
                typed, accepted = QInputDialog.getText(
                    self,
                    tr("Password required"),
                    _secret_prompt(field, config.name),
                    QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    return
                manager.remember_secret(connection_id, field, typed)
        manager.activate(connection_id)

    def _trust_host(self, connection_id: str) -> None:
        """The SSH server's key is unknown: show its fingerprint, and remember it on a yes."""
        session = self._services.manager.session(connection_id)
        error = session.error
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
            self._services.manager.trust_host_key(error)
            self._services.manager.connect(connection_id)

    def go_to_table(self) -> None:
        """Ctrl+P: pick a table or column of the active connection and show it."""
        session = self._services.manager.active
        schema = session.schema if session is not None else None
        if session is None or schema is None:
            self._status_label.setText(tr("The database structure is not loaded yet."))
            return
        dialog = QuickOpenDialog(schema, self)
        if dialog.exec() and dialog.chosen is not None:
            table, _column = dialog.chosen
            pane = self._erd_panes.get(session.id)
            if pane is not None:
                pane.focus_table(table.key)
            self._open_table(session.id, table)

    def refresh_schema(self) -> None:
        session = self._services.manager.active
        if session is not None and session.state is SessionState.READY:
            session.load_schema()

    def current_pane(self) -> ErdPane | None:
        widget = self.erd_stack.currentWidget()
        return widget if isinstance(widget, ErdPane) else None

    def fit_diagram(self) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.view.fit_all()

    def export_diagram(self) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.export_dialog()

    def reset_diagram(self) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.reset_layout()

    def set_erd_collapsed(self, collapsed: bool) -> None:
        """Shrink the right side to its header (connection switcher) or restore the diagram."""
        self._erd_collapsed = collapsed
        self.erd_stack.setVisible(not collapsed)
        self.collapse_button.setText("⏴" if collapsed else "⏵")
        self.collapse_button.setToolTip(
            tr("Show the diagram") if collapsed else tr("Hide the diagram")
        )
        self.toggle_diagram_action.setChecked(not collapsed)
        self._erd_pane_widget.setMaximumWidth(_COLLAPSED_WIDTH if collapsed else 16_777_215)
        if collapsed:
            total = sum(self.splitter.sizes())
            self.splitter.setSizes([total - _COLLAPSED_WIDTH, _COLLAPSED_WIDTH])
        self._services.db.set_state(_ERD_COLLAPSED_KEY, collapsed)

    def _open_table(self, connection_id: str, table: Table) -> None:
        workspace = self._workspaces.get(connection_id)
        if workspace is not None:
            workspace.open_table(table)

    def _insert_text(self, connection_id: str, text: str) -> None:
        workspace = self._workspaces.get(connection_id)
        if workspace is not None:
            workspace.insert_text(text)

    def _select_all_from(self, connection_id: str, table: Table) -> None:
        workspace = self._workspaces.get(connection_id)
        if workspace is not None:
            workspace.select_all_from(table)

    def set_theme(self, name: str) -> None:
        self._services.settings.theme = name  # type: ignore[assignment]
        self._services.settings_store.save(self._services.settings)
        self._restyle()

    def _restyle(self) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, self._services.settings.theme)
        for workspace in self._workspaces.values():
            workspace.refresh_theme()
        for pane in self._erd_panes.values():
            pane.refresh_theme()
        self._refresh()

    def _on_system_scheme_changed(self, *_: object) -> None:
        """The operating system switched between light and dark: follow it if asked to."""
        if not self._closed and self._services.settings.theme == "system":
            self._restyle()

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
        if isinstance(event, SchemaChanged):
            pane = self._erd_panes.get(event.connection_id)
            if pane is not None:
                pane.update_from_session()
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
                usage=self._services.usage_store,
                keyword_case=lambda: self._services.settings.keyword_case,
                edit_keys=self._services.edit_keys,
                history=self._services.history,
                saved=self._services.saved,
            )
            self._workspaces[session.id] = workspace
            self.workspaces.addWidget(workspace)
        workspace.set_session(session)
        return workspace

    def _pane_for(self, session: Session) -> ErdPane:
        pane = self._erd_panes.get(session.id)
        if pane is None:
            pane = ErdPane(session, self._services.erd_store, self._services.db)
            connection_id = session.id
            pane.tableOpenRequested.connect(lambda t, c=connection_id: self._open_table(c, t))
            pane.insertRequested.connect(lambda text, c=connection_id: self._insert_text(c, text))
            pane.selectStarRequested.connect(lambda t, c=connection_id: self._select_all_from(c, t))
            pane.exported.connect(
                lambda path: self._status_label.setText(tr("Diagram saved to {path}", path=path))
            )
            self._erd_panes[session.id] = pane
            self.erd_stack.addWidget(pane)
        else:
            pane.set_session(session)
        return pane

    def _drop_stale_workspaces(self) -> None:
        known = {c.id for c in self._services.store.all()}
        for connection_id in [i for i in self._workspaces if i not in known]:
            workspace = self._workspaces.pop(connection_id)
            workspace.shutdown()
            self.workspaces.removeWidget(workspace)
            workspace.deleteLater()
            self._services.tab_store.forget(connection_id)
            self._services.erd_store.forget(connection_id)
            self._services.usage_store.forget(connection_id)
            self._services.edit_keys.forget(connection_id)
            self._services.history.forget(connection_id)
            self._services.saved.forget(connection_id)
            pane = self._erd_panes.pop(connection_id, None)
            if pane is not None:
                self.erd_stack.removeWidget(pane)
                pane.deleteLater()

    def set_keyword_case(self, value: str) -> None:
        if value in ("upper", "lower", "preserve"):
            self._services.settings.keyword_case = value  # type: ignore[assignment]
            self._services.settings_store.save(self._services.settings)

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
            self.erd_stack.setCurrentWidget(self._pane_for(session))
        else:
            self.left.setCurrentWidget(self.panel)
            self.erd_stack.setCurrentWidget(self._erd_hint)
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
        if db.get_state(_ERD_COLLAPSED_KEY, False) is True:
            self.set_erd_collapsed(True)

    def _save_state(self) -> None:
        db = self._services.db
        db.set_state(_GEOMETRY_KEY, bytes(self.saveGeometry().toBase64().data()).decode("ascii"))
        db.set_state(_SPLITTER_KEY, self.splitter.sizes())

    def _confirm_quit(self) -> bool:
        pending = sum(w.pending_changes() for w in self._workspaces.values())
        if not pending:
            return True
        answer = QMessageBox.question(
            self,
            tr("Quit without applying?"),
            tr("There are {n} changes in the grids that were not applied.", n=pending)
            + "\n\n"
            + tr("Quit and discard them?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._closed and not self._confirm_quit():
            event.ignore()
            return
        if not self._closed:  # a second close request must not touch the closed services
            self._closed = True
            for workspace in self._workspaces.values():
                workspace.shutdown()
            self._save_state()
            self._runner.shutdown()
            self._services.close()
        super().closeEvent(event)
