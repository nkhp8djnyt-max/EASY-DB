"""The result model with pending edits laid over it: colours, new rows, deleted rows, errors.

The loaded rows are never modified. What the grid shows is the loaded value unless the change set
holds an edit for that cell; new rows are listed after the loaded ones. All changes go through the
:class:`~easydbms.core.editing.ChangeSet`, which is what undo / redo and "apply" work on.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont

from ...core.db import QueryResult
from ...core.editing import (
    ChangeSet,
    EditTarget,
    RowKey,
    TargetColumn,
    ValueParseError,
    parse_value,
)
from ..i18n import tr
from ..theme import current_tokens
from .model import ResultTableModel, format_cell

_Index = QModelIndex | QPersistentModelIndex
_ROOT = QModelIndex()

#: ``Qt.UserRole + 1``: the original (loaded) value of a cell, for tooltips.
ORIGINAL_ROLE = Qt.ItemDataRole.UserRole + 1
#: ``Qt.UserRole + 2``: ``True`` for a cell that was edited, is new or is marked for deletion.
CHANGED_ROLE = Qt.ItemDataRole.UserRole + 2

_ALPHA = 70


def _tint(color: str, alpha: int = _ALPHA) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(alpha)
    return tinted


class EditableModel(ResultTableModel):
    #: Pending changes (or how they are shown) changed: the status bar should be refreshed.
    changed = Signal()
    #: An edit was refused; the argument says why (shown in the status bar).
    editRejected = Signal(str)

    def __init__(
        self, result: QueryResult, target: EditTarget, changes: ChangeSet, parent: Any = None
    ) -> None:
        super().__init__(result, parent)
        self.target = target
        self.changes = changes
        self._keys: list[RowKey | None] = [target.key_of(row) for row in result.rows]
        self._new_ids: list[int] = list(changes.inserts)
        #: ``subject (row key or new id) -> (columns, message)`` of failed statements.
        self._errors: dict[RowKey | int, tuple[frozenset[str], str]] = {}

    # ------------------------------------------------------------------ rows

    def _loaded_rows(self) -> int:
        return super().rowCount()

    def is_new(self, row: int) -> bool:
        return row >= self._loaded_rows()

    def new_id(self, row: int) -> int | None:
        position = row - self._loaded_rows()
        return self._new_ids[position] if 0 <= position < len(self._new_ids) else None

    def row_key(self, row: int) -> RowKey | None:
        """The key a loaded row was loaded with (``None`` for new rows and rows without a key)."""
        if self.is_new(row):
            return None
        return self._keys[self.source_row(row)]

    def row_of_key(self, key: RowKey) -> int | None:
        for row in range(self._loaded_rows()):
            if self._keys[self.source_row(row)] == key:
                return row
        return None

    def row_of_new(self, new_id: int) -> int | None:
        if new_id in self._new_ids:
            return self._loaded_rows() + self._new_ids.index(new_id)
        return None

    def original_values(self, row: int) -> dict[str, Any]:
        """The loaded values of a loaded row by column name."""
        return self.target.values_of(self.result.rows[self.source_row(row)])

    def column_spec(self, column: int) -> TargetColumn | None:
        return self.target.by_index(column)

    def rowCount(self, parent: _Index = _ROOT) -> int:
        if parent.isValid():
            return 0
        return self._loaded_rows() + len(self._new_ids)

    # ------------------------------------------------------------------ values

    def value(self, row: int, column: int) -> Any:
        """The value shown in a cell: the pending one when there is one."""
        return self.cell_value(row, column)[0]

    def cell_value(self, row: int, column: int) -> tuple[Any, bool]:
        """``(value, is_pending)``; a never-set cell of a new row is ``(None, False)``."""
        spec = self.column_spec(column)
        if self.is_new(row):
            new_id = self.new_id(row)
            insert = self.changes.inserts.get(new_id) if new_id is not None else None
            if spec is not None and insert is not None and spec.name in insert.values:
                return insert.values[spec.name], True
            return None, False
        loaded = self.result.rows[self.source_row(row)][column]
        key = self.row_key(row)
        if spec is None or key is None:
            return loaded, False
        edited, value = self.changes.edited_value(key, spec.name)
        return (value, True) if edited else (loaded, False)

    def is_default(self, row: int, column: int) -> bool:
        """Is this cell of a new row left to the database default?"""
        if not self.is_new(row):
            return False
        spec = self.column_spec(column)
        return spec is not None and not self.cell_value(row, column)[1]

    # ------------------------------------------------------------------ Qt API

    def flags(self, index: _Index) -> Qt.ItemFlag:
        flags = super().flags(index)
        if not index.isValid():
            return flags
        spec = self.column_spec(index.column())
        if spec is None or not spec.editable:
            return flags
        if self.is_new(index.row()):
            return flags | Qt.ItemFlag.ItemIsEditable
        key = self.row_key(index.row())
        if key is None or self.changes.is_deleted(key):
            return flags
        return flags | Qt.ItemFlag.ItemIsEditable

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        value, pending = self.cell_value(row, column)
        state = self._state(row, column, pending)
        if role == Qt.ItemDataRole.DisplayRole:
            if self.is_default(row, column):
                return self._default_text(column)
            return "NULL" if value is None else format_cell(value)
        if role == Qt.ItemDataRole.EditRole:
            return value
        if role == ORIGINAL_ROLE:
            return None if self.is_new(row) else self.result.rows[self.source_row(row)][column]
        if role == CHANGED_ROLE:
            return state != "plain"
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._background(state)
        if role == Qt.ItemDataRole.ForegroundRole:
            if self.is_default(row, column) or (value is None and state != "error"):
                return QColor(current_tokens().text_muted)
            return None
        if role == Qt.ItemDataRole.FontRole:
            return self._font(row, column, state)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(row, column, state, value)
        return super().data(index, role)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if (
            orientation == Qt.Orientation.Vertical
            and role == Qt.ItemDataRole.DisplayRole
            and self.is_new(section)
        ):
            return "+"
        return super().headerData(section, orientation, role)

    def setData(self, index: _Index, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        return self.edit_cell(index.row(), index.column(), value)

    # ------------------------------------------------------------------ display helpers

    def _state(self, row: int, column: int, pending: bool) -> str:
        if self._has_error(row, column):
            return "error"
        if self.is_new(row):
            return "new"
        key = self.row_key(row)
        if key is not None and self.changes.is_deleted(key):
            return "deleted"
        return "edited" if pending else "plain"

    def _has_error(self, row: int, column: int) -> bool:
        subject = self.new_id(row) if self.is_new(row) else self.row_key(row)
        entry = self._errors.get(subject) if subject is not None else None
        if entry is None:
            return False
        columns, _ = entry
        spec = self.column_spec(column)
        return not columns or (spec is not None and spec.name in columns)

    @staticmethod
    def _background(state: str) -> QColor | None:
        tokens = current_tokens()
        return {
            "edited": _tint(tokens.warning),
            "new": _tint(tokens.success),
            "deleted": _tint(tokens.danger),
            "error": _tint(tokens.danger, 150),
        }.get(state)

    def _font(self, row: int, column: int, state: str) -> QFont | None:
        font = QFont()
        if state == "deleted":
            font.setStrikeOut(True)
            return font
        if self.is_default(row, column):
            font.setItalic(True)
            return font
        return None

    def _default_text(self, column: int) -> str:
        spec = self.column_spec(column)
        if spec is not None and not spec.nullable and not spec.has_default and not spec.key:
            return tr("required")
        if spec is not None and (spec.has_default or spec.key):
            return tr("default")
        return "NULL"

    def _tooltip(self, row: int, column: int, state: str, value: Any) -> str | None:
        subject = self.new_id(row) if self.is_new(row) else self.row_key(row)
        entry = self._errors.get(subject) if subject is not None else None
        if state == "error" and entry is not None:
            return entry[1]
        if state == "edited":
            loaded = self.result.rows[self.source_row(row)][column]
            was = "NULL" if loaded is None else format_cell(loaded)
            return tr("was: {value}", value=was)
        if state == "deleted":
            return tr("This row will be deleted.")
        if state == "new":
            return tr("This row will be inserted.")
        if value is None:
            return None
        shown = super().data(self.index(row, column), Qt.ItemDataRole.ToolTipRole)
        return shown if isinstance(shown, str) else None

    # ------------------------------------------------------------------ editing

    def edit_cell(self, row: int, column: int, value: Any) -> bool:
        """Set a cell to ``value`` (``None`` is NULL); ``False`` when nothing was changed."""
        spec = self.column_spec(column)
        if spec is None or not spec.editable:
            return False
        if value is None and not spec.nullable:
            self.editRejected.emit(tr("The column {name} does not accept NULL.", name=spec.name))
            return False
        if self.is_new(row):
            new_id = self.new_id(row)
            done = new_id is not None and self.changes.set_new_cell(new_id, spec.name, value)
        else:
            key = self.row_key(row)
            done = key is not None and self.changes.set_cell(
                key, self.original_values(row), spec.name, value
            )
        if done:
            self._clear_error(row)
            self.refresh()
        return done

    def edit_cell_text(self, row: int, column: int, text: str) -> bool:
        """Set a cell from typed text; text that is not a valid value is refused with a message."""
        spec = self.column_spec(column)
        if spec is None:
            return False
        try:
            value = parse_value(spec.kind, text)
        except ValueParseError as error:
            self.editRejected.emit(_parse_message(spec.name, error))
            return False
        return self.edit_cell(row, column, value)

    def set_null(self, row: int, column: int) -> bool:
        return self.edit_cell(row, column, None)

    def set_default(self, row: int, column: int) -> bool:
        """Back to the database default (new rows only)."""
        spec = self.column_spec(column)
        new_id = self.new_id(row)
        if spec is None or new_id is None:
            return False
        done = self.changes.unset_new_cell(new_id, spec.name)
        if done:
            self.refresh()
        return done

    def add_row(self, values: Mapping[str, Any] | None = None) -> int:
        """Append a new row; returns its row number in the model."""
        self.changes.add_row(values)
        self.refresh()
        return self.rowCount() - 1

    def duplicate_rows(self, rows: Iterable[int]) -> list[int]:
        """New rows with the values of ``rows`` except their key columns (left to the default)."""
        created: list[int] = []
        keys = set(self.target.key)
        with self.changes.group():
            for row in sorted(set(rows)):
                values = {
                    spec.name: self.value(row, spec.index)
                    for spec in self.target.columns
                    if spec.editable and spec.name not in keys
                }
                self.changes.add_row(values)
        self.refresh()
        count = self.rowCount()
        total = len(set(rows))
        created.extend(range(count - total, count))
        return created

    def delete_rows(self, rows: Collection[int]) -> int:
        """Mark loaded rows for deletion (unmark them if all are marked already); drop new rows.

        Returns how many rows were affected.
        """
        loaded = [
            r for r in sorted(set(rows)) if not self.is_new(r) and self.row_key(r) is not None
        ]
        fresh = [r for r in set(rows) if self.is_new(r)]
        if not loaded and not fresh:
            return 0
        keys = [key for r in loaded if (key := self.row_key(r)) is not None]
        restore = bool(keys) and not fresh and all(self.changes.is_deleted(k) for k in keys)
        with self.changes.group():
            for row in loaded:
                key = self.row_key(row)
                assert key is not None
                if restore:
                    self.changes.restore_row(key)
                else:
                    self.changes.delete_row(key, self.original_values(row))
            for new_id in [self.new_id(r) for r in fresh]:
                if new_id is not None:
                    self.changes.remove_new(new_id)
        self.refresh()
        return len(loaded) + len(fresh)

    def revert_cell(self, row: int, column: int) -> bool:
        spec = self.column_spec(column)
        if spec is None:
            return False
        if self.is_new(row):
            return self.set_default(row, column)
        key = self.row_key(row)
        done = key is not None and self.changes.revert_cell(key, spec.name)
        if done:
            self._clear_error(row)
            self.refresh()
        return done

    def revert_row(self, row: int) -> bool:
        if self.is_new(row):
            new_id = self.new_id(row)
            done = new_id is not None and self.changes.remove_new(new_id)
        else:
            key = self.row_key(row)
            done = key is not None and self.changes.revert_row(key)
        if done:
            self.refresh()
        return done

    # ------------------------------------------------------------------ errors

    def set_error(self, subject: RowKey | int, columns: Collection[str], message: str) -> None:
        self._errors[subject] = (frozenset(columns), message)
        self._repaint()

    def clear_errors(self) -> None:
        if self._errors:
            self._errors.clear()
            self._repaint()

    def _clear_error(self, row: int) -> None:
        subject = self.new_id(row) if self.is_new(row) else self.row_key(row)
        if subject is not None and self._errors.pop(subject, None) is not None:
            self._repaint()

    def error_rows(self) -> list[int]:
        rows = []
        for subject in self._errors:
            row = self.row_of_new(subject) if isinstance(subject, int) else self.row_of_key(subject)
            if row is not None:
                rows.append(row)
        return sorted(rows)

    # ------------------------------------------------------------------ keeping in step

    def refresh(self) -> None:
        """Re-read the change set (after an edit, undo, redo, discard)."""
        wanted = list(self.changes.inserts)
        old = self._new_ids
        loaded = self._loaded_rows()
        if wanted != old:
            if wanted[: len(old)] == old:  # rows appended
                self.beginInsertRows(_ROOT, loaded + len(old), loaded + len(wanted) - 1)
                self._new_ids = wanted
                self.endInsertRows()
            elif old[: len(wanted)] == wanted:  # rows removed from the end
                self.beginRemoveRows(_ROOT, loaded + len(wanted), loaded + len(old) - 1)
                self._new_ids = wanted
                self.endRemoveRows()
            else:
                self.beginResetModel()
                self._new_ids = wanted
                self.endResetModel()
        self._repaint()
        self.changed.emit()

    def _repaint(self) -> None:
        rows, columns = self.rowCount(), self.columnCount()
        if rows and columns:
            self.dataChanged.emit(self.index(0, 0), self.index(rows - 1, columns - 1))


def _parse_message(column: str, error: ValueParseError) -> str:
    from ...core.dialects import TypeKind

    expected = {
        TypeKind.INTEGER: tr("a whole number"),
        TypeKind.DECIMAL: tr("a number"),
        TypeKind.FLOAT: tr("a number"),
        TypeKind.BOOLEAN: tr("true or false"),
        TypeKind.DATE: tr("a date (YYYY-MM-DD)"),
        TypeKind.TIME: tr("a time (HH:MM:SS)"),
        TypeKind.DATETIME: tr("a date and time (YYYY-MM-DD HH:MM:SS)"),
        TypeKind.JSON: tr("valid JSON"),
        TypeKind.UUID: tr("a UUID"),
    }.get(error.kind, tr("a valid value"))
    return tr("{name}: expected {what}.", name=column, what=expected)
