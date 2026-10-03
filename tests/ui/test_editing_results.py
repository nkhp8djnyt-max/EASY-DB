"""Editing the result of a SELECT, the cell editors by type, copy formats and the dialogs."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QModelIndex, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QApplication,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTimeEdit,
)
from pytestqt.qtbot import QtBot

from easydbms.core.db import QueryResult
from easydbms.core.dialects import POSTGRESQL, SQLITE
from easydbms.core.editing import ChangeSet, build_statements
from easydbms.ui.results import ResultGrid
from easydbms.ui.results.delegates import CellDelegate
from easydbms.ui.results.dialogs import KeyDialog, PreviewDialog, TextDialog
from easydbms.ui.results.editable_model import EditableModel
from easydbms.ui.results.model import ResultTableModel
from easydbms.ui.workspace import QueryWorkspace
from tests.core.editing.conftest import books_table, target_of

from .conftest import Env, Prompts, add_shop
from .test_editing import db_rows, open_workspace

K = Qt.Key


def run_query(workspace: QueryWorkspace, qtbot: QtBot, sql: str) -> Any:
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText(sql)
    tab.editor.go_to(len(sql))
    workspace.run_current()
    qtbot.waitUntil(lambda: tab.results.count() == 1 and not tab.running, timeout=10000)
    page = tab.results.current_page()
    assert page is not None
    return page


# ---------------------------------------------------------------------------- a SELECT result


def test_a_plain_select_of_one_table_is_editable(env: Env, qtbot: QtBot) -> None:
    workspace = open_workspace(env, qtbot)
    page = run_query(workspace, qtbot, "SELECT * FROM book WHERE id < 4 ORDER BY id")
    assert page.editor.editable
    assert "Editable" in page.editor.bar.status.text()


def test_edits_of_a_select_result_are_applied_and_the_query_is_run_again(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=5)
    workspace = open_workspace(env, qtbot, config)
    page = run_query(workspace, qtbot, "SELECT id, title FROM book WHERE id < 3 ORDER BY id")
    model = page.model
    assert isinstance(model, EditableModel)
    assert model.edit_cell_text(0, 1, "From a query result")
    page.editor.request_apply()
    tab = workspace.current_tab()
    assert tab is not None
    qtbot.waitUntil(
        lambda: (
            db_rows(env, config, "SELECT title FROM book WHERE id = 1")
            == [("From a query result",)]
        ),
        timeout=10000,
    )
    qtbot.waitUntil(
        lambda: tab.results.current_page() is not page and not tab.running, timeout=10000
    )
    fresh = tab.results.current_page()
    assert fresh is not None
    assert fresh.model.value(0, 1) == "From a query result"  # type: ignore[attr-defined]
    assert fresh.editor.pending == 0  # type: ignore[attr-defined]


def test_columns_may_be_renamed_in_the_select_list(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = add_shop(env, "Shop", rows=3)
    workspace = open_workspace(env, qtbot, config)
    page = run_query(workspace, qtbot, "SELECT id AS book_id, title AS name FROM book ORDER BY id")
    assert page.editor.editable
    assert page.model.edit_cell_text(1, 1, "Renamed")
    page.editor.request_apply()
    qtbot.waitUntil(
        lambda: db_rows(env, config, "SELECT title FROM book WHERE id = 2") == [("Renamed",)],
        timeout=10000,
    )


@pytest.mark.parametrize(
    ("sql", "text"),
    [
        (
            "SELECT b.id, a.name FROM book b JOIN author a ON a.id = b.author_id",
            "not the rows of a single table",
        ),
        (
            "SELECT author_id, count(*) FROM book GROUP BY author_id",
            "not the rows of a single table",
        ),
        ("SELECT DISTINCT title FROM book", "not the rows of a single table"),
        ("SELECT title FROM book", "key columns (id)"),
        ("SELECT * FROM author_names", "is a view"),
        ("PRAGMA table_info(book)", "only a plain SELECT"),
    ],
)
def test_other_results_are_read_only_with_a_reason(
    env: Env, qtbot: QtBot, sql: str, text: str
) -> None:
    workspace = open_workspace(env, qtbot)
    page = run_query(workspace, qtbot, sql)
    assert not page.editor.editable
    assert text in page.editor.bar.status.text()
    assert not page.editor.bar.apply_button.isVisibleTo(page)


def test_without_the_database_structure_a_result_is_read_only(env: Env, qtbot: QtBot) -> None:
    workspace = open_workspace(env, qtbot)
    session = workspace._session
    assert session is not None
    session._schema = None
    page = run_query(workspace, qtbot, "SELECT * FROM book")
    assert "structure is not loaded" in page.editor.bar.status.text()


def test_the_filter_box_still_works_on_an_editable_result(env: Env, qtbot: QtBot) -> None:
    workspace = open_workspace(env, qtbot)
    page = run_query(workspace, qtbot, "SELECT * FROM book ORDER BY id")
    page.filter_edit.setText("Book 003")
    assert page.model.rowCount() == 1
    page.model.edit_cell_text(0, 2, "found and edited")
    assert page.editor.pending == 1
    page.filter_edit.setText("")
    assert page.model.rowCount() == 5
    assert page.model.value(2, 2) == "found and edited"


def test_sorting_an_editable_result_keeps_the_edits_on_their_rows(env: Env, qtbot: QtBot) -> None:
    workspace = open_workspace(env, qtbot)
    page = run_query(workspace, qtbot, "SELECT * FROM book ORDER BY id")
    page.model.edit_cell_text(0, 2, "zzz first")
    page.grid.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    values = [page.model.value(r, 2) for r in range(5)]
    # sorting uses the loaded values: the edited row ("Book 001") moves to the end with its edit
    assert values == ["Book 005", "Book 004", "Book 003", "Book 002", "zzz first"]
    assert page.editor.pending == 1


def test_a_new_row_stays_last_when_sorting(env: Env, qtbot: QtBot) -> None:
    workspace = open_workspace(env, qtbot)
    page = run_query(workspace, qtbot, "SELECT * FROM book ORDER BY id")
    page.editor.add_row()
    page.grid.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    assert page.model.is_new(5)


# ---------------------------------------------------------------------------- cell editors by type


@pytest.fixture
def typed_grid(qtbot: QtBot) -> Any:
    table = books_table()
    target = target_of(table, POSTGRESQL)
    columns = tuple(c.name for c in table.columns)
    row = (
        1,
        "Dune",
        412,
        Decimal("9.90"),
        4.5,
        date(1965, 8, 1),
        datetime(2020, 5, 5, 10, 30, 15),
        True,
        '{"a": 1}',
        b"\x00\x01",
    )
    result = QueryResult(columns, (row,), None, False, 0.0)
    model = EditableModel(result, target, ChangeSet(target))
    grid = ResultGrid()
    grid.setItemDelegate(CellDelegate(grid))
    grid.set_result_model(model)
    qtbot.addWidget(grid)
    grid.resize(900, 200)
    grid.show()
    return grid, model, columns


def editor_for(grid: ResultGrid, model: EditableModel, columns: tuple[str, ...], name: str) -> Any:
    index = model.index(0, columns.index(name))
    grid.setCurrentIndex(index)
    assert grid.edit(index)
    widget = None
    for kind in (QComboBox, QDateEdit, QTimeEdit, QDateTimeEdit, QLineEdit):
        widget = grid.viewport().findChild(kind)
        if widget is not None:
            break
    return index, widget


def commit_now(grid: ResultGrid, widget: Any) -> None:
    delegate = grid.itemDelegate()
    delegate.commitData.emit(widget)
    delegate.closeEditor.emit(widget, QAbstractItemDelegate.EndEditHint.NoHint)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # the editor goes away now


@pytest.mark.parametrize(
    ("column", "widget"),
    [
        ("in_print", QComboBox),
        ("published", QDateEdit),
        ("added", QDateTimeEdit),
        ("title", QLineEdit),
        ("pages", QLineEdit),
        ("price", QLineEdit),
        ("rating", QLineEdit),
        ("meta", QLineEdit),
    ],
)
def test_the_editor_fits_the_column_type(typed_grid: Any, column: str, widget: type) -> None:
    grid, model, columns = typed_grid
    _, editor = editor_for(grid, model, columns, column)
    assert type(editor) is widget
    commit_now(grid, editor)


def test_numbers_cannot_be_typed_as_text(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    _, editor = editor_for(grid, model, columns, "pages")
    assert editor.validator() is not None
    editor.setText("12x")
    assert not editor.hasAcceptableInput()
    editor.setText("-123")
    assert editor.hasAcceptableInput()
    commit_now(grid, editor)
    assert model.value(0, columns.index("pages")) == -123


def test_decimals_accept_a_point_and_an_exponent(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    _, editor = editor_for(grid, model, columns, "price")
    editor.setText("1.5e2")
    assert editor.hasAcceptableInput()
    commit_now(grid, editor)
    assert model.value(0, columns.index("price")) == Decimal("1.5E+2")


def test_a_date_is_picked_with_a_calendar_and_stored_as_a_date(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    _, editor = editor_for(grid, model, columns, "published")
    assert editor.calendarPopup()
    assert editor.date().year() == 1965
    editor.setDate(editor.date().addYears(1))
    commit_now(grid, editor)
    assert model.value(0, columns.index("published")) == date(1966, 8, 1)


def test_a_datetime_is_edited_with_seconds(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    _, editor = editor_for(grid, model, columns, "added")
    assert editor.dateTime().time().second() == 15
    editor.setTime(editor.time().addSecs(45))
    commit_now(grid, editor)
    assert model.value(0, columns.index("added")) == datetime(2020, 5, 5, 10, 31, 0)


def test_a_boolean_combo_includes_null_only_for_nullable_columns(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    _, combo = editor_for(grid, model, columns, "in_print")
    assert [combo.itemText(i) for i in range(combo.count())] == ["true", "false", "NULL"]
    combo.setCurrentIndex(2)
    commit_now(grid, combo)
    assert model.value(0, columns.index("in_print")) is None


def test_a_value_that_is_not_valid_text_is_refused_with_a_message(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    seen: list[str] = []
    model.editRejected.connect(seen.append)
    _, editor = editor_for(grid, model, columns, "meta")
    editor.setText("{broken")
    commit_now(grid, editor)
    assert model.value(0, columns.index("meta")) == '{"a": 1}'
    assert seen
    assert "valid JSON" in seen[0]


def test_binary_cells_have_no_editor(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    index = model.index(0, columns.index("cover"))
    grid.setCurrentIndex(index)
    assert not grid.edit(index)


def test_edit_text_of_each_type_starts_from_the_value(typed_grid: Any) -> None:
    grid, model, columns = typed_grid
    _, editor = editor_for(grid, model, columns, "price")
    assert editor.text() == "9.90"
    commit_now(grid, editor)
    _, editor = editor_for(grid, model, columns, "rating")
    assert editor.text() == "4.5"
    commit_now(grid, editor)


# ---------------------------------------------------------------------------- the model, in detail


def test_the_model_reports_structure_changes_to_views(qtbot: QtBot) -> None:
    table = books_table()
    target = target_of(table)
    columns = tuple(c.name for c in table.columns)
    result = QueryResult(columns, ((1,) + (None,) * 9, (2,) + (None,) * 9), None, False, 0.0)
    changes = ChangeSet(target)
    model = EditableModel(result, target, changes)
    inserted: list[tuple[int, int]] = []
    removed: list[tuple[int, int]] = []
    model.rowsInserted.connect(lambda _p, a, b: inserted.append((a, b)))
    model.rowsRemoved.connect(lambda _p, a, b: removed.append((a, b)))
    model.add_row()
    model.add_row()
    assert inserted == [(2, 2), (3, 3)]
    changes.undo()
    model.refresh()
    assert removed == [(3, 3)]
    resets: list[bool] = []
    model.modelReset.connect(lambda: resets.append(True))
    changes.reset()
    model.refresh()
    assert model.rowCount() == 2
    assert removed[-1] == (2, 2)
    assert resets == []


def test_keys_with_null_are_not_editable(qtbot: QtBot) -> None:
    from easydbms.core.editing import target_for_table
    from tests.core.editing.conftest import keyless_table

    table = keyless_table(unique=True)
    target = target_for_table(table, "stock", POSTGRESQL, ["sku", "qty"])
    assert not isinstance(target, type(None))
    result = QueryResult(("sku", "qty"), (("a", 1), (None, 2)), None, False, 0.0)
    model = EditableModel(result, target, ChangeSet(target))  # type: ignore[arg-type]
    assert model.flags(model.index(0, 1)) & Qt.ItemFlag.ItemIsEditable
    assert not model.flags(model.index(1, 1)) & Qt.ItemFlag.ItemIsEditable
    assert model.delete_rows([1]) == 0


def test_new_rows_show_what_is_required(qtbot: QtBot) -> None:
    table = books_table()
    target = target_of(table)
    columns = tuple(c.name for c in table.columns)
    model = EditableModel(QueryResult(columns, (), None, False, 0.0), target, ChangeSet(target))
    model.add_row()
    shown = {c: model.index(0, i).data() for i, c in enumerate(columns)}
    assert shown["id"] == "default"  # the key
    assert shown["title"] == "required"  # NOT NULL without a default
    assert shown["pages"] == "NULL"
    assert shown["in_print"] == "default"  # has a default


# ------------------------------------------------------------------ copy as CSV / Markdown


def grid_with(rows: list[tuple[object, ...]]) -> ResultGrid:
    result = QueryResult(("id", "text", "n"), tuple(rows), None, False, 0.0)
    grid = ResultGrid()
    grid.set_result_model(ResultTableModel(result))
    grid.selectAll()
    return grid


def test_copy_formats(qtbot: QtBot) -> None:
    grid = grid_with([(1, 'say "hi", ok', None), (2, "a|b\nc", Decimal("1.50"))])
    qtbot.addWidget(grid)
    assert grid.selected_text() == '1\tsay "hi", ok\t\n2\ta|b c\t1.50'
    assert grid.selected_text(header=True).splitlines()[0] == "id\ttext\tn"
    assert grid.selected_text(header=True, fmt="csv") == (
        'id,text,n\n1,"say ""hi"", ok",\n2,"a|b\nc",1.50'
    )
    assert grid.selected_text(fmt="csv").startswith('1,"say ""hi"", ok",')
    assert grid.selected_text(fmt="markdown") == (
        "| id | text | n |\n| --- | --- | --- |\n"
        '| 1 | say "hi", ok | NULL |\n| 2 | a\\|b<br>c | 1.50 |'
    )


def test_copy_as_csv_goes_to_the_clipboard(qtbot: QtBot) -> None:
    grid = grid_with([(1, "x", 2)])
    qtbot.addWidget(grid)
    grid.copy_selection(header=True, fmt="csv")
    assert QGuiApplication.clipboard().text() == "id,text,n\n1,x,2"


def test_the_copy_menu_works_on_a_read_only_grid(qtbot: QtBot) -> None:
    grid = grid_with([(1, "x", 2)])
    qtbot.addWidget(grid)
    menu = grid.build_menu(grid.model().index(0, 0))
    texts = [a.text().split("\t")[0] for a in menu.actions() if a.text()]
    assert texts == ["Copy", "Copy with headers", "Copy as CSV", "Copy as Markdown"]
    next(a for a in menu.actions() if a.text() == "Copy as Markdown").trigger()
    assert QGuiApplication.clipboard().text().startswith("| id | text | n |")


def test_copying_nothing_is_a_no_op(qtbot: QtBot) -> None:
    grid = grid_with([(1, "x", 2)])
    qtbot.addWidget(grid)
    grid.clearSelection()
    assert grid.selected_text(fmt="csv") == ""
    menu = grid.build_menu(QModelIndex())
    assert not menu.actions()[0].isEnabled()


# ---------------------------------------------------------------------------- dialogs


def planned_example() -> Any:
    table = books_table()
    changes = ChangeSet(target_of(table))
    original = {c.name: None for c in table.columns} | {"id": 1, "title": "Dune"}
    changes.set_cell((1,), original, "title", "Dune 2")
    changes.delete_row((2,), original | {"id": 2})
    changes.add_row({"title": "New"})
    return build_statements(changes, POSTGRESQL)


def test_the_review_dialog_lists_the_statements_and_counts_rows(qtbot: QtBot) -> None:
    dialog = PreviewDialog(planned_example(), POSTGRESQL, connection_name="db")
    qtbot.addWidget(dialog)
    assert dialog.summary.text() == "rows to change: 1 · rows to add: 1 · rows to delete: 1"
    lines = dialog.sql.toPlainText().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith('DELETE FROM "public"."books"')
    assert all(line.endswith(";") for line in lines)
    assert dialog.sql.isReadOnly()
    assert dialog.apply_button.isDefault()
    assert not [lb for lb in dialog.findChildren(QLabel) if "PRODUCTION" in lb.text()]


def test_the_review_dialog_marks_production(qtbot: QtBot) -> None:
    dialog = PreviewDialog(
        planned_example(), POSTGRESQL, connection_name="Billing", production=True
    )
    qtbot.addWidget(dialog)
    assert any("PRODUCTION — Billing" in label.text() for label in dialog.findChildren(QLabel))


def test_the_review_dialog_copies_its_sql_and_alt_s_applies(qtbot: QtBot) -> None:
    dialog = PreviewDialog(planned_example(), POSTGRESQL)
    qtbot.addWidget(dialog)
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text() == dialog.sql.toPlainText()
    shortcuts = [s for s in dialog.findChildren(QShortcut) if s.key() == QKeySequence("Alt+S")]
    assert len(shortcuts) == 1
    shortcuts[0].activated.emit()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_the_review_uses_the_dialect_of_the_connection(qtbot: QtBot) -> None:
    table = books_table()
    changes = ChangeSet(target_of(table, SQLITE))
    changes.add_row({"title": "x"})
    dialog = PreviewDialog(build_statements(changes, SQLITE), SQLITE)
    qtbot.addWidget(dialog)
    assert dialog.sql.toPlainText() == 'INSERT INTO "public"."books" ("title") VALUES (\'x\');'


def test_the_key_dialog_needs_at_least_one_column(qtbot: QtBot) -> None:
    table = books_table()
    dialog = KeyDialog(table, ["id"])
    qtbot.addWidget(dialog)
    assert dialog.chosen() == ("id",)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert ok.isEnabled()
    dialog.list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.chosen() == ()
    assert not ok.isEnabled()
    dialog.list.item(2).setCheckState(Qt.CheckState.Checked)
    dialog.list.item(1).setCheckState(Qt.CheckState.Checked)
    assert dialog.chosen() == ("title", "pages")  # in table order
    assert ok.isEnabled()
    assert "no primary key" in dialog.findChildren(QLabel)[0].text()


def test_the_text_dialog_returns_what_was_typed(qtbot: QtBot) -> None:
    dialog = TextDialog("Edit body", "one\ntwo")
    qtbot.addWidget(dialog)
    assert dialog.text() == "one\ntwo"
    dialog.editor.setPlainText("changed\nlines")
    assert dialog.text() == "changed\nlines"


# ---------------------------------------------------------------------------- the time editor


def test_a_time_gets_a_time_editor(qtbot: QtBot) -> None:
    from easydbms.core.schema import Column, Table

    table = Table(
        "public",
        "t",
        columns=(Column("id", "integer", primary_key=True, nullable=False), Column("at", "time")),
        primary_key=("id",),
    )
    target = target_of(table)
    result = QueryResult(("id", "at"), ((1, time(8, 30, 5)),), None, False, 0.0)
    model = EditableModel(result, target, ChangeSet(target))
    grid = ResultGrid()
    grid.setItemDelegate(CellDelegate(grid))
    grid.set_result_model(model)
    qtbot.addWidget(grid)
    grid.show()
    index = model.index(0, 1)
    grid.setCurrentIndex(index)
    assert grid.edit(index)
    editor = grid.viewport().findChild(QTimeEdit)
    assert editor is not None
    assert editor.time().minute() == 30
    editor.setTime(editor.time().addSecs(60))
    commit_now(grid, editor)
    assert model.value(0, 1) == time(8, 31, 5)


# ------------------------------------------------------------------ losing edits by accident


def test_running_a_query_again_asks_before_dropping_edited_results(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    workspace = open_workspace(env, qtbot)
    page = run_query(workspace, qtbot, "SELECT * FROM book ORDER BY id")
    page.model.edit_cell_text(0, 2, "edited")
    prompts.question_answer = QMessageBox.StandardButton.No
    workspace.run_current()
    assert "not applied" in prompts.questions[-1]
    tab = workspace.current_tab()
    assert tab is not None
    assert tab.results.current_page() is page  # nothing was replaced
    prompts.question_answer = QMessageBox.StandardButton.Yes
    workspace.run_current()
    qtbot.waitUntil(
        lambda: tab.results.current_page() is not page and not tab.running, timeout=10000
    )


def test_closing_a_query_tab_with_edited_results_asks(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    workspace = open_workspace(env, qtbot)
    workspace.new_tab()
    workspace.tabs.setCurrentIndex(0)
    page = run_query(workspace, qtbot, "SELECT * FROM book ORDER BY id")
    page.model.edit_cell_text(0, 2, "edited")
    prompts.question_answer = QMessageBox.StandardButton.No
    workspace.close_tab(0)
    assert workspace.tabs.count() == 2
    prompts.question_answer = QMessageBox.StandardButton.Yes
    workspace.close_tab(0)
    assert workspace.tabs.count() == 1


def test_running_does_not_ask_when_nothing_was_edited(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    workspace = open_workspace(env, qtbot)
    run_query(workspace, qtbot, "SELECT * FROM book")
    workspace.run_current()
    assert prompts.questions == []
