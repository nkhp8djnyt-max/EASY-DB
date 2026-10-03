"""The strip under an editable grid: what is pending, and the buttons to apply or discard it."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from ..i18n import tr


def _set_flag(widget: QWidget, name: str, value: bool) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class EditBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(8, 4, 8, 6)
        column.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.status = QLabel()
        self.status.setProperty("muted", True)
        self.status.setWordWrap(True)  # a long message wraps instead of widening the window
        self.undo_button = QToolButton()
        self.undo_button.setText("↶")
        self.undo_button.setToolTip(tr("Undo (Ctrl+Z)"))
        self.redo_button = QToolButton()
        self.redo_button.setText("↷")
        self.redo_button.setToolTip(tr("Redo (Ctrl+Y)"))
        self.discard_button = QPushButton(tr("Discard") + "  (Esc)")
        self.apply_button = QPushButton(tr("Apply") + "  (Alt+S)")
        self.apply_button.setProperty("primary", True)
        self.reload_button = QPushButton(tr("Reload"))
        self.reload_button.setToolTip(tr("Load the current rows; your changes are kept"))
        self.key_button = QPushButton(tr("Choose key columns…"))
        row.addWidget(self.status, 1)
        for button in (
            self.key_button,
            self.reload_button,
            self.undo_button,
            self.redo_button,
            self.discard_button,
            self.apply_button,
        ):
            row.addWidget(button)
        column.addLayout(row)

        self.problem = QLabel()
        self.problem.setProperty("error", True)
        self.problem.setWordWrap(True)
        self.problem.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.problem)
        self.set_problem("")
        self.show_read_only("")

    # ------------------------------------------------------------------ states

    def show_read_only(self, reason: str, *, key_button: bool = False) -> None:
        """A result that cannot be edited: say why (empty ``reason`` hides the bar's controls)."""
        self.status.setText(reason)
        _set_flag(self.status, "muted", True)
        for button in (
            self.undo_button,
            self.redo_button,
            self.discard_button,
            self.apply_button,
            self.reload_button,
        ):
            button.hide()
        self.key_button.setVisible(key_button)
        self.setVisible(bool(reason))

    def show_editable(
        self, text: str, *, pending: bool, can_undo: bool, can_redo: bool, reload: bool = False
    ) -> None:
        self.setVisible(True)
        self.status.setText(text)
        _set_flag(self.status, "muted", not pending)
        self.key_button.hide()
        self.undo_button.show()
        self.redo_button.show()
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)
        self.discard_button.setVisible(pending)
        self.apply_button.setVisible(pending)
        self.reload_button.setVisible(reload)

    def set_problem(self, text: str) -> None:
        self.problem.setText(text)
        self.problem.setVisible(bool(text))

    def set_busy(self, busy: bool) -> None:
        """Nothing can be pressed while the changes are being written."""
        self.setEnabled(not busy)
