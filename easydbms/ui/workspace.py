"""One connection's query workspace: editor tabs, toolbar, results, running and cancelling."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.connections import FileConnection, ServerConnection
from ..core.dialects import (
    MYSQL,
    POSTGRESQL,
    SQLITE,
    Dialect,
    SqlSyntaxError,
    Statement,
    format_sql,
    get_dialect,
)
from ..core.queries import (
    Outcome,
    QueryTabStore,
    ScriptRun,
    StatementOutcome,
    TabState,
    assess,
    is_write,
)
from ..core.queries.danger import DangerKind
from ..core.session import Session
from .connection_form import DIALECT_LABELS
from .editor import SqlEditor
from .i18n import tr
from .results import ResultsPanel

_SAVE_DELAY_MS = 600
_DIALECTS: tuple[Dialect, ...] = (POSTGRESQL, MYSQL, SQLITE)


def _seconds(value: float) -> str:
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.1f} s"


class QueryTab(QSplitter):
    """An editor with its own results underneath."""

    def __init__(self, title: str, dialect: Dialect) -> None:
        super().__init__(Qt.Orientation.Vertical)
        self.title = title
        self.editor = SqlEditor(dialect)
        self.results = ResultsPanel()
        self.addWidget(self.editor)
        self.addWidget(self.results)
        self.setChildrenCollapsible(False)
        self.setSizes([320, 280])
        self.run: ScriptRun | None = None
        self.started = 0.0
        self.alive = True

    @property
    def running(self) -> bool:
        return self.run is not None


class QueryWorkspace(QWidget):
    statusMessage = Signal(str)
    _outcome = Signal(object, int, object)
    _finished = Signal(object, object)

    def __init__(
        self,
        config: ServerConnection | FileConnection,
        tab_store: QueryTabStore,
        row_limit: Callable[[], int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._tab_store = tab_store
        self._row_limit = row_limit
        self._session: Session | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.flush)
        self._clock = QTimer(self)
        self._clock.setInterval(100)
        self._clock.timeout.connect(self._tick)
        self._build()
        self._outcome.connect(self._on_outcome)
        self._finished.connect(self._on_finished)
        self._restore_tabs()
        self._sync_controls()

    # ------------------------------------------------------------------ construction

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_current_changed)
        self.tabs.tabBar().tabBarDoubleClicked.connect(self.rename_tab)
        self.tabs.tabBar().tabMoved.connect(lambda *_: self._schedule_save())
        add = QToolButton()
        add.setText("+")
        add.setToolTip(tr("New query tab (Ctrl+T)"))
        add.setProperty("flat", True)
        add.clicked.connect(lambda: self.new_tab())
        self.tabs.setCornerWidget(add, Qt.Corner.TopRightCorner)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(8)
        self.run_button = QPushButton("▷  " + tr("Run"))
        self.run_button.setProperty("primary", True)
        self.run_button.setToolTip(tr("Run the selection or the current statement (Ctrl+Enter)"))
        self.script_button = QPushButton(tr("Run script"))
        self.script_button.setToolTip(tr("Run every statement in order (F5)"))
        self.stop_button = QPushButton("■  " + tr("Stop"))
        self.stop_button.setToolTip(tr("Cancel the running query (Esc)"))
        self.format_button = QPushButton(tr("Format"))
        self.format_button.setToolTip(tr("Format the SQL (Ctrl+Shift+F)"))
        self.dialect_combo = QComboBox()
        for dialect in _DIALECTS:
            self.dialect_combo.addItem(DIALECT_LABELS[dialect.id], dialect.id)
        self.dialect_combo.setToolTip(tr("SQL dialect of this tab"))
        self.status = QLabel()
        self.status.setProperty("muted", True)
        for widget in (self.run_button, self.script_button, self.stop_button, self.format_button):
            bar.addWidget(widget)
        bar.addWidget(self.dialect_combo)
        bar.addStretch(1)
        bar.addWidget(self.status)

        layout.addWidget(self.tabs, 1)
        bar_host = QWidget()
        bar_host.setLayout(bar)
        layout.insertWidget(0, bar_host)

        self.run_button.clicked.connect(self.run_current)
        self.script_button.clicked.connect(self.run_script)
        self.stop_button.clicked.connect(self.cancel)
        self.format_button.clicked.connect(self.format_current)
        self.dialect_combo.activated.connect(self._on_dialect_chosen)

    # ------------------------------------------------------------------ session

    def set_session(self, session: Session | None) -> None:
        """Attach the live session (or ``None`` when the connection is not ready)."""
        self._session = session
        self._sync_controls()

    @property
    def config(self) -> ServerConnection | FileConnection:
        return self._config

    @property
    def dialect(self) -> Dialect:
        return self._config.dialect_impl

    # ------------------------------------------------------------------ tabs

    def current_tab(self) -> QueryTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, QueryTab) else None

    def all_tabs(self) -> list[QueryTab]:
        return [
            t for i in range(self.tabs.count()) if isinstance(t := self.tabs.widget(i), QueryTab)
        ]

    def new_tab(
        self, title: str | None = None, sql: str = "", dialect: Dialect | None = None
    ) -> QueryTab:
        tab = QueryTab(title or tr("Query {n}", n=self._next_number()), dialect or self.dialect)
        tab.editor.setPlainText(sql)
        tab.editor.document().modificationChanged.connect(lambda *_: self._schedule_save())
        tab.editor.textChanged.connect(self._schedule_save)
        tab.results.errorLocated.connect(
            lambda start, pos, t=tab: self._locate_error(t, start, pos)
        )
        index = self.tabs.addTab(tab, tab.title)
        self.tabs.setCurrentIndex(index)
        tab.editor.setFocus()
        self._schedule_save()
        return tab

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if not isinstance(widget, QueryTab):
            return
        if widget.run is not None:
            widget.run.cancel()
        widget.alive = False
        self.tabs.removeTab(index)
        widget.deleteLater()
        if self.tabs.count() == 0:
            self.new_tab()
        self._schedule_save()

    def close_current_tab(self) -> None:
        self.close_tab(self.tabs.currentIndex())

    def rename_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if not isinstance(tab, QueryTab):
            return
        name, accepted = QInputDialog.getText(
            self, tr("Rename tab"), tr("Tab name:"), text=tab.title
        )
        if accepted and name.strip():
            tab.title = name.strip()
            self.tabs.setTabText(index, tab.title)
            self._schedule_save()

    def _next_number(self) -> int:
        used = {t.title for t in self.all_tabs()}
        number = 1
        while tr("Query {n}", n=number) in used:
            number += 1
        return number

    def _on_current_changed(self, index: int) -> None:
        tab = self.current_tab()
        if tab is not None:
            self.dialect_combo.setCurrentIndex(self.dialect_combo.findData(tab.editor.dialect.id))
            self._tab_store.set_active_index(self._config.id, index)
        self._sync_controls()

    def _on_dialect_chosen(self, _: int) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.editor.set_dialect(get_dialect(self.dialect_combo.currentData()))
            self._schedule_save()

    # ------------------------------------------------------------------ persistence

    def _restore_tabs(self) -> None:
        saved = self._tab_store.load(self._config.id)
        for state in saved:
            self.new_tab(state.title, state.sql, get_dialect(state.dialect))
        if not saved:
            self.new_tab()
        index = min(self._tab_store.active_index(self._config.id), self.tabs.count() - 1)
        self.tabs.setCurrentIndex(max(index, 0))
        self._save_timer.stop()

    def _schedule_save(self) -> None:
        self._save_timer.start(_SAVE_DELAY_MS)

    def flush(self) -> None:
        """Write the tabs to ``app.db`` now."""
        self._save_timer.stop()
        self._tab_store.save(
            self._config.id,
            [
                TabState(t.title, t.editor.text(), t.editor.dialect.id.value)
                for t in self.all_tabs()
            ],
        )

    def shutdown(self) -> None:
        for tab in self.all_tabs():
            if tab.run is not None:
                tab.run.cancel()
        self._clock.stop()
        self.flush()

    def refresh_theme(self) -> None:
        for tab in self.all_tabs():
            tab.editor.refresh_theme()

    # ------------------------------------------------------------------ running

    def run_current(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            self._run(tab, tab.editor.statements_to_run())

    def run_script(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            self._run(tab, tab.editor.all_statements())

    def cancel(self) -> None:
        tab = self.current_tab()
        if tab is not None and tab.run is not None:
            tab.run.cancel()
            self._say(tr("Cancelling…"))

    def _run(self, tab: QueryTab, statements: list[Statement]) -> None:
        session = self._session
        if session is None or session.client is None:
            self._say(tr("The connection is not ready."), error=True)
            return
        if tab.running:
            return
        if not statements:
            self._say(tr("There is nothing to run."))
            return
        if not self._confirmed(statements):
            return
        tab.results.clear()
        tab.started = time.perf_counter()
        tab.run = session.run_script(
            statements, self._row_limit(), partial(self._emit_outcome, tab)
        )
        tab.run.future.add_done_callback(partial(self._emit_finished, tab))
        self._clock.start()
        self._sync_controls()

    def _confirmed(self, statements: list[Statement]) -> bool:
        reasons: list[str] = []
        for statement in statements:
            danger = assess(statement.body, self.dialect)
            if danger is None:
                continue
            what = {
                DangerKind.DROP: tr("drops {target}"),
                DangerKind.TRUNCATE: tr("empties {target}"),
                DangerKind.DELETE_ALL: tr("deletes every row of {target}"),
                DangerKind.UPDATE_ALL: tr("changes every row of {target}"),
            }[danger.kind]
            reasons.append("• " + what.format(target=danger.target or tr("an object")))
        if self._config.production and any(is_write(s.keyword) for s in statements):
            reasons.append(
                "• "
                + tr(
                    "it changes data on the PRODUCTION connection “{name}”", name=self._config.name
                )
            )
        if not reasons:
            return True
        answer = QMessageBox.question(
            self,
            tr("Run this?"),
            tr("This script:") + "\n" + "\n".join(reasons) + "\n\n" + tr("Run it anyway?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _emit_outcome(self, tab: QueryTab, index: int, outcome: StatementOutcome) -> None:
        self._outcome.emit(tab, index, outcome)  # runs on the worker thread: queued to the GUI

    def _emit_finished(self, tab: QueryTab, future: Future[list[StatementOutcome]]) -> None:
        self._finished.emit(tab, future)

    def _on_outcome(self, tab: QueryTab, index: int, outcome: StatementOutcome) -> None:
        if tab.alive:
            tab.results.add_outcome(outcome)

    def _on_finished(self, tab: QueryTab, future: Future[list[StatementOutcome]]) -> None:
        if not tab.alive:
            return
        tab.run = None
        elapsed = time.perf_counter() - tab.started
        outcomes: list[StatementOutcome] = []
        try:
            outcomes = future.result()
        except Exception as error:  # a bug in the runner itself, not a SQL error
            self._say(str(error), error=True)
        failed = sum(1 for o in outcomes if o.outcome is Outcome.ERROR)
        cancelled = any(o.outcome is Outcome.CANCELLED for o in outcomes)
        if failed:
            self._say(tr("Failed after {time}", time=_seconds(elapsed)), error=True)
        elif cancelled:
            self._say(tr("Cancelled after {time}", time=_seconds(elapsed)))
        else:
            self._say(tr("Done in {time}", time=_seconds(elapsed)))
        if not any(t.running for t in self.all_tabs()):
            self._clock.stop()
        self._sync_controls()

    def _locate_error(self, tab: QueryTab, start: int, position: int) -> None:
        if tab.alive:
            tab.editor.go_to(start + max(position - 1, 0))

    def _tick(self) -> None:
        tab = self.current_tab()
        if tab is not None and tab.running:
            self.status.setText(
                tr("Running… {time}", time=_seconds(time.perf_counter() - tab.started))
            )

    # ------------------------------------------------------------------ format

    def format_current(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        editor = tab.editor
        cursor = editor.textCursor()
        selected = cursor.hasSelection()
        source = editor._selected_text(cursor) if selected else editor.text()
        if not source.strip():
            return
        try:
            formatted = format_sql(source, editor.dialect)
        except SqlSyntaxError as error:
            issue = error.issues[0]
            self._say(
                f"{issue.message} ({tr('line')} {issue.line}, {tr('column')} {issue.column})",
                error=True,
            )
            block = editor.document().findBlockByNumber(issue.line - 1)
            editor.go_to(block.position() + max(issue.column - 1, 0))
            return
        cursor.beginEditBlock()
        if not selected:
            cursor.select(cursor.SelectionType.Document)
        cursor.insertText(formatted)
        cursor.endEditBlock()
        self._say(tr("Formatted."))

    # ------------------------------------------------------------------ controls

    def _say(self, text: str, *, error: bool = False) -> None:
        self.status.setProperty("error", error)
        self.status.setProperty("muted", not error)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)
        self.statusMessage.emit(text)

    def _sync_controls(self) -> None:
        tab = self.current_tab()
        ready = self._session is not None and self._session.client is not None
        running = tab is not None and tab.running
        self.run_button.setEnabled(ready and not running)
        self.script_button.setEnabled(ready and not running)
        self.stop_button.setEnabled(running)
        self.format_button.setEnabled(tab is not None)
