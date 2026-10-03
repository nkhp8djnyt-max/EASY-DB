"""The diagram area of one connection: toolbar, canvas and the loading / error / empty states."""

from __future__ import annotations

import re

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.erd import ALL_SCHEMAS, ErdLayoutStore, build_erd
from ...core.schema import Column, DatabaseSchema, Table, TableKey
from ...core.session import SchemaState, Session
from ...core.storage import AppDatabase
from ..i18n import tr
from ..theme import current_tokens
from .export import ExportFormat, export_diagram, file_filters, format_for_path
from .scene import ErdScene
from .view import ErdView

_SEARCH_DELAY_MS = 160
_VIEW, _MESSAGE = 0, 1


class ErdPane(QWidget):
    #: Open a table's data (double click, context menu, quick open).
    tableOpenRequested = Signal(object)
    #: Text to put in the editor at the cursor (a column or table name).
    insertRequested = Signal(str)
    #: Put ``SELECT * FROM <table>`` into the editor.
    selectStarRequested = Signal(object)
    #: The diagram was written to this path.
    exported = Signal(str)

    def __init__(
        self,
        session: Session,
        layouts: ErdLayoutStore,
        state: AppDatabase,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._layouts = layouts
        self._state = state
        self._scope: str | None = None
        self._shown_version = -1
        self._needs_fit = False
        self.scene = ErdScene(current_tokens(), self)
        self.view = ErdView(self.scene)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._apply_filter)
        self._build()
        self.scene.tableOpenRequested.connect(self.tableOpenRequested)
        self.scene.columnClicked.connect(self._on_column_clicked)
        self.scene.menuRequested.connect(self._show_menu)
        self.scene.cardsMoved.connect(self._remember_positions)
        self.update_from_session()

    # ------------------------------------------------------------------ construction

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search tables and columns…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        self.search.returnPressed.connect(self._jump_to_matches)
        self.scope_combo = QComboBox()
        self.scope_combo.setToolTip(tr("Schema shown in the diagram"))
        self.scope_combo.activated.connect(self._on_scope_chosen)
        self.refresh_button = self._tool_button("↻", tr("Re-read the database structure"))
        self.refresh_button.clicked.connect(self.reload)
        self.reset_button = self._tool_button("⟲", tr("Reset the card layout"))
        self.reset_button.clicked.connect(self.reset_layout)
        self.export_button = self._tool_button(
            "⤓", tr("Export the diagram as an image, PDF or text…")
        )
        self.export_button.clicked.connect(self.export_dialog)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.scope_combo)
        bar.addWidget(self.refresh_button)
        bar.addWidget(self.reset_button)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self._build_message_page())
        layout.addWidget(self.stack, 1)

        self.summary = QLabel()
        self.summary.setProperty("muted", True)
        layout.addWidget(self.summary)

    def _tool_button(self, text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedSize(32, 32)
        button.setProperty("compact", True)
        return button

    def _build_message_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.addStretch(1)
        self._message_title = QLabel()
        self._message_title.setProperty("heading", True)
        self._message_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_body = QLabel()
        self._message_body.setWordWrap(True)
        self._message_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.retry_button = QPushButton(tr("Try again"))
        self.retry_button.clicked.connect(self.reload)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.retry_button)
        row.addStretch(1)
        column.addWidget(self._message_title)
        column.addWidget(self._message_body)
        column.addLayout(row)
        column.addStretch(2)
        return page

    # ------------------------------------------------------------------ state

    def set_session(self, session: Session) -> None:
        """Follow a new session object for the same connection (after it was edited)."""
        if session is not self._session:
            self._session = session
            self._shown_version = -1
            self.update_from_session()

    def update_from_session(self) -> None:
        """Show whatever the session's schema state calls for (call on every schema event)."""
        session = self._session
        state = session.schema_state
        schema = session.schema
        if schema is not None and session.schema_version != self._shown_version:
            self._show_schema(schema)
        if state is SchemaState.ERROR:
            error = session.schema_error
            self._message(tr("Could not read the database structure"), str(error or ""), error=True)
        elif schema is None:
            if state is SchemaState.LOADING:
                self._message(tr("Reading the database structure…"), "")
            else:
                self._message(tr("The database structure has not been read yet."), "")
        elif not schema.tables:
            self._message(
                tr("This database has no tables yet."),
                tr("Create one in the editor and press refresh."),
            )
        else:
            self.stack.setCurrentIndex(_VIEW)
        self.refresh_button.setEnabled(state is not SchemaState.LOADING)

    def _message(self, title: str, body: str, *, error: bool = False) -> None:
        self._message_title.setText(title)
        self._message_title.setProperty("error", error)
        self._message_body.setText(body)
        self._message_body.setVisible(bool(body))
        self.retry_button.setVisible(error)
        for label in (self._message_title,):
            label.style().unpolish(label)
            label.style().polish(label)
        self.stack.setCurrentIndex(_MESSAGE)

    def reload(self) -> None:
        if self._session.client is not None:
            self._session.load_schema()

    def _show_schema(self, schema: DatabaseSchema) -> None:
        first = self._shown_version < 0
        self._shown_version = self._session.schema_version
        self._fill_scope_combo(schema)
        self._build_scene(schema, refit=first)

    def _fill_scope_combo(self, schema: DatabaseSchema) -> None:
        saved = self._state.get_state(self._scope_key(), None)
        schemas = list(schema.schemas)
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem(tr("All schemas"), None)
        for name in schemas:
            self.scope_combo.addItem(name, name)
        self.scope_combo.setVisible(len(schemas) > 1)
        if len(schemas) <= 1 or saved == ALL_SCHEMAS:
            self._scope = None
        elif isinstance(saved, str) and saved in schemas:
            self._scope = saved
        else:
            self._scope = schema.default_schema if schema.default_schema in schemas else None
        self.scope_combo.setCurrentIndex(max(self.scope_combo.findData(self._scope), 0))
        self.scope_combo.blockSignals(False)

    def _scope_key(self) -> str:
        return f"erd/{self._session.id}/scope"

    def _store_scope(self) -> str:
        return ALL_SCHEMAS if self._scope is None else self._scope

    def _build_scene(
        self, schema: DatabaseSchema, *, use_saved: bool = True, refit: bool = True
    ) -> None:
        """Rebuild the scene; ``refit=False`` (a refresh) keeps the zoom and the visible spot."""
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        model = build_erd(schema, self._scope)
        saved = self._layouts.load(self._session.id, self._store_scope()) if use_saved else {}
        known = {t.key for t in model.tables}
        stale = [key for key in saved if key not in known]
        if stale:
            self._layouts.discard(self._session.id, self._store_scope(), stale)
            saved = {k: v for k, v in saved.items() if k in known}
        self.scene.build(model, saved)
        relations = len(model.relations)
        self.summary.setText(
            tr(
                "{tables} tables · {relations} relations",
                tables=len(model.tables),
                relations=relations,
            )
            + (" · " + tr("read in {ms} ms", ms=f"{schema.duration * 1000:.0f}"))
        )
        if refit:
            self._needs_fit = True
            QTimer.singleShot(0, self._fit_when_visible)
        else:
            self.view.centerOn(centre)

    def _fit_when_visible(self) -> None:
        if self._needs_fit and self.view.viewport().width() > 50:
            self._needs_fit = False
            self.view.fit_all()

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        QTimer.singleShot(0, self._fit_when_visible)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        if self._needs_fit:
            QTimer.singleShot(0, self._fit_when_visible)

    # ------------------------------------------------------------------ actions

    def _on_scope_chosen(self, _: int) -> None:
        self._scope = self.scope_combo.currentData()
        self._state.set_state(self._scope_key(), self._store_scope())
        schema = self._session.schema
        if schema is not None:
            self._build_scene(schema)

    def reset_layout(self) -> None:
        """Forget every card the user moved and lay the diagram out again."""
        self._layouts.clear(self._session.id, self._store_scope())
        schema = self._session.schema
        if schema is not None:
            self._build_scene(schema, use_saved=False)

    def refresh_theme(self) -> None:
        self.scene.set_tokens(current_tokens())

    # ------------------------------------------------------------------ export

    def export_to(self, path: str, fmt: ExportFormat | None = None) -> ExportFormat:
        """Write the diagram (as it is filtered now) to ``path``; see :func:`export_diagram`."""
        written = export_diagram(self.scene, path, fmt)
        self.exported.emit(path)
        return written

    def export_dialog(self) -> str | None:
        """Ask where to save the diagram and in which format, then write it."""
        if self.stack.currentWidget() is not self.view or not self.scene.cards():
            QMessageBox.information(
                self, tr("Export the diagram"), tr("There is no diagram to export yet.")
            )
            return None
        filters = file_filters()
        name = re.sub(r"[^\w.-]+", "-", self._session.config.name).strip("-") or "diagram"
        start = f"{name}-diagram"
        path, chosen = QFileDialog.getSaveFileName(
            self,
            tr("Export the diagram"),
            start,
            ";;".join(label for _fmt, label in filters),
        )
        if not path:
            return None
        fmt = format_for_path(path)
        if fmt is None:  # no known suffix typed: take it from the filter that was selected
            fmt = next((f for f, label in filters if label == chosen), ExportFormat.PNG)
            path += fmt.suffix
        try:
            self.export_to(path, fmt)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr("Export the diagram"), str(error))
            return None
        return path

    def _remember_positions(self, moved: object) -> None:
        if isinstance(moved, dict):
            self._layouts.save(self._session.id, self._store_scope(), moved)

    def _apply_filter(self) -> None:
        self.scene.set_filter(self.search.text())
        if self.search.text().strip():
            self.view.fit_all(self.scene.visible_bounds())
        else:
            self.view.fit_all()

    def _jump_to_matches(self) -> None:
        self._search_timer.stop()
        matches = self.scene.set_filter(self.search.text())
        if not matches:
            return
        self.scene.clearSelection()
        first = self.scene.card(matches[0])
        if first is not None:
            first.setSelected(True)
        self.view.fit_all(self.scene.visible_bounds())

    def focus_table(self, key: TableKey) -> bool:
        """Select ``key`` and bring it into view (clearing a search that hides it)."""
        if self.scene.card(key) is None and self._scope is not None and key.schema != self._scope:
            self._scope = key.schema
            self.scope_combo.setCurrentIndex(max(self.scope_combo.findData(self._scope), 0))
            self._state.set_state(self._scope_key(), self._store_scope())
            schema = self._session.schema
            if schema is not None:
                self._build_scene(schema)
        card = self.scene.card(key)
        if card is None:
            return False
        if not card.isVisible():
            self.search.clear()
            self.scene.set_filter("")
        self.scene.clearSelection()
        card.setSelected(True)
        self.view.centerOn(card)
        return True

    def table_reference(self, table: Table) -> str:
        schema = self._session.schema
        dialect = self._session.config.dialect_impl
        if schema is None:
            return dialect.quote_ident_if_needed(table.name)
        return schema.reference(table, dialect)

    def _on_column_clicked(self, table: object, column: object) -> None:
        if isinstance(column, Column):
            dialect = self._session.config.dialect_impl
            self.insertRequested.emit(dialect.quote_ident_if_needed(column.name))

    def _show_menu(self, table: object, position: object) -> None:
        if isinstance(table, Table) and isinstance(position, QPoint):
            self.build_menu(table).exec(position)

    def build_menu(self, table: Table) -> QMenu:
        """The card's context menu (separate from showing it so tests can use it)."""
        menu = QMenu(self)
        actions = (
            (tr("Open data"), lambda: self.tableOpenRequested.emit(table)),
            (tr("SELECT * FROM this table"), lambda: self.selectStarRequested.emit(table)),
            (
                tr("Insert table name"),
                lambda: self.insertRequested.emit(self.table_reference(table)),
            ),
            (
                tr("Copy table name"),
                lambda: QGuiApplication.clipboard().setText(self.table_reference(table)),
            ),
        )
        for text, handler in actions:
            action = QAction(text, menu)
            action.triggered.connect(lambda _checked=False, h=handler: h())
            menu.addAction(action)
        return menu
