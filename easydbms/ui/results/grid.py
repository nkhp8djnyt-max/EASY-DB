"""The result grid: copy as TSV / CSV / Markdown, and the hooks of an attached editor."""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView, QWidget

from ..i18n import tr
from .model import ResultTableModel, format_cell

if TYPE_CHECKING:
    from .editing import GridEditor

_SAMPLE_ROWS = 200
_MIN_COLUMN = 60
_MAX_COLUMN = 420


class ResultGrid(QTableView):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.editor: GridEditor | None = None
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
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

    # ------------------------------------------------------------------ editing hooks

    def attach_editor(self, editor: GridEditor) -> None:
        self.editor = editor

    def event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and self.editor is not None
            and self.editor.wants_key(event.key())
        ):
            event.accept()  # Esc discards the pending changes instead of stopping a query
            return True
        return super().event(event)

    def edit(  # type: ignore[override]
        self,
        index: QModelIndex | QPersistentModelIndex,
        trigger: QAbstractItemView.EditTrigger = QAbstractItemView.EditTrigger.AllEditTriggers,
        event: QEvent | None = None,
    ) -> bool:
        model = self.model()
        if (
            self.editor is not None
            and model is not None
            and trigger != QAbstractItemView.EditTrigger.NoEditTriggers
        ):
            plain = model.index(index.row(), index.column())
            if self.editor.wants_dialog(plain):
                self.editor.edit_in_dialog(plain)  # a long text needs more than one line
                return False
        return bool(super().edit(index, trigger, event))  # type: ignore[arg-type]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            self.editor is not None
            and self.state() != QAbstractItemView.State.EditingState
            and self.editor.handle_key(event)
        ):
            return
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_selection(header=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ context menu

    def build_menu(self, index: QModelIndex | None = None) -> QMenu:
        """The context menu for the cell at ``index`` (copy actions, and the editor's actions)."""
        menu = QMenu(self)
        has_selection = bool(self.selectionModel() and self.selectionModel().hasSelection())
        for text, header, fmt in (
            (tr("Copy") + "\tCtrl+C", False, "tsv"),
            (tr("Copy with headers") + "\tCtrl+Shift+C", True, "tsv"),
            (tr("Copy as CSV"), True, "csv"),
            (tr("Copy as Markdown"), True, "markdown"),
        ):
            action = menu.addAction(text)
            action.setEnabled(has_selection)
            action.triggered.connect(
                lambda _checked=False, h=header, f=fmt: self.copy_selection(header=h, fmt=f)
            )
        if self.editor is not None:
            self.editor.extend_menu(menu, index if index is not None else QModelIndex())
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        index = self.indexAt(event.pos())
        if index.isValid() and self.selectionModel() is not None:
            selection = self.selectionModel()
            if not selection.isSelected(index):
                self.setCurrentIndex(index)
        self.build_menu(index).exec(event.globalPos())

    # ------------------------------------------------------------------ copy

    def selected_text(self, *, header: bool = False, fmt: str = "tsv") -> str:
        """The selection as text: ``tsv`` (default), ``csv`` or ``markdown`` (rows in view order).

        NULL is an empty field in TSV and CSV and ``NULL`` in Markdown; Markdown always has the
        header row.
        """
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
        names = [model.result.columns[c] for c in columns]
        table: list[list[str | None]] = []
        for row in rows:
            cells: list[str | None] = []
            for column in columns:
                value = model.value(row, column) if (row, column) in chosen else None
                cells.append(None if value is None else format_full(value))
            table.append(cells)
        if fmt == "csv":
            return _csv(names if header else None, table)
        if fmt == "markdown":
            return _markdown(names, table)
        lines = ["\t".join(names)] if header else []
        lines += ["\t".join("" if v is None else _clean(v) for v in cells) for cells in table]
        return "\n".join(lines)

    def copy_selection(self, *, header: bool = False, fmt: str = "tsv") -> None:
        text = self.selected_text(header=header, fmt=fmt)
        if text:
            QGuiApplication.clipboard().setText(text)


def format_full(value: object) -> str:
    """Complete text of a value for copying (cell display truncates long text)."""
    return value if isinstance(value, str) else format_cell(value)


def _clean(text: str) -> str:
    return text.replace("\t", " ").replace("\n", " ").replace("\r", "")


def _csv(header: list[str] | None, rows: list[list[str | None]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    if header is not None:
        writer.writerow(header)
    for cells in rows:
        writer.writerow(["" if v is None else v for v in cells])
    return out.getvalue().rstrip("\n")


def _markdown(header: list[str], rows: list[list[str | None]]) -> str:
    def cell(text: str | None) -> str:
        shown = "NULL" if text is None else text
        return shown.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")

    lines = ["| " + " | ".join(cell(h) for h in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    lines += ["| " + " | ".join(cell(v) for v in cells) + " |" for cells in rows]
    return "\n".join(lines)
