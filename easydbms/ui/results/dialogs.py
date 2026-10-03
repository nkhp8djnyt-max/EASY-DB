"""Dialogs of the editable grid: review the SQL, pick key columns, edit long text."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.dialects import Dialect
from ...core.editing import PlannedStatement, preview_script
from ...core.schema import Table
from ..editor import SqlHighlighter
from ..i18n import tr
from ..theme import current_syntax, current_tokens


class PreviewDialog(QDialog):
    """Shows what will run (as SQL with the values written out) and asks for the go-ahead."""

    def __init__(
        self,
        planned: Sequence[PlannedStatement],
        dialect: Dialect,
        *,
        connection_name: str = "",
        production: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Review the changes"))
        self.resize(760, 460)
        self.planned = tuple(planned)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if production:
            banner = QLabel(tr("PRODUCTION") + f" — {connection_name}")
            banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            banner.setStyleSheet(
                f"background-color: {current_tokens().danger}; color: white; font-weight: 700;"
                " padding: 4px; border-radius: 4px;"
            )
            layout.addWidget(banner)

        self.summary = QLabel(self._summary())
        self.summary.setProperty("heading", True)
        layout.addWidget(self.summary)

        self.sql = QPlainTextEdit()
        self.sql.setReadOnly(True)
        self.sql.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.sql.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._highlighter = SqlHighlighter(self.sql.document(), dialect)
        self._highlighter.set_colors(current_syntax())
        self.sql.setPlainText(preview_script(self.planned))
        layout.addWidget(self.sql, 1)

        note = QLabel(
            tr("All statements run in one transaction. If one fails, nothing is written.")
        )
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        self.copy_button = QPushButton(tr("Copy SQL"))
        self.apply_button = QPushButton(tr("Apply") + "  (Alt+S)")
        self.apply_button.setProperty("primary", True)
        self.apply_button.setDefault(True)
        self.cancel_button = QPushButton(tr("Cancel"))
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.addButton(self.apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.copy_button.clicked.connect(self._copy)
        layout.addWidget(buttons)
        QShortcut(QKeySequence("Alt+S"), self, self.accept)

    def _summary(self) -> str:
        counts = {"update": 0, "insert": 0, "delete": 0}
        for planned in self.planned:
            counts[planned.kind] += 1
        parts = []
        if counts["update"]:
            parts.append(tr("rows to change: {n}", n=counts["update"]))
        if counts["insert"]:
            parts.append(tr("rows to add: {n}", n=counts["insert"]))
        if counts["delete"]:
            parts.append(tr("rows to delete: {n}", n=counts["delete"]))
        return " · ".join(parts)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.sql.toPlainText())


class KeyDialog(QDialog):
    """Pick the columns that identify one row of a table that has no primary key."""

    def __init__(
        self, table: Table, current: Sequence[str] = (), parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Choose key columns"))
        self.resize(420, 420)
        self._table = table
        layout = QVBoxLayout(self)
        text = QLabel(
            tr(
                "The table {name} has no primary key. Tick the columns whose values are unique "
                "for every row: changes find their row by them. A change that would touch more "
                "than one row is refused and nothing is written.",
                name=table.name,
            )
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        self.list = QListWidget()
        for column in table.columns:
            item = QListWidgetItem(f"{column.name}  ·  {column.type}")
            item.setData(Qt.ItemDataRole.UserRole, column.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if column.name in current else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.list.itemChanged.connect(lambda _item: self._sync())
        layout.addWidget(self.list, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._sync()

    def chosen(self) -> tuple[str, ...]:
        """The ticked columns in table order."""
        return tuple(
            str(self.list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.list.count())
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        )

    def _sync(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.chosen()))


class TextDialog(QDialog):
    """A multi-line editor for long text values."""

    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.editor.toPlainText()
