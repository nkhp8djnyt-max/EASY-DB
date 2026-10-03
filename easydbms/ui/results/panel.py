"""The results area under an editor: one tab per executed statement."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.db import QueryError, QueryResult
from ...core.queries import Outcome, StatementOutcome
from ..i18n import tr
from ..theme import current_tokens
from .grid import ResultGrid
from .model import ResultTableModel

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
    def __init__(self, result: QueryResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = ResultTableModel(result)
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
        self.grid.set_result_model(self.model)
        self.empty = self._empty_state()
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.empty, 1)
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs)
        self._hint = QLabel(tr("Run a query to see results here."))
        self._hint.setProperty("muted", True)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._hint)
        self._counter = 0
        self._sync_hint()

    def clear(self) -> None:
        self.tabs.clear()
        self._counter = 0
        self._sync_hint()

    def count(self) -> int:
        return self.tabs.count()

    def add_outcome(self, outcome: StatementOutcome) -> None:
        """Add a tab for one finished statement."""
        self._counter += 1
        number = self._counter
        statement = outcome.statement
        if outcome.outcome is Outcome.OK and outcome.result is not None:
            result = outcome.result
            if result.returns_rows or statement.keyword in _ROW_STATEMENTS:
                page: QWidget = _GridPage(result)
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
