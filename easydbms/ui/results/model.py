"""Table model over a query result: formatting, client-side sort and filter."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor

from ...core.columnar import ColumnStore
from ...core.db import QueryResult
from ..theme import current_tokens

_MAX_DISPLAY = 400
_NUMERIC = (int, float, Decimal)
_Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()


def format_cell(value: object) -> str:
    """How a value looks in a cell (NULL is handled by the caller)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        shown = raw[: _MAX_DISPLAY // 2].hex()
        return f"\\x{shown}" + ("…" if len(raw) > _MAX_DISPLAY // 2 else "")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    text = str(value)
    flat = text.replace("\n", "⏎").replace("\r", "")
    return flat if len(flat) <= _MAX_DISPLAY else flat[:_MAX_DISPLAY] + "…"


class ResultTableModel(QAbstractTableModel):
    def __init__(self, result: QueryResult, parent: Any = None) -> None:
        super().__init__(parent)
        self._result = result
        self._store = ColumnStore(result.columns, result.rows)
        self._view: np.ndarray | None = None  # row indices currently shown, None = all in order
        self._sort: tuple[int, bool] | None = None
        self._filter = ""
        self._numeric = [
            bool(result.rows)
            and all(isinstance(r[i], _NUMERIC) or r[i] is None for r in result.rows[:50])
            for i in range(len(result.columns))
        ]

    # ------------------------------------------------------------------ properties

    @property
    def result(self) -> QueryResult:
        return self._result

    @property
    def total_rows(self) -> int:
        return len(self._result.rows)

    @property
    def sort_state(self) -> tuple[int, bool] | None:
        return self._sort

    @property
    def filter_text(self) -> str:
        return self._filter

    def source_row(self, row: int) -> int:
        return int(self._view[row]) if self._view is not None else row

    def value(self, row: int, column: int) -> Any:
        return self._result.rows[self.source_row(row)][column]

    # ------------------------------------------------------------------ Qt model API

    def rowCount(self, parent: _Index = _ROOT) -> int:
        if parent.isValid():
            return 0
        return len(self._view) if self._view is not None else len(self._result.rows)

    def columnCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._result.columns)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        value = self.value(index.row(), index.column())
        if role == Qt.ItemDataRole.DisplayRole:
            return "NULL" if value is None else format_cell(value)
        if role == Qt.ItemDataRole.ForegroundRole and value is None:
            return QColor(current_tokens().text_muted)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            numeric = self._numeric[index.column()]
            horizontal = Qt.AlignmentFlag.AlignRight if numeric else Qt.AlignmentFlag.AlignLeft
            return int(horizontal | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole and value is not None:
            text = value.hex() if isinstance(value, bytes) else str(value)
            return text if len(text) <= 2000 else text[:2000] + "…"
        if role == Qt.ItemDataRole.UserRole:
            return value
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return self._result.columns[section]
            return str(section + 1)
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if column < 0:
            self._sort = None
        else:
            self._sort = (column, order == Qt.SortOrder.DescendingOrder)
        self._rebuild()

    # ------------------------------------------------------------------ filter

    def set_filter(self, text: str) -> None:
        self._filter = text.strip()
        self._rebuild()

    def _rebuild(self) -> None:
        matching = self._store.search(self._filter) if self._filter else None
        if self._sort is not None:
            column, descending = self._sort
            view: np.ndarray | None = self._store.sort_order(column, descending, matching)
        else:
            view = matching
        self.beginResetModel()
        self._view = view
        self.endResetModel()
