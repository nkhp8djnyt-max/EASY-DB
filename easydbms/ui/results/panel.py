"""The results area under an editor: one tab per executed statement."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.db import QueryError, QueryResult
from ...core.dialects import Statement
from ...core.editing import EditTarget, ReadOnly, ReadOnlyReason, target_for_query
from ...core.queries import Outcome, StatementOutcome
from ...core.schema import TableKey
from ..i18n import tr
from ..theme import current_tokens
from .editing import EditingContext, GridEditor
from .grid import ResultGrid
from .model import ResultTableModel
from .table_tab import TableTab

#: Statements that produce a result set even when it has no rows (and so no known columns).
_ROW_STATEMENTS = frozenset({"SELECT", "WITH", "VALUES", "TABLE", "SHOW", "EXPLAIN", "("})


def _seconds(value: float) -> str:
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


class _Message(QWidget):
    """Status text for statements without rows, and for errors."""

    def __init__(self, title: str, body: str, *, error: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QLabel(title)
        heading.setProperty("heading", True)
        heading.setProperty("error", error)
        text = QLabel(body)
        text.setWordWrap(True)
        text.setProperty("error", error)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(heading)
        layout.addWidget(text)
        layout.addStretch(1)


class _GridPage(QWidget):
    def __init__(
        self,
        result: QueryResult,
        statement: Statement | None = None,
        editing: EditingContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.statement = statement
        self._columns = result.columns
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("Filter rows…"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMaximumWidth(280)
        self.filter_edit.textChanged.connect(self._on_filter)
        self.summary = QLabel()
        self.summary.setProperty("muted", True)
        bar.addWidget(self.filter_edit)
        bar.addStretch(1)
        bar.addWidget(self.summary)
        layout.addLayout(bar)

        self.grid = ResultGrid()
        self.empty = self._empty_state()
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.empty, 1)
        self.editor = GridEditor(self.grid, editing)
        self.editor.modelReplaced.connect(self._on_model_replaced)
        layout.addWidget(self.editor.bar)
        self.editor.show_result(result, self._resolver(editing))
        self._update_summary()

    @property
    def model(self) -> ResultTableModel:
        model = self.editor.model
        assert model is not None
        return model

    def _resolver(self, editing: EditingContext | None) -> Callable[..., EditTarget | ReadOnly]:
        statement = self.statement

        def resolve(chosen: object = None) -> EditTarget | ReadOnly:
            context = self.editor.context if hasattr(self, "editor") else editing
            schema = context.schema() if context is not None else None
            if statement is None or context is None:
                return ReadOnly(ReadOnlyReason.NOT_A_QUERY)
            if schema is None:
                return ReadOnly(ReadOnlyReason.UNKNOWN_TABLE)
            return target_for_query(
                statement.body,
                schema,
                context.dialect,
                self._columns,
                lambda _table: chosen,  # type: ignore[arg-type,return-value]
            )

        return resolve

    def _on_model_replaced(self) -> None:
        if hasattr(self, "filter_edit"):
            self.model.set_filter(self.filter_edit.text())
            self._update_summary()

    def _empty_state(self) -> QFrame:
        frame = QFrame()
        column = QVBoxLayout(frame)
        column.addStretch(1)
        icon = QLabel(">_")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"font-size: 34px; font-weight: 700; color: {current_tokens().text_muted};"
        )
        text = QLabel(tr("Your query returned no data"))
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setProperty("muted", True)
        column.addWidget(icon)
        column.addWidget(text)
        column.addStretch(2)
        return frame

    def _on_filter(self, text: str) -> None:
        self.model.set_filter(text)
        self._update_summary()

    def _update_summary(self) -> None:
        result = self.model.result
        shown, total = self.model.rowCount(), self.model.total_rows
        has_rows = total > 0
        self.grid.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        parts = [tr("{count} rows", count=f"{shown:,}")]
        if self.model.filter_text:
            parts = [tr("{shown} of {total} rows", shown=f"{shown:,}", total=f"{total:,}")]
        if result.truncated:
            parts.append(tr("cut off at {limit} rows", limit=f"{total:,}"))
        parts.append(_seconds(result.duration))
        self.summary.setText(" · ".join(parts))


class ResultsPanel(QWidget):
    #: An error with a position: ``(statement start offset, 1-based position inside it or 0)``.
    errorLocated = Signal(int, int)
    #: Edits of a statement's result were written: run the statement again.
    rerunRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_requested)
        layout.addWidget(self.tabs)
        self._table_tabs: dict[TableKey, TableTab] = {}
        self._hint = QLabel(tr("Run a query to see results here."))
        self._hint.setProperty("muted", True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)
        self._counter = 0
        self._editing: EditingContext | None = None
        self._sync_hint()

    def set_editing(self, editing: EditingContext | None) -> None:
        """Let the grids of this panel edit their rows (``None``: everything is read-only)."""
        self._editing = editing
        for tab in self._table_tabs.values():
            tab.set_editing(editing)

    def pending_count(self, *, tables: bool = True) -> int:
        """Changes made in the grids of this panel and not written yet.

        ``tables=False`` counts only the statement results, which a new run replaces.
        """
        total = 0
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if (isinstance(page, TableTab) and tables) or isinstance(page, _GridPage):
                total += page.editor.pending
        return total

    def current_editor(self) -> GridEditor | None:
        page = self.tabs.currentWidget()
        return page.editor if isinstance(page, TableTab | _GridPage) else None

    def clear(self) -> None:
        """Remove the statement results; the open table tabs stay."""
        for index in range(self.tabs.count() - 1, -1, -1):
            page = self.tabs.widget(index)
            if not isinstance(page, TableTab):
                self.tabs.removeTab(index)
                if page is not None:
                    page.deleteLater()
        self._counter = 0
        self._sync_hint()

    def count(self) -> int:
        """Tabs of statement results (table tabs are counted by :meth:`table_count`)."""
        return self.tabs.count() - len(self._table_tabs)

    def table_count(self) -> int:
        return len(self._table_tabs)

    def table_tab(self, key: TableKey) -> TableTab | None:
        return self._table_tabs.get(key)

    def show_table(self, key: TableKey, title: str, create: Callable[[], TableTab]) -> TableTab:
        """Select the tab of table ``key``, creating and loading it on first use."""
        existing = self._table_tabs.get(key)
        if existing is not None:
            self.tabs.setCurrentWidget(existing)
            return existing
        tab = create()
        tab.set_editing(self._editing)
        self._table_tabs[key] = tab
        index = self.tabs.addTab(tab, "▦ " + title)
        self.tabs.setTabToolTip(index, tab.reference)
        self.tabs.setCurrentIndex(index)
        self._sync_hint()
        tab.load()
        return tab

    def _close_requested(self, index: int) -> None:
        page = self.tabs.widget(index)
        if isinstance(page, TableTab):
            if page.pending and not self._confirm_discard(page.pending):
                return
            self._table_tabs.pop(page.table.key, None)
            page.shutdown()
            self.tabs.removeTab(index)
            page.deleteLater()
            self._sync_hint()

    def _confirm_discard(self, count: int) -> bool:
        answer = QMessageBox.question(
            self,
            tr("Discard the changes?"),
            tr("This tab has {n} changes that were not applied.", n=count)
            + "\n\n"
            + tr("Close it and discard them?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def shutdown(self) -> None:
        for tab in self._table_tabs.values():
            tab.shutdown()

    def add_outcome(self, outcome: StatementOutcome) -> None:
        """Add a tab for one finished statement."""
        self._counter += 1
        number = self._counter
        statement = outcome.statement
        if outcome.outcome is Outcome.OK and outcome.result is not None:
            result = outcome.result
            if result.returns_rows or statement.keyword in _ROW_STATEMENTS:
                grid_page = _GridPage(result, statement, self._editing)
                grid_page.editor.applied.connect(lambda s=statement: self.rerunRequested.emit(s))
                page: QWidget = grid_page
                label = tr("Result {n}", n=number)
            else:
                detail = _seconds(result.duration)
                if result.rowcount is not None:
                    detail = tr("{count} rows affected", count=result.rowcount) + f" · {detail}"
                page = _Message(tr("Query OK"), detail)
                label = tr("OK {n}", n=number)
        elif outcome.outcome is Outcome.ERROR and outcome.error is not None:
            page = _Message(tr("Query failed"), str(outcome.error), error=True)
            label = tr("Error {n}", n=number)
            position = outcome.error.position if isinstance(outcome.error, QueryError) else None
            self.errorLocated.emit(statement.start, position or 0)
        elif outcome.outcome is Outcome.CANCELLED:
            page = _Message(tr("Cancelled"), tr("The statement was stopped."))
            label = tr("Cancelled {n}", n=number)
        else:
            page = _Message(tr("Skipped"), tr("An earlier statement failed or was cancelled."))
            label = tr("Skipped {n}", n=number)
        index = self.tabs.addTab(page, label)
        self.tabs.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, None)
        if outcome.outcome is Outcome.ERROR:
            self.tabs.tabBar().setTabTextColor(index, QColor(current_tokens().danger))
        if self.tabs.count() == 1 or outcome.outcome is Outcome.ERROR:
            self.tabs.setCurrentIndex(index)
        self._sync_hint()

    def current_grid(self) -> ResultGrid | None:
        page = self.tabs.currentWidget()
        return page.grid if isinstance(page, _GridPage) else None

    def current_page(self) -> QWidget | None:
        return self.tabs.currentWidget()

    def _sync_hint(self) -> None:
        empty = self.tabs.count() == 0
        self.tabs.setVisible(not empty)
        self._hint.setVisible(empty)
