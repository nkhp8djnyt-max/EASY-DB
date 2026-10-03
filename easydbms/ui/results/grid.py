"""The read-only result grid."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from .model import ResultTableModel, format_cell

_SAMPLE_ROWS = 200
_MIN_COLUMN = 60
_MAX_COLUMN = 420


class ResultGrid(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.setWordWrap(False)
        self.verticalHeader().setDefaultSectionSize(26)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSortIndicatorShown(True)
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

    def set_result_model(self, model: ResultTableModel) -> None:
        self.setModel(model)
        self.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self._fit_columns(model)

    def _fit_columns(self, model: ResultTableModel) -> None:
        """Size columns from the header and the first rows (measuring every row would be slow)."""
        metrics = self.fontMetrics()
        sample = min(model.rowCount(), _SAMPLE_ROWS)
        for column in range(model.columnCount()):
            widest = metrics.horizontalAdvance(model.result.columns[column]) + 44
            for row in range(sample):
                value = model.value(row, column)
                text = "NULL" if value is None else format_cell(value)
                widest = max(widest, metrics.horizontalAdvance(text[:80]) + 36)
            self.setColumnWidth(column, max(_MIN_COLUMN, min(widest, _MAX_COLUMN)))

    # ------------------------------------------------------------------ copy

    def selected_text(self, *, header: bool = False) -> str:
        """The selection as tab-separated text (rows in view order, NULL as an empty field)."""
        model = self.model()
        selection = self.selectionModel()
        if not isinstance(model, ResultTableModel) or selection is None:
            return ""
        indexes = selection.selectedIndexes()
        if not indexes:
            return ""
        rows = sorted({i.row() for i in indexes})
        columns = sorted({i.column() for i in indexes})
        chosen = {(i.row(), i.column()) for i in indexes}
        lines: list[str] = []
        if header:
            lines.append("\t".join(model.result.columns[c] for c in columns))
        for row in rows:
            cells = []
            for column in columns:
                value = model.value(row, column) if (row, column) in chosen else None
                cells.append("" if value is None else _clean(format_full(value)))
            lines.append("\t".join(cells))
        return "\n".join(lines)

    def copy_selection(self, *, header: bool = False) -> None:
        text = self.selected_text(header=header)
        if text:
            QGuiApplication.clipboard().setText(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_selection(header=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        super().keyPressEvent(event)


def format_full(value: object) -> str:
    """Complete text of a value for copying (cell display truncates long text)."""
    return value if isinstance(value, str) else format_cell(value)


def _clean(text: str) -> str:
    return text.replace("\t", " ").replace("\n", " ").replace("\r", "")
