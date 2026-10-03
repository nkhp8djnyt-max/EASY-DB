"""Editing rows in the grid: from typing in a cell to the change being in the database."""

from __future__ import annotations

import sqlite3
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemDelegate, QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from easydbms.core.connections import FileConnection
from easydbms.ui.results import TableTab
from easydbms.ui.results.editable_model import CHANGED_ROLE, EditableModel
from easydbms.ui.workspace import QueryWorkspace

from .conftest import Env, Prompts, add_shop

K = Qt.Key


def open_workspace(
    env: Env,
    qtbot: QtBot,
    config: FileConnection | None = None,
    page_size: int = 100,
    **extra: Any,
) -> QueryWorkspace:
    config = config or add_shop(env, "Shop", rows=5)
    session = env.services.manager.activate(config.id).result(timeout=10)
    qtbot.waitUntil(lambda: session.schema is not None, timeout=10000)
    workspace = QueryWorkspace(
        config,
        env.services.tab_store,
        lambda: page_size,
        edit_keys=env.services.edit_keys,
        **extra,
    )
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.resize(1000, 700)
    workspace.show()
    return workspace


def open_table(
    workspace: QueryWorkspace, qtbot: QtBot, name: str = "book", ready: bool = True
) -> TableTab:
    session = workspace._session
    assert session is not None
    assert session.schema is not None
    table = session.schema.find(name)
    assert table is not None
    tab = workspace.open_table(table)
    assert tab is not None
    if ready:
        wait_loaded(tab, qtbot)
    return tab


def wait_loaded(tab: TableTab, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not tab._loading and tab.grid.model() is not None, timeout=10000)


def model_of(tab: TableTab) -> EditableModel:
    model = tab.grid.model()
    assert isinstance(model, EditableModel), model
    return model


def db_rows(env: Env, config: FileConnection, sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(config.path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def press(
    widget: Any, key: K, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
) -> None:
    QTest.keyClick(widget, key, modifiers)


def commit(tab: TableTab, editor: Any) -> None:
    """What the delegate does when Enter is pressed in a cell editor (the offscreen platform does
    not route the key to the delegate's event filter, so the signals are sent directly)."""
    delegate = tab.grid.itemDelegate()
    delegate.commitData.emit(editor)
    delegate.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)


def cancel(tab: TableTab, editor: Any) -> None:
    tab.grid.itemDelegate().closeEditor.emit(
        editor, QAbstractItemDelegate.EndEditHint.RevertModelCache
    )


def select_row(tab: TableTab, row: int, column: int = 0) -> None:
    tab.grid.setCurrentIndex(model_of(tab).index(row, column))
    tab.grid.selectRow(row)


def cell(tab: TableTab, row: int, column: str) -> Any:
    model = model_of(tab)
    return model.value(row, model.result.columns.index(column))


def edit_text(tab: TableTab, row: int, column: str, text: str) -> bool:
    model = model_of(tab)
    return model.edit_cell_text(row, model.result.columns.index(column), text)


def apply_all(tab: TableTab, qtbot: QtBot, prompts: Prompts) -> None:
    tab.editor.request_apply()
    qtbot.waitUntil(lambda: not tab.editor._applying, timeout=10000)
    qtbot.waitUntil(lambda: not tab._loading, timeout=10000)


# ---------------------------------------------------------------------------- what is editable


def test_a_table_tab_with_a_primary_key_is_editable(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    assert tab.editor.editable
    assert tab.editor.read_only is None
    assert "Editable" in tab.editor.bar.status.text()
    assert "key: id" in tab.editor.bar.status.text()
    assert tab.grid.editTriggers() & tab.grid.EditTrigger.DoubleClicked


def test_nothing_is_pending_at_first(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    assert tab.pending == 0
    assert not tab.editor.bar.apply_button.isVisibleTo(tab)
    assert not tab.editor.bar.discard_button.isVisibleTo(tab)


def test_a_view_is_read_only_and_says_so(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot, "author_names")
    assert not tab.editor.editable
    assert "is a view" in tab.editor.bar.status.text()
    assert not tab.editor.bar.apply_button.isVisibleTo(tab)


def test_a_read_only_connection_cannot_be_edited(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Shop", rows=3)
    config = config.model_copy(update={"read_only": True})
    env.services.store.save(config)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    assert not tab.editor.editable
    assert "this connection is read-only" in tab.editor.bar.status.text()


def test_a_table_without_a_key_offers_to_choose_one(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=0)
    con = sqlite3.connect(config.path)
    con.executescript(
        "CREATE TABLE stock (sku TEXT, qty INTEGER); INSERT INTO stock VALUES ('a', 1), ('b', 2);"
    )
    con.close()
    workspace = open_workspace(env, qtbot, config)
    tab = open_table(workspace, qtbot, "stock")
    assert not tab.editor.editable
    assert "no primary key" in tab.editor.bar.status.text()
    assert tab.editor.bar.key_button.isVisibleTo(tab)
    prompts.key_choice = ("sku",)
    assert tab.editor.choose_key()
    assert tab.editor.editable
    assert "key: sku" in tab.editor.bar.status.text()
    assert env.services.edit_keys.get(config.id, tab.table.key) == ("sku",)
    # remembered: a freshly opened tab is editable at once
    workspace.close_tab(workspace.tabs.currentIndex())
    again = open_table(open_workspace(env, qtbot, config), qtbot, "stock")
    assert again.editor.editable


def test_cancelling_the_key_dialog_changes_nothing(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=0)
    con = sqlite3.connect(config.path)
    con.executescript("CREATE TABLE stock (sku TEXT, qty INTEGER);")
    con.close()
    tab = open_table(open_workspace(env, qtbot, config), qtbot, "stock")
    assert not tab.editor.choose_key()
    assert not tab.editor.editable
    assert env.services.edit_keys.get(config.id, tab.table.key) is None


# ---------------------------------------------------------------------------- editing a cell


def test_typing_in_a_cell_marks_it_and_counts_the_change(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    model = model_of(tab)
    column = model.result.columns.index("title")
    assert edit_text(tab, 0, "title", "A new title")
    index = model.index(0, column)
    assert index.data() == "A new title"
    assert index.data(CHANGED_ROLE) is True
    assert model.index(1, column).data(CHANGED_ROLE) is False
    assert index.data(Qt.ItemDataRole.BackgroundRole).alpha() > 0
    assert "was: Book 001" in index.data(Qt.ItemDataRole.ToolTipRole)
    assert tab.pending == 1
    assert "Pending changes: 1" in tab.editor.bar.status.text()
    assert "Alt+S" in tab.editor.bar.status.text()
    assert "Esc" in tab.editor.bar.status.text()
    assert tab.editor.bar.apply_button.isVisibleTo(tab)


def test_the_cell_editor_is_a_line_edit_that_writes_back_on_enter(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    model = model_of(tab)
    index = model.index(0, model.result.columns.index("title"))
    tab.grid.setCurrentIndex(index)
    tab.grid.edit(index)
    editor = tab.grid.viewport().findChild(QLineEdit)
    assert editor is not None
    assert editor.text() == "Book 001"
    editor.setText("Typed")
    commit(tab, editor)
    assert index.data() == "Typed"
    assert tab.pending == 1


def test_escape_while_editing_a_cell_closes_the_editor_only(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    edit_text(tab, 1, "title", "pending")
    model = model_of(tab)
    index = model.index(0, model.result.columns.index("title"))
    tab.grid.setCurrentIndex(index)
    tab.grid.edit(index)
    editor = tab.grid.viewport().findChild(QLineEdit)
    assert editor is not None
    editor.setText("never committed")
    cancel(tab, editor)
    assert index.data() == "Book 001"
    assert tab.pending == 1  # the other change is still there


def test_editing_back_to_the_original_clears_the_mark(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    edit_text(tab, 0, "title", "x")
    edit_text(tab, 0, "title", "Book 001")
    assert tab.pending == 0
    assert "Editable" in tab.editor.bar.status.text()


def test_a_value_that_does_not_fit_the_type_is_refused(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    assert not edit_text(tab, 0, "published", "soon")
    assert tab.pending == 0
    assert "whole number" in tab.editor.bar.problem.text()
    assert tab.editor.bar.problem.isVisibleTo(tab)
    assert edit_text(tab, 0, "published", "2001")


def test_null_is_refused_for_a_required_column(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot, add_shop(env, "Shop2", rows=0)), qtbot, "author")
    model = model_of(tab)
    assert not model.set_null(0, model.result.columns.index("name"))
    assert "does not accept NULL" in tab.editor.bar.problem.text()
    assert model.set_null(0, model.result.columns.index("email"))
    assert tab.pending == 1


def test_an_empty_number_field_means_null(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    assert edit_text(tab, 0, "published", "")
    assert cell(tab, 0, "published") is None
    assert model_of(tab).index(0, 3).data() == "NULL"


def test_binary_and_unknown_columns_have_no_editor(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    model = model_of(tab)
    assert model.flags(model.index(0, 1)) & Qt.ItemFlag.ItemIsEditable
    assert not model.flags(model.index(0, 99)) & Qt.ItemFlag.ItemIsEditable


# ---------------------------------------------------------------------------- undo, redo, discard


def test_undo_redo_and_discard_with_the_keyboard(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.grid.setFocus()
    edit_text(tab, 0, "title", "A")
    edit_text(tab, 1, "title", "B")
    press(tab.grid, K.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert tab.pending == 1
    assert cell(tab, 1, "title") == "Book 002"
    press(tab.grid, K.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert tab.pending == 2
    press(
        tab.grid, K.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    )
    assert tab.pending == 2  # Ctrl+Shift+Z is redo too: nothing left to redo
    press(tab.grid, K.Key_Escape)
    assert tab.pending == 0
    assert cell(tab, 0, "title") == "Book 001"
    press(tab.grid, K.Key_Z, Qt.KeyboardModifier.ControlModifier)  # discarding is undoable
    assert tab.pending == 2


def test_the_bar_buttons_do_the_same(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    bar = tab.editor.bar
    edit_text(tab, 0, "title", "A")
    assert bar.undo_button.isEnabled()
    assert not bar.redo_button.isEnabled()
    bar.undo_button.click()
    assert tab.pending == 0
    assert bar.redo_button.isEnabled()
    bar.redo_button.click()
    assert tab.pending == 1
    bar.discard_button.click()
    assert tab.pending == 0


def test_escape_is_taken_from_the_stop_shortcut_only_while_changes_are_pending(
    env: Env, qtbot: QtBot
) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    event = QKeyEvent(QEvent.Type.ShortcutOverride, K.Key_Escape, Qt.KeyboardModifier.NoModifier)
    tab.grid.event(event)
    assert not event.isAccepted()
    edit_text(tab, 0, "title", "A")
    event = QKeyEvent(QEvent.Type.ShortcutOverride, K.Key_Escape, Qt.KeyboardModifier.NoModifier)
    tab.grid.event(event)
    assert event.isAccepted()


# ------------------------------------------------------------------ rows: add, duplicate, delete


def test_ctrl_n_adds_a_row_shown_in_green_with_defaults(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.grid.setFocus()
    press(tab.grid, K.Key_N, Qt.KeyboardModifier.ControlModifier)
    model = model_of(tab)
    assert model.rowCount() == 6
    assert model.is_new(5)
    assert model.headerData(5, Qt.Orientation.Vertical) == "+"
    assert model.index(5, 0).data(Qt.ItemDataRole.BackgroundRole).green() > 100
    assert model.index(5, 0).data() == "default"  # the key column: the database picks it
    assert tab.pending == 1
    assert tab.grid.currentIndex().row() == 5


def test_a_new_row_is_filled_in_cell_by_cell(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    row = tab.editor.add_row()
    assert row == 5
    assert edit_text(tab, row, "author_id", "1")
    assert edit_text(tab, row, "title", "Fresh")
    assert cell(tab, row, "title") == "Fresh"
    assert tab.pending == 1  # one row, however many cells
    model = model_of(tab)
    model.set_default(row, model.result.columns.index("title"))
    assert model.index(row, model.result.columns.index("title")).data() in (
        "NULL",
        "required",
        "default",
    )
    assert tab.pending == 1


def test_the_plus_button_adds_a_row(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.add_button.click()
    assert model_of(tab).rowCount() == 6


def test_deleting_marks_rows_red_and_del_again_restores_them(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.grid.setFocus()
    select_row(tab, 1)
    press(tab.grid, K.Key_Delete)
    model = model_of(tab)
    assert model.index(1, 1).data(Qt.ItemDataRole.BackgroundRole).red() > 150
    assert model.index(1, 1).data(Qt.ItemDataRole.FontRole).strikeOut()
    assert not model.flags(model.index(1, 1)) & Qt.ItemFlag.ItemIsEditable
    assert tab.pending == 1
    press(tab.grid, K.Key_Delete)
    assert tab.pending == 0
    assert model.flags(model.index(1, 1)) & Qt.ItemFlag.ItemIsEditable


def test_deleting_several_selected_rows_is_one_undo_step(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.grid.setFocus()
    tab.grid.selectRow(0)
    tab.grid.selectRow(2)
    tab.grid.selectionModel().select(
        model_of(tab).index(0, 0),
        tab.grid.selectionModel().SelectionFlag.Select
        | tab.grid.selectionModel().SelectionFlag.Rows,
    )
    count = tab.editor.delete_rows()
    assert count == 2
    assert tab.pending == 2
    tab.editor.undo()
    assert tab.pending == 0


def test_deleting_a_new_row_just_removes_it(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.editor.add_row()
    select_row(tab, 5)
    assert tab.editor.delete_rows() == 1
    assert model_of(tab).rowCount() == 5
    assert tab.pending == 0


def test_a_deleted_row_loses_its_edits(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    edit_text(tab, 1, "title", "edited")
    select_row(tab, 1)
    tab.editor.delete_rows()
    assert tab.pending == 1
    assert cell(tab, 1, "title") == "Book 002"


def test_ctrl_d_duplicates_a_row_without_its_key(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    select_row(tab, 2)
    created = tab.editor.duplicate_rows()
    assert created == [5]
    assert cell(tab, 5, "title") == "Book 003"
    assert cell(tab, 5, "id") is None
    assert model_of(tab).index(5, 0).data() == "default"
    assert tab.pending == 1


def test_a_row_is_added_and_removed_by_undo_and_redo(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    tab.editor.add_row()
    tab.editor.add_row()
    assert model_of(tab).rowCount() == 7
    tab.editor.undo()
    assert model_of(tab).rowCount() == 6
    tab.editor.undo()
    assert model_of(tab).rowCount() == 5
    tab.editor.redo()
    assert model_of(tab).rowCount() == 6


# ------------------------------------------------------------------ reviewing and applying


def test_alt_s_shows_the_sql_and_applying_writes_it(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=5)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "title", "Edited title")
    tab.grid.setFocus()
    press(tab.grid, K.Key_S, Qt.KeyboardModifier.AltModifier)
    qtbot.waitUntil(lambda: not tab.editor._applying and tab.pending == 0, timeout=10000)
    (dialog,) = prompts.previews
    sql = dialog.sql.toPlainText()
    assert sql.startswith('UPDATE "main"."book" SET "title" = \'Edited title\' WHERE "id" = 1')
    assert "AND \"title\" = 'Book 001'" in sql  # the old value guards against concurrent changes
    assert "rows to change: 1" in dialog.summary.text()
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 1") == [("Edited title",)]
    assert "Applied 1 changes" in tab.editor.bar.status.text()
    assert not tab.editor.bar.apply_button.isVisibleTo(tab)
    qtbot.waitUntil(lambda: cell(tab, 0, "title") == "Edited title", timeout=10000)  # reloaded


def test_cancelling_the_review_writes_nothing(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = add_shop(env, "Shop", rows=5)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "title", "Edited")
    prompts.preview_accepts = False
    assert not tab.editor.request_apply()
    assert tab.pending == 1
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 1") == [("Book 001",)]


def test_apply_with_nothing_pending_does_nothing(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    assert not tab.editor.request_apply()
    assert prompts.previews == []


def test_updates_inserts_and_deletes_are_written_together(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=5)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "title", "Changed")
    select_row(tab, 4)
    tab.editor.delete_rows()
    row = tab.editor.add_row()
    assert row is not None
    edit_text(tab, row, "author_id", "2")
    edit_text(tab, row, "title", "Brand new")
    apply_all(tab, qtbot, prompts)
    (dialog,) = prompts.previews
    assert "rows to change: 1" in dialog.summary.text()
    assert "rows to add: 1" in dialog.summary.text()
    assert "rows to delete: 1" in dialog.summary.text()
    titles = [r[0] for r in db_rows(env, config, "SELECT title FROM book ORDER BY id")]
    assert titles == ["Changed", "Book 002", "Book 003", "Book 004", "Brand new"]
    assert tab.pending == 0
    assert model_of(tab).rowCount() == 5


def test_a_conflict_is_reported_at_the_row_and_the_edit_is_kept(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=5)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "title", "Mine")
    edit_text(tab, 2, "title", "Also mine")
    con = sqlite3.connect(config.path)
    con.execute("UPDATE book SET title = 'Theirs' WHERE id = 1")
    con.commit()
    con.close()
    apply_all(tab, qtbot, prompts)
    assert "changed or deleted by someone else" in tab.editor.bar.problem.text()
    assert "id = 1" in tab.editor.bar.problem.text()
    assert "Nothing was written" in tab.editor.bar.problem.text()
    assert tab.pending == 2  # the edits are still there
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 3") == [("Book 003",)]
    model = model_of(tab)
    assert model.index(0, 2).data(Qt.ItemDataRole.BackgroundRole).alpha() > 100
    assert "someone else" in model.index(0, 2).data(Qt.ItemDataRole.ToolTipRole)
    assert tab.editor.bar.reload_button.isVisibleTo(tab)
    # reload keeps the edits against the fresh rows, and applying now overrides the other change
    tab.editor.bar.reload_button.click()
    qtbot.waitUntil(lambda: not tab._loading, timeout=10000)
    assert tab.pending == 2
    assert cell(tab, 0, "title") == "Mine"
    apply_all(tab, qtbot, prompts)
    assert db_rows(env, config, "SELECT title FROM book WHERE id IN (1, 3) ORDER BY id") == [
        ("Mine",),
        ("Also mine",),
    ]


def test_a_failing_statement_marks_its_row_and_writes_nothing(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=5)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "title", "Fine")
    row = tab.editor.add_row()
    assert row is not None
    edit_text(tab, row, "id", "2")  # exists already
    edit_text(tab, row, "author_id", "1")
    apply_all(tab, qtbot, prompts)
    assert (
        "UNIQUE" in tab.editor.bar.problem.text().upper()
        or "constraint" in tab.editor.bar.problem.text()
    )
    assert "Nothing was written" in tab.editor.bar.problem.text()
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 1") == [("Book 001",)]
    assert tab.pending == 2
    assert model_of(tab).error_rows() == [5]


def test_editing_an_errored_cell_clears_its_error(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = add_shop(env, "Shop", rows=5)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    row = tab.editor.add_row()
    assert row is not None
    edit_text(tab, row, "id", "2")
    edit_text(tab, row, "author_id", "1")
    apply_all(tab, qtbot, prompts)
    model = model_of(tab)
    assert model.error_rows() == [5]
    edit_text(tab, 5, "id", "99")
    assert model.error_rows() == []
    apply_all(tab, qtbot, prompts)
    assert tab.pending == 0
    assert db_rows(env, config, "SELECT id FROM book WHERE id = 99") == [(99,)]


def test_a_production_connection_asks_once_more(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = add_shop(env, "Shop", rows=5)
    config = config.model_copy(update={"production": True})
    env.services.store.save(config)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "title", "Prod change")
    prompts.question_answer = prompts.question_answer.__class__.No
    assert not tab.editor.request_apply()
    assert len(prompts.questions) == 1
    assert "PRODUCTION" in prompts.questions[0]
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 1") == [("Book 001",)]
    prompts.question_answer = prompts.question_answer.__class__.Yes
    apply_all(tab, qtbot, prompts)
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 1") == [("Prod change",)]
    assert (
        "PRODUCTION"
        in prompts.previews[0]
        .findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)[0]
        .text()
    )


def test_changes_survive_paging_sorting_and_filtering(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Shop", rows=30)
    tab = open_table(open_workspace(env, qtbot, config, page_size=10), qtbot)
    edit_text(tab, 0, "title", "first page edit")
    tab.next_page()
    wait_loaded(tab, qtbot)
    assert tab.offset == 10
    assert cell(tab, 0, "title") == "Book 011"
    edit_text(tab, 0, "title", "second page edit")
    assert tab.pending == 2
    tab.previous_page()
    wait_loaded(tab, qtbot)
    assert cell(tab, 0, "title") == "first page edit"
    tab.grid.horizontalHeader().sectionClicked.emit(2)  # sort by title: rows move, edits follow
    wait_loaded(tab, qtbot)
    titles = [cell(tab, r, "title") for r in range(model_of(tab).rowCount())]
    assert "first page edit" in titles
    assert tab.pending == 2


def test_a_new_row_stays_while_paging(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Shop", rows=30)
    tab = open_table(open_workspace(env, qtbot, config, page_size=10), qtbot)
    tab.editor.add_row()
    tab.next_page()
    wait_loaded(tab, qtbot)
    assert model_of(tab).rowCount() == 11
    assert model_of(tab).is_new(10)


def test_the_primary_key_can_be_edited(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = add_shop(env, "Shop", rows=3)
    tab = open_table(open_workspace(env, qtbot, config), qtbot)
    edit_text(tab, 0, "id", "100")
    apply_all(tab, qtbot, prompts)
    assert [r[0] for r in db_rows(env, config, "SELECT id FROM book ORDER BY id")] == [2, 3, 100]


# ---------------------------------------------------------------------------- menus and misc


def test_the_context_menu_offers_the_editing_actions(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    model = model_of(tab)
    index = model.index(0, 2)
    tab.grid.setCurrentIndex(index)
    menu = tab.grid.build_menu(index)
    actions = {a.text().split("\t")[0]: a for a in menu.actions() if a.text()}
    assert actions["Edit cell"].isEnabled()
    assert actions["Set NULL"].isEnabled()
    assert actions["New row"].isEnabled()
    assert not actions["Use the default"].isEnabled()  # only for new rows
    assert not actions["Apply changes…"].isEnabled()
    assert not actions["Undo"].isEnabled()
    assert "Copy as CSV" in actions
    edit_text(tab, 0, "title", "x")
    actions = {a.text().split("\t")[0]: a for a in tab.grid.build_menu(index).actions() if a.text()}
    assert actions["Apply changes…"].isEnabled()
    assert actions["Undo"].isEnabled()
    assert actions["Revert this cell"].isEnabled()


def test_set_null_from_the_menu(env: Env, qtbot: QtBot) -> None:
    tab = open_table(open_workspace(env, qtbot), qtbot)
    model = model_of(tab)
    index = model.index(0, model.result.columns.index("published"))
    menu = tab.grid.build_menu(index)
    next(a for a in menu.actions() if a.text() == "Set NULL").trigger()
    assert cell(tab, 0, "published") is None
    assert tab.pending == 1


def test_a_boolean_gets_a_combo_box(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Shop", rows=0)
    con = sqlite3.connect(config.path)
    con.executescript(
        "CREATE TABLE flags (id INTEGER PRIMARY KEY, on_sale BOOLEAN, note TEXT);"
        " INSERT INTO flags VALUES (1, 1, 'x');"
    )
    con.close()
    tab = open_table(open_workspace(env, qtbot, config), qtbot, "flags")
    model = model_of(tab)
    index = model.index(0, 1)
    tab.grid.setCurrentIndex(index)
    tab.grid.edit(index)
    combo = tab.grid.viewport().findChild(QComboBox)
    assert combo is not None
    assert combo.currentData() is True
    combo.setCurrentIndex(combo.findData(False))
    commit(tab, combo)
    assert model.value(0, 1) is False


def test_a_long_text_is_edited_in_a_dialog(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = add_shop(env, "Shop", rows=0)
    con = sqlite3.connect(config.path)
    con.executescript(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);"
        " INSERT INTO notes VALUES (1, 'line one\nline two');"
    )
    con.close()
    tab = open_table(open_workspace(env, qtbot, config), qtbot, "notes")
    model = model_of(tab)
    index = model.index(0, 1)
    prompts.long_text = "line one\nline two\nline three"
    assert tab.grid.edit(index, tab.grid.EditTrigger.EditKeyPressed, None) is False
    assert model.value(0, 1) == "line one\nline two\nline three"
    assert tab.pending == 1


def test_closing_a_table_tab_with_changes_asks(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    workspace = open_workspace(env, qtbot)
    tab = open_table(workspace, qtbot)
    edit_text(tab, 0, "title", "x")
    panel = workspace.current_tab().results  # type: ignore[union-attr]
    prompts.question_answer = prompts.question_answer.__class__.No
    panel._close_requested(panel.tabs.indexOf(tab))
    assert panel.table_count() == 1
    assert "not applied" in prompts.questions[-1]
    prompts.question_answer = prompts.question_answer.__class__.Yes
    panel._close_requested(panel.tabs.indexOf(tab))
    assert panel.table_count() == 0


def test_the_workspace_counts_pending_changes_of_all_tabs(env: Env, qtbot: QtBot) -> None:
    workspace = open_workspace(env, qtbot)
    tab = open_table(workspace, qtbot)
    assert workspace.pending_changes() == 0
    edit_text(tab, 0, "title", "x")
    edit_text(tab, 1, "title", "y")
    assert workspace.pending_changes() == 2


def test_the_workspace_applies_the_current_grid(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = add_shop(env, "Shop", rows=3)
    workspace = open_workspace(env, qtbot, config)
    tab = open_table(workspace, qtbot)
    edit_text(tab, 0, "title", "via workspace")
    workspace.apply_changes()
    qtbot.waitUntil(lambda: tab.pending == 0, timeout=10000)
    assert db_rows(env, config, "SELECT title FROM book WHERE id = 1") == [("via workspace",)]


def test_apply_without_a_connection_reports_instead_of_raising(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    workspace = open_workspace(env, qtbot)
    tab = open_table(workspace, qtbot)
    edit_text(tab, 0, "title", "x")
    workspace.set_session(None)
    tab.editor.request_apply()
    qtbot.waitUntil(lambda: bool(tab.editor.bar.problem.text()), timeout=5000)
    assert "not ready" in tab.editor.bar.problem.text()
    assert tab.pending == 1
    assert tab.editor.bar.isEnabled()
