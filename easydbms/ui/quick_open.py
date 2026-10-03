"""Ctrl+P: jump to a table or column by typing a few letters of its name."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.schema import DatabaseSchema, Table
from .i18n import tr

_MAX_ROWS = 100


class QuickOpenDialog(QDialog):
    def __init__(self, schema: DatabaseSchema, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Go to table or column"))
        self.setMinimumWidth(460)
        self._schema = schema
        self.chosen: tuple[Table, str | None] | None = None
        layout = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("Type a table or column name…"))
        self.edit.textChanged.connect(self._refill)
        self.edit.installEventFilter(self)
        self.list = QListWidget()
        self.list.setMinimumHeight(260)
        self.list.itemActivated.connect(lambda _item: self.accept())
        layout.addWidget(self.edit)
        layout.addWidget(self.list)
        self._refill("")

    # ------------------------------------------------------------------ matching

    def matches(self, text: str) -> list[tuple[Table, str | None]]:
        """Tables, then columns, best first: prefix matches before inner matches."""
        needle = text.strip().casefold()
        scored: list[tuple[int, str, Table, str | None]] = []
        for table in self._schema.tables:
            name = table.name.casefold()
            if not needle or needle in name:
                scored.append((0 if name.startswith(needle) else 1, name, table, None))
        if needle:
            for table in self._schema.tables:
                for column in table.columns:
                    lowered = column.name.casefold()
                    if needle in lowered:
                        rank = 2 if lowered.startswith(needle) else 3
                        scored.append(
                            (rank, f"{table.name}.{column.name}".casefold(), table, column.name)
                        )
        scored.sort(key=lambda row: (row[0], row[1]))
        return [(table, column) for _, _, table, column in scored[:_MAX_ROWS]]

    def _refill(self, text: str) -> None:
        self.list.clear()
        for table, column in self.matches(text):
            label = table.name if column is None else f"{table.name}.{column}"
            suffix = tr("view") if table.is_view and column is None else ""
            item = QListWidgetItem(label + (f"   · {suffix}" if suffix else ""))
            item.setData(Qt.ItemDataRole.UserRole, (table, column))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    # ------------------------------------------------------------------ keys

    def eventFilter(self, watched: object, event: object) -> bool:
        if (
            watched is self.edit
            and isinstance(event, QKeyEvent)
            and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up)
        ):
            if event.type() == QEvent.Type.KeyPress:
                step = 1 if event.key() == Qt.Key.Key_Down else -1
                row = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
                self.list.setCurrentRow(row)
            return True
        return super().eventFilter(watched, event)  # type: ignore[arg-type]

    def accept(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.chosen = item.data(Qt.ItemDataRole.UserRole)
            super().accept()
