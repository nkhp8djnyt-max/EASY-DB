"""Cell editors by column type: a combo for booleans, calendars for dates, checked text."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from PySide6.QtCore import (
    QDate,
    QDateTime,
    QModelIndex,
    QPersistentModelIndex,
    QRegularExpression,
    QTime,
)
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QLineEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTimeEdit,
    QWidget,
)

from ...core.dialects import TypeKind
from ...core.editing import edit_text
from ..i18n import tr
from .editable_model import EditableModel

_Index = QModelIndex | QPersistentModelIndex

_INTEGER = QRegularExpression(r"[+-]?\d*")
_NUMBER = QRegularExpression(r"[+-]?\d*\.?\d*([eE][+-]?\d*)?")
_NULL_CHOICE = object()


class CellDelegate(QStyledItemDelegate):
    """Creates the editor that fits the column and writes the typed value back to the model."""

    def createEditor(  # type: ignore[override]
        self, parent: QWidget, option: QStyleOptionViewItem, index: _Index
    ) -> QWidget | None:
        model = index.model()
        if not isinstance(model, EditableModel):
            return None
        spec = model.column_spec(index.column())
        if spec is None or not spec.editable:
            return None
        value = index.data(0x0002)  # Qt.EditRole
        kind = spec.kind
        if kind is TypeKind.BOOLEAN:
            combo = QComboBox(parent)
            combo.addItem(tr("true"), True)
            combo.addItem(tr("false"), False)
            if spec.nullable:
                combo.addItem("NULL", _NULL_CHOICE)
            return combo
        if kind is TypeKind.DATE and isinstance(value, date) and not isinstance(value, datetime):
            return self._calendar(QDateEdit(parent))
        if kind is TypeKind.DATETIME and _plain_datetime(value):
            editor = QDateTimeEdit(parent)
            editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            editor.setCalendarPopup(True)
            return editor
        if kind is TypeKind.TIME and isinstance(value, time) and value.microsecond == 0:
            editor_time = QTimeEdit(parent)
            editor_time.setDisplayFormat("HH:mm:ss")
            return editor_time
        line = QLineEdit(parent)
        if kind is TypeKind.INTEGER:
            line.setValidator(QRegularExpressionValidator(_INTEGER, line))
        elif kind in (TypeKind.DECIMAL, TypeKind.FLOAT):
            line.setValidator(QRegularExpressionValidator(_NUMBER, line))
        hints = {
            TypeKind.DATE: "YYYY-MM-DD",
            TypeKind.TIME: "HH:MM:SS",
            TypeKind.DATETIME: "YYYY-MM-DD HH:MM:SS",
            TypeKind.UUID: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        }
        if kind in hints:
            line.setPlaceholderText(hints[kind])
        return line

    @staticmethod
    def _calendar(editor: QDateEdit) -> QDateEdit:
        editor.setDisplayFormat("yyyy-MM-dd")
        editor.setCalendarPopup(True)
        return editor

    def setEditorData(self, editor: QWidget, index: _Index) -> None:
        model = index.model()
        value = index.data(0x0002)
        if isinstance(editor, QComboBox):
            wanted: Any = _NULL_CHOICE if value is None else bool(value)
            position = editor.findData(wanted)
            editor.setCurrentIndex(max(position, 0))
        elif isinstance(editor, QDateEdit):  # QDateEdit and QTimeEdit are QDateTimeEdits too
            editor.setDate(QDate(value.year, value.month, value.day))
        elif isinstance(editor, QTimeEdit):
            editor.setTime(QTime(value.hour, value.minute, value.second))
        elif isinstance(editor, QDateTimeEdit):
            editor.setDateTime(_to_qdatetime(value))
        elif isinstance(editor, QLineEdit):
            spec = model.column_spec(index.column()) if isinstance(model, EditableModel) else None
            editor.setText(edit_text(spec.kind, value) if spec is not None else str(value or ""))
            editor.selectAll()

    def setModelData(self, editor: QWidget, model: Any, index: _Index) -> None:
        if not isinstance(model, EditableModel):
            return
        row, column = index.row(), index.column()
        if isinstance(editor, QComboBox):
            choice = editor.currentData()
            model.edit_cell(row, column, None if choice is _NULL_CHOICE else bool(choice))
        elif isinstance(editor, QDateEdit):
            model.edit_cell(row, column, editor.date().toPython())
        elif isinstance(editor, QTimeEdit):
            model.edit_cell(row, column, editor.time().toPython())
        elif isinstance(editor, QDateTimeEdit):
            model.edit_cell(row, column, editor.dateTime().toPython())
        elif isinstance(editor, QLineEdit):
            model.edit_cell_text(row, column, editor.text())

    def updateEditorGeometry(
        self, editor: QWidget, option: QStyleOptionViewItem, index: _Index
    ) -> None:
        editor.setGeometry(option.rect)


def _plain_datetime(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is None and value.microsecond == 0


def _to_qdatetime(value: datetime) -> QDateTime:
    return QDateTime(
        QDate(value.year, value.month, value.day),
        QTime(value.hour, value.minute, value.second),
    )
