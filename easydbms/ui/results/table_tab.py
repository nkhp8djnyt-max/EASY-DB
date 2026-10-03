"""A table's rows, page by page, with server-side sorting and a WHERE filter."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.browse import FilterError, page_sql
from ...core.db import QueryResult
from ...core.dialects import Dialect
from ...core.schema import Table
from ..i18n import tr
from .grid import ResultGrid
from .model import ResultTableModel

#: ``loader(sql, max_rows)`` runs one statement somewhere off the GUI thread.
PageLoader = Callable[[str, int], "Future[QueryResult]"]

_GRID, _MESSAGE = 0, 1


class TableTab(QWidget):
    #: ``(request id, result or None, error or None)``: emitted from a worker thread, queued.
    _arrived = Signal(int, object, object)

    def __init__(
        self,
        table: Table,
        reference: str,
        dialect: Dialect,
        loader: PageLoader,
        page_size: Callable[[], int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.table = table
        self.reference = reference
        self._dialect = dialect
        self._loader = loader
        self._page_size = page_size
        self._offset = 0
        self._sort: tuple[str, bool] | None = None
        self._where = ""
        self._request = 0
        self._has_next = False
        self._shown_rows = 0
        self._loading = False
        self.alive = True
        self._build()
        self._arrived.connect(self._on_arrived)

    # ------------------------------------------------------------------ construction

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        bar.setSpacing(6)
        self.reload_button = self._button("⟳", tr("Reload this page"))
        self.prev_button = self._button("◀", tr("Previous page"))
        self.next_button = self._button("▶", tr("Next page"))
        self.reload_button.clicked.connect(self.reload)
        self.prev_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(tr("WHERE condition, e.g. id > 10 AND name LIKE 'a%'"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.returnPressed.connect(self.apply_filter)
        self.filter_edit.textChanged.connect(
            lambda _: self.filter_edit.setProperty("invalid", False)
        )
        self.summary = QLabel()
        self.summary.setProperty("muted", True)
        for widget in (self.reload_button, self.prev_button, self.next_button):
            bar.addWidget(widget)
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(self.summary)
        layout.addLayout(bar)

        host = QWidget()
        self._pages = QStackedLayout(host)
        self.grid = ResultGrid()
        self.grid.setSortingEnabled(False)  # sorting happens on the server
        header = self.grid.horizontalHeader()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self._pages.addWidget(self.grid)
        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._pages.addWidget(self._message)
        layout.addWidget(host, 1)
        self._sync_buttons()

    @staticmethod
    def _button(text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedSize(30, 30)
        button.setProperty("compact", True)
        return button

    # ------------------------------------------------------------------ paging

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def sort(self) -> tuple[str, bool] | None:
        return self._sort

    @property
    def where(self) -> str:
        return self._where

    def load(self, offset: int = 0) -> None:
        """Fetch the page starting at row ``offset`` (a stale answer is discarded)."""
        size = self._page_size()
        order = [self._sort] if self._sort is not None else []
        try:
            sql = page_sql(
                self._dialect,
                self.table,
                limit=size + 1,  # one extra row tells whether a next page exists
                offset=offset,
                order_by=order,
                where=self._where,
            )
        except FilterError as error:
            self.filter_edit.setProperty("invalid", True)
            self.filter_edit.style().unpolish(self.filter_edit)
            self.filter_edit.style().polish(self.filter_edit)
            self._show_error(str(error))
            return
        self._request += 1
        request = self._request
        self._loading = True
        self._offset = offset
        self.summary.setText(tr("Loading…"))
        self._sync_buttons()
        try:
            future = self._loader(sql, size + 1)
        except Exception as error:  # the session is gone
            self._loading = False
            self._show_error(str(error))
            return
        future.add_done_callback(partial(self._deliver, request))

    def _deliver(self, request: int, future: Future[QueryResult]) -> None:
        try:
            result = future.result()
        except Exception as error:  # includes DbError and a cancelled future
            self._emit(request, None, error)
        else:
            self._emit(request, result, None)

    def _emit(self, request: int, result: object, error: object) -> None:
        with contextlib.suppress(RuntimeError):  # the widget was deleted mid-query
            self._arrived.emit(request, result, error)

    def reload(self) -> None:
        self.load(self._offset)

    def next_page(self) -> None:
        if self._has_next and not self._loading:
            self.load(self._offset + self._page_size())

    def previous_page(self) -> None:
        if self._offset > 0 and not self._loading:
            self.load(max(0, self._offset - self._page_size()))

    def apply_filter(self) -> None:
        self._where = self.filter_edit.text().strip()
        self.load(0)

    def _on_header_clicked(self, section: int) -> None:
        model = self.grid.model()
        if not isinstance(model, ResultTableModel) or self._loading:
            return
        column = model.result.columns[section]
        if self._sort is None or self._sort[0] != column:
            self._sort = (column, False)
        elif not self._sort[1]:
            self._sort = (column, True)
        else:
            self._sort = None
        self.load(0)

    # ------------------------------------------------------------------ results

    def _on_arrived(self, request: int, result: object, error: object) -> None:
        if not self.alive or request != self._request:
            return
        self._loading = False
        if isinstance(result, QueryResult):
            self._show_result(result)
        else:
            self._show_error(str(error))
        self._sync_buttons()

    def _show_result(self, result: QueryResult) -> None:
        size = self._page_size()
        self._has_next = len(result.rows) > size
        page = QueryResult(
            columns=result.columns,
            rows=result.rows[:size],
            rowcount=None,
            truncated=False,
            duration=result.duration,
        )
        self._shown_rows = len(page.rows)
        model = ResultTableModel(page)
        self.grid.set_result_model(model)
        header = self.grid.horizontalHeader()
        if self._sort is not None and self._sort[0] in page.columns:
            order = Qt.SortOrder.DescendingOrder if self._sort[1] else Qt.SortOrder.AscendingOrder
            header.setSortIndicator(page.columns.index(self._sort[0]), order)
        else:
            header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        if not page.rows:
            self._show_message(
                tr("No rows match the filter.") if self._where else tr("This table is empty.")
            )
        else:
            self._pages.setCurrentIndex(_GRID)
        first = self._offset + 1 if page.rows else 0
        last = self._offset + len(page.rows)
        text = tr("rows {first}-{last}", first=f"{first:,}", last=f"{last:,}")
        estimate = self.table.row_estimate
        if estimate is not None and not self._where and estimate > 0:
            text += " · " + tr("about {n} in the table", n=f"{estimate:,}")
        self.summary.setText(text + f" · {result.duration * 1000:.0f} ms")
        self.summary.setProperty("error", False)

    def _show_message(self, text: str, *, error: bool = False) -> None:
        self._message.setText(text)
        self._message.setProperty("error", error)
        self._message.style().unpolish(self._message)
        self._message.style().polish(self._message)
        self._pages.setCurrentIndex(_MESSAGE)

    def _show_error(self, text: str) -> None:
        self._show_message(text, error=True)
        self.summary.setText(tr("Could not load the rows"))

    def _sync_buttons(self) -> None:
        self.prev_button.setEnabled(self._offset > 0 and not self._loading)
        self.next_button.setEnabled(self._has_next and not self._loading)
        self.reload_button.setEnabled(not self._loading)

    def shutdown(self) -> None:
        self.alive = False
