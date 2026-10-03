"""Editing a result grid: the controller that connects model, bar, dialogs and the database.

One :class:`GridEditor` serves one grid (a table tab, or the result of a ``SELECT``). It decides
whether the result may be edited, keeps the :class:`ChangeSet` across reloads, answers the keys
(``F2``, ``Ctrl+N``, ``Del``, ``Ctrl+Z``, ``Alt+S``, ``Esc`` …), shows the review dialog and writes
the changes in one transaction through ``EditingContext.apply``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from ...core.db import ApplyResult, BoundStatement, QueryResult
from ...core.dialects import Dialect, TypeKind
from ...core.editing import (
    ChangeSet,
    EditKeyStore,
    EditTarget,
    PlannedStatement,
    ReadOnly,
    ReadOnlyReason,
    build_statements,
    edit_text,
    row_label,
)
from ...core.schema import DatabaseSchema
from ..i18n import tr
from .delegates import CellDelegate
from .dialogs import KeyDialog, PreviewDialog, TextDialog
from .edit_bar import EditBar
from .editable_model import EditableModel
from .grid import ResultGrid
from .model import ResultTableModel

#: ``resolve(chosen key columns or None) -> target`` decides what the displayed result is.
Resolver = Callable[[Sequence[str] | None], EditTarget | ReadOnly]

_LONG_TEXT = 120
_TEXT_KINDS = frozenset({TypeKind.TEXT, TypeKind.JSON, TypeKind.OTHER, TypeKind.ENUM})


@dataclass(slots=True)
class EditingContext:
    """What editing needs from the connection the grid belongs to."""

    connection_id: str
    connection_name: str
    dialect: Dialect
    schema: Callable[[], DatabaseSchema | None]
    #: Runs the statements in one transaction somewhere off the GUI thread.
    apply: Callable[[Sequence[BoundStatement]], Future[ApplyResult]]
    read_only: bool = False
    production: bool = False
    edit_keys: EditKeyStore | None = None


class GridEditor(QObject):
    #: The changes were written; the page should load its rows again.
    applied = Signal()
    #: Load the current rows but keep the pending changes (the page calls ``take_rebase``).
    reloadRequested = Signal()
    #: The grid got a new model (a result was shown, or the key columns were chosen).
    modelReplaced = Signal()
    #: ``(planned statements, ApplyResult or the exception)``, from a worker thread, queued.
    _finished = Signal(object, object)

    def __init__(
        self, grid: ResultGrid, context: EditingContext | None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent or grid)
        self.grid = grid
        self.context = context
        self.bar = EditBar()
        self.model: ResultTableModel | None = None
        self.changes: ChangeSet | None = None
        self.read_only: ReadOnly | None = None
        self.last_result: ApplyResult | None = None
        self._resolver: Resolver | None = None
        self._result: QueryResult | None = None
        self._applying = False
        self._rebase = False
        self._notice = ""
        grid.attach_editor(self)
        grid.setItemDelegate(CellDelegate(grid))
        self.bar.apply_button.clicked.connect(self.request_apply)
        self.bar.discard_button.clicked.connect(self.discard)
        self.bar.undo_button.clicked.connect(self.undo)
        self.bar.redo_button.clicked.connect(self.redo)
        self.bar.reload_button.clicked.connect(self.request_reload)
        self.bar.key_button.clicked.connect(self.choose_key)
        self._finished.connect(self._on_finished)

    # ------------------------------------------------------------------ showing a result

    @property
    def editable(self) -> bool:
        return isinstance(self.model, EditableModel)

    @property
    def pending(self) -> int:
        return self.changes.count if self.changes is not None else 0

    def show_result(self, result: QueryResult, resolver: Resolver, *, rebase: bool = False) -> None:
        """Show ``result``; ``resolver`` says whether (and as which table) it can be edited."""
        self._result = result
        self._resolver = resolver
        context = self.context
        if context is not None and context.read_only:
            target: EditTarget | ReadOnly = ReadOnly(ReadOnlyReason.CONNECTION)
        elif context is None:
            target = ReadOnly(ReadOnlyReason.CONNECTION)
        else:
            chosen = None
            target = resolver(chosen)
            if isinstance(target, ReadOnly) and target.reason is ReadOnlyReason.NO_KEY:
                chosen = self._stored_key(target)
                if chosen:
                    target = resolver(chosen)
        if isinstance(target, EditTarget):
            self.read_only = None
            if self.changes is None or not _same_target(self.changes.target, target):
                self.changes = ChangeSet(target)
            else:
                self.changes.target = target
                if rebase:
                    self.changes.rebase(target.values_of(row) for row in result.rows)
            editable = EditableModel(result, target, self.changes)
            editable.changed.connect(self._refresh_bar)
            editable.editRejected.connect(self._on_rejected)
            self.model = editable
        else:
            self.read_only = target
            self.changes = None
            self.model = ResultTableModel(result)
        self.grid.set_result_model(self.model)
        self._refresh_bar()
        self.modelReplaced.emit()

    def _stored_key(self, target: ReadOnly) -> tuple[str, ...] | None:
        context = self.context
        if context is None or context.edit_keys is None or target.table is None:
            return None
        return context.edit_keys.get(context.connection_id, target.table.key)

    def take_rebase(self) -> bool:
        """Should the next loaded rows become the baseline of the pending edits?"""
        rebase, self._rebase = self._rebase, False
        return rebase

    def request_reload(self) -> None:
        self._rebase = True
        self.reloadRequested.emit()

    # ------------------------------------------------------------------ the bar

    def _refresh_bar(self) -> None:
        bar = self.bar
        changes = self.changes
        if changes is None:
            self._show_read_only()
            return
        if changes.count:
            self._notice = ""
            bar.show_editable(
                tr(
                    "Pending changes: {n} · Alt+S apply · Esc discard",
                    n=changes.count,
                ),
                pending=True,
                can_undo=changes.can_undo,
                can_redo=changes.can_redo,
                reload=bool(bar.problem.text()) and self._conflict,
            )
            bar.status.setToolTip(self._pending_tooltip(changes))
            return
        text = self._notice or tr(
            "Editable · {table} (key: {key}) · F2 edit · Ctrl+N new row · Del delete",
            table=changes.target.table.name,
            key=", ".join(changes.target.key),
        )
        bar.show_editable(
            text,
            pending=False,
            can_undo=changes.can_undo,
            can_redo=changes.can_redo,
        )
        bar.status.setToolTip("")

    _conflict = False

    @staticmethod
    def _pending_tooltip(changes: ChangeSet) -> str:
        parts = []
        if changes.updates:
            parts.append(tr("rows to change: {n}", n=len(changes.updates)))
        if changes.inserts:
            parts.append(tr("rows to add: {n}", n=len(changes.inserts)))
        if changes.deletes:
            parts.append(tr("rows to delete: {n}", n=len(changes.deletes)))
        return " · ".join(parts)

    def _show_read_only(self) -> None:
        reason = self.read_only
        if reason is None:
            self.bar.show_read_only("")
            return
        name = reason.names[0] if reason.names else ""
        texts = {
            ReadOnlyReason.CONNECTION: tr("Read-only: this connection is read-only."),
            ReadOnlyReason.VIEW: tr("Read-only: {name} is a view.", name=name),
            ReadOnlyReason.NOT_A_QUERY: tr(
                "Read-only: only a plain SELECT from one table can be edited."
            ),
            ReadOnlyReason.COMPLEX: tr(
                "Read-only: this is not the rows of a single table (a join, grouping, DISTINCT, "
                "an aggregate or a subquery). Open the table to change its data."
            ),
            ReadOnlyReason.UNKNOWN_TABLE: tr(
                "Read-only: the structure of {name} is not known (yet).", name=name
            )
            if name
            else tr("Read-only: the database structure is not loaded yet."),
            ReadOnlyReason.NO_KEY: tr(
                "Read-only: {name} has no primary key and no unique index.", name=name
            ),
            ReadOnlyReason.KEY_NOT_SELECTED: tr(
                "Read-only: the key columns ({columns}) are not in the result; "
                "add them to the SELECT list.",
                columns=", ".join(reason.names),
            ),
            ReadOnlyReason.MISMATCH: tr("Read-only: the columns do not match {name}.", name=name),
        }
        self.bar.show_read_only(
            texts[reason.reason], key_button=reason.reason is ReadOnlyReason.NO_KEY
        )

    def _on_rejected(self, message: str) -> None:
        self.bar.set_problem(message)

    # ------------------------------------------------------------------ choosing a key

    def choose_key(self) -> bool:
        """Let the user pick the columns that identify a row (tables without a primary key)."""
        reason = self.read_only
        context = self.context
        if (
            reason is None
            or reason.table is None
            or context is None
            or context.edit_keys is None
            or self._resolver is None
            or self._result is None
        ):
            return False
        current = context.edit_keys.get(context.connection_id, reason.table.key) or ()
        dialog = KeyDialog(reason.table, current, self.grid.window())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        context.edit_keys.set(context.connection_id, reason.table.key, dialog.chosen())
        self.show_result(self._result, self._resolver)
        return True

    # ------------------------------------------------------------------ editing commands

    def _model(self) -> EditableModel | None:
        return self.model if isinstance(self.model, EditableModel) and not self._applying else None

    def _selected_rows(self) -> list[int]:
        selection = self.grid.selectionModel()
        rows = {i.row() for i in selection.selectedIndexes()} if selection is not None else set()
        if not rows and self.grid.currentIndex().isValid():
            rows.add(self.grid.currentIndex().row())
        return sorted(rows)

    def add_row(self) -> int | None:
        model = self._model()
        if model is None:
            return None
        row = model.add_row()
        self._focus_row(model, row)
        return row

    def duplicate_rows(self) -> list[int]:
        model = self._model()
        rows = self._selected_rows()
        if model is None or not rows:
            return []
        created = model.duplicate_rows(rows)
        if created:
            self._focus_row(model, created[0])
        return created

    def delete_rows(self) -> int:
        model = self._model()
        rows = self._selected_rows()
        if model is None or not rows:
            return 0
        return model.delete_rows(rows)

    def _focus_row(self, model: EditableModel, row: int) -> None:
        column = next((s.index for s in model.target.columns if s.editable and not s.key), 0)
        index = model.index(row, column)
        self.grid.setCurrentIndex(index)
        self.grid.scrollTo(index)

    def undo(self) -> bool:
        return self._history(lambda c: c.undo())

    def redo(self) -> bool:
        return self._history(lambda c: c.redo())

    def discard(self) -> bool:
        return self._history(lambda c: c.discard_all(), clear_errors=True)

    def _history(self, action: Callable[[ChangeSet], bool], *, clear_errors: bool = False) -> bool:
        model = self._model()
        if model is None or self.changes is None:
            return False
        done = action(self.changes)
        if done:
            if clear_errors:
                model.clear_errors()
            self.bar.set_problem("")
            self._conflict = False
            model.refresh()
        return done

    def set_null(self, index: QModelIndex) -> bool:
        model = self._model()
        return model is not None and model.set_null(index.row(), index.column())

    def edit_in_dialog(self, index: QModelIndex) -> bool:
        """Edit a long or multi-line text in a dialog (a cell editor is a single line)."""
        model = self._model()
        if model is None or not index.isValid():
            return False
        spec = model.column_spec(index.column())
        if (
            spec is None
            or not spec.editable
            or not (model.flags(index) & Qt.ItemFlag.ItemIsEditable)
        ):
            return False
        value = model.value(index.row(), index.column())
        dialog = TextDialog(
            tr("Edit {name}", name=spec.name),
            edit_text(spec.kind, value),
            self.grid.window(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return True
        model.edit_cell_text(index.row(), index.column(), dialog.text())
        return True

    def wants_dialog(self, index: QModelIndex) -> bool:
        """Is the value in ``index`` too long / multi-line for the inline editor?"""
        model = self._model()
        if model is None or not index.isValid():
            return False
        spec = model.column_spec(index.column())
        if spec is None or spec.kind not in _TEXT_KINDS:
            return False
        value = model.value(index.row(), index.column())
        return isinstance(value, str) and (
            "\n" in value or "\r" in value or len(value) > _LONG_TEXT
        )

    # ------------------------------------------------------------------ keys and menu

    def wants_key(self, key: int) -> bool:
        """Keys that must not reach a menu shortcut (``Esc`` stops a query otherwise)."""
        return key == Qt.Key.Key_Escape and self.pending > 0 and not self._applying

    def handle_key(self, event: QKeyEvent) -> bool:
        if self._model() is None:
            return False
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and key == Qt.Key.Key_Z and not shift:
            return self.undo() or True
        if ctrl and (key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift)):
            return self.redo() or True
        if ctrl and key == Qt.Key.Key_N:
            self.add_row()
            return True
        if ctrl and key == Qt.Key.Key_D:
            self.duplicate_rows()
            return True
        if alt and key == Qt.Key.Key_S:
            self.request_apply()
            return True
        if not ctrl and not alt and key == Qt.Key.Key_Delete:
            self.delete_rows()
            return True
        if key == Qt.Key.Key_Escape and self.pending:
            self.discard()
            return True
        return False

    def extend_menu(self, menu: QMenu, index: QModelIndex) -> None:
        """Add the editing actions for the cell under the mouse to the grid's context menu."""
        model = self._model()
        if model is None:
            return
        menu.addSeparator()
        valid = index.isValid()
        editable = valid and bool(model.flags(index) & Qt.ItemFlag.ItemIsEditable)
        spec = model.column_spec(index.column()) if valid else None
        row = index.row() if valid else -1

        def add(text: str, handler: Callable[[], Any], enabled: bool = True) -> QAction:
            action = menu.addAction(text)
            action.setEnabled(enabled)
            action.triggered.connect(lambda _checked=False: handler())
            return action

        add(tr("Edit cell") + "\tF2", lambda: self.grid.edit(index), editable)
        add(
            tr("Edit in a dialog…"),
            lambda: self.edit_in_dialog(index),
            editable and spec is not None and spec.kind in _TEXT_KINDS,
        )
        add(
            tr("Set NULL"),
            lambda: self.set_null(index),
            editable and spec is not None and spec.nullable,
        )
        add(
            tr("Use the default"),
            lambda: model.set_default(row, index.column()),
            valid and model.is_new(row) and spec is not None and spec.editable,
        )
        add(tr("Revert this cell"), lambda: model.revert_cell(row, index.column()), valid)
        menu.addSeparator()
        add(tr("New row") + "\tCtrl+N", self.add_row)
        add(tr("Duplicate row") + "\tCtrl+D", self.duplicate_rows, valid)
        key = model.row_key(row) if valid else None
        deleted = key is not None and model.changes.is_deleted(key)
        add(
            (tr("Restore row") if deleted else tr("Delete row")) + "\tDel",
            self.delete_rows,
            valid,
        )
        add(tr("Revert this row"), lambda: model.revert_row(row), valid)
        menu.addSeparator()
        add(tr("Undo") + "\tCtrl+Z", self.undo, self.changes is not None and self.changes.can_undo)
        add(tr("Redo") + "\tCtrl+Y", self.redo, self.changes is not None and self.changes.can_redo)
        add(tr("Apply changes…") + "\tAlt+S", self.request_apply, self.pending > 0)
        add(tr("Discard changes") + "\tEsc", self.discard, self.pending > 0)

    # ------------------------------------------------------------------ applying

    def planned(self) -> list[PlannedStatement]:
        context = self.context
        if context is None or self.changes is None:
            return []
        return build_statements(self.changes, context.dialect)

    def request_apply(self) -> bool:
        """Review the statements, confirm (production), then write them in one transaction."""
        context = self.context
        if self._applying or context is None or self.changes is None or self.changes.is_empty:
            return False
        planned = self.planned()
        dialog = PreviewDialog(
            planned,
            context.dialect,
            connection_name=context.connection_name,
            production=context.production,
            parent=self.grid.window(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if context.production and not self._confirm_production(len(planned)):
            return False
        self.run_apply(planned)
        return True

    def _confirm_production(self, count: int) -> bool:
        context = self.context
        assert context is not None
        answer = QMessageBox.question(
            self.grid.window(),
            tr("Change production data?"),
            tr(
                "This is the PRODUCTION connection “{name}”. {n} statements will change its data.",
                name=context.connection_name,
                n=count,
            )
            + "\n\n"
            + tr("Apply them?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def run_apply(self, planned: Sequence[PlannedStatement]) -> None:
        """Write ``planned`` now (no review); the outcome arrives through :meth:`_on_finished`."""
        context = self.context
        model = self._model()
        if context is None or model is None:
            return
        self._applying = True
        self._conflict = False
        self.bar.set_busy(True)
        self.bar.set_problem("")
        model.clear_errors()
        try:
            future = context.apply([p.statement for p in planned])
        except Exception as error:  # the connection is gone
            self._finished.emit(tuple(planned), error)
            return
        future.add_done_callback(lambda done: self._deliver(tuple(planned), done))

    def _deliver(self, planned: tuple[PlannedStatement, ...], future: Future[ApplyResult]) -> None:
        try:
            outcome: object = future.result()
        except Exception as error:  # includes a lost connection and a cancelled future
            outcome = error
        with contextlib.suppress(RuntimeError):  # the widget was deleted meanwhile
            self._finished.emit(planned, outcome)

    def _on_finished(self, planned: tuple[PlannedStatement, ...], outcome: object) -> None:
        self._applying = False
        self.bar.set_busy(False)
        model = self.model
        if isinstance(outcome, ApplyResult):
            self.last_result = outcome
            if outcome.ok:
                self._after_success(planned, outcome)
            else:
                self._after_failure(planned, outcome)
            return
        self.bar.set_problem(str(outcome))
        if isinstance(model, EditableModel):
            model.refresh()

    def _after_success(self, planned: tuple[PlannedStatement, ...], result: ApplyResult) -> None:
        if self.changes is not None:
            self.changes.reset()
        self._conflict = False
        self._notice = tr(
            "Applied {n} changes in {time}.",
            n=len(planned),
            time=f"{result.duration * 1000:.0f} ms",
        )
        self.bar.set_problem("")
        if isinstance(self.model, EditableModel):
            self.model.refresh()
        self.applied.emit()

    def _after_failure(self, planned: tuple[PlannedStatement, ...], result: ApplyResult) -> None:
        model = self.model
        target = self.changes.target if self.changes is not None else None
        failing = planned[result.failed] if result.failed is not None else None
        label = ""
        if failing is not None and target is not None and not isinstance(failing.subject, int):
            label = row_label(target, failing.subject) + ": "
        if result.conflict:
            message = tr(
                "{row}was changed or deleted by someone else since it was loaded. "
                "Nothing was written. Reload to see the current rows; your changes are kept.",
                row=label,
            )
        elif result.matched is not None and result.matched > 1:
            message = tr(
                "{row}the key matches {n} rows, so the change was refused. Nothing was written. "
                "Choose key columns that are unique.",
                row=label,
                n=result.matched,
            )
        else:
            message = label + (result.error or tr("The change could not be written."))
            message += "\n" + tr("Nothing was written.")
        self._conflict = result.conflict
        self.bar.set_problem(message)
        if isinstance(model, EditableModel) and failing is not None:
            model.set_error(failing.subject, failing.columns, message)
            row = (
                model.row_of_new(failing.subject)
                if isinstance(failing.subject, int)
                else model.row_of_key(failing.subject)
            )
            if row is not None:
                self.grid.scrollTo(model.index(row, 0))
        self._refresh_bar()


def _same_target(left: EditTarget, right: EditTarget) -> bool:
    return (
        left.table.key == right.table.key
        and left.key == right.key
        and [c.name for c in left.columns] == [c.name for c in right.columns]
    )
