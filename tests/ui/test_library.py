"""History and saved queries in the workspace, the library window and the main window."""

from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QMessageBox, QTreeWidget, QTreeWidgetItem
from pytestqt.qtbot import QtBot

from easydbms.core.queries import HistoryOutcome
from easydbms.ui.library import LibraryDialog, SaveQueryDialog, format_duration, format_when
from easydbms.ui.workspace import QueryWorkspace

from .conftest import Env, Prompts
from .test_main_window import make_window
from .test_workspace import type_and_run, wait_idle


def make(env: Env, qtbot: QtBot) -> QueryWorkspace:
    config = env.add_sqlite("lite")
    session = env.services.manager.activate(config.id).result(timeout=5)
    workspace = QueryWorkspace(
        config,
        env.services.tab_store,
        lambda: 1000,
        history=env.services.history,
        saved=env.services.saved,
    )
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.resize(900, 600)
    workspace.show()
    return workspace


def top(tree: QTreeWidget, index: int) -> QTreeWidgetItem:
    item = tree.topLevelItem(index)
    assert item is not None
    return item


def kid(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    child = item.child(index)
    assert child is not None
    return child


def library_of(workspace: QueryWorkspace, which: str = "history") -> LibraryDialog:
    dialog = workspace.show_library(which)
    assert dialog is not None
    return dialog


# ---------------------------------------------------------------------------- recording


def test_every_statement_that_runs_is_remembered(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1 as a;\nselect 2 as b;", script=True)
    wait_idle(workspace, qtbot)
    entries = env.services.history.recent(workspace.config.id)
    assert [e.sql for e in entries] == ["select 2 as b", "select 1 as a"]
    assert all(e.outcome is HistoryOutcome.OK and e.row_count == 1 for e in entries)
    assert all(e.dialect == "sqlite" and e.duration >= 0 for e in entries)


def test_a_failing_statement_is_kept_with_its_error_and_the_skipped_ones_are_not(
    env: Env, qtbot: QtBot
) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1;\nselect * from nowhere;\nselect 3;", script=True)
    wait_idle(workspace, qtbot)
    entries = env.services.history.recent(workspace.config.id)
    assert [(e.sql, e.outcome) for e in entries] == [
        ("select * from nowhere", HistoryOutcome.ERROR),
        ("select 1", HistoryOutcome.OK),
    ]
    assert "nowhere" in entries[0].error


def test_commands_remember_how_many_rows_they_changed(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "create table t (id integer);", script=True)
    wait_idle(workspace, qtbot)
    type_and_run(workspace, "insert into t values (1), (2), (3);", script=True)
    wait_idle(workspace, qtbot)
    latest = env.services.history.recent(workspace.config.id)[0]
    assert (latest.sql, latest.row_count) == ("insert into t values (1), (2), (3)", 3)


def test_running_the_same_statement_again_counts_a_run(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    for _ in range(3):
        type_and_run(workspace, "select 1;", script=True)
        wait_idle(workspace, qtbot)
    (entry,) = env.services.history.recent(workspace.config.id)
    assert entry.runs == 3


def test_a_workspace_without_a_library_still_runs(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("plain")
    session = env.services.manager.activate(config.id).result(timeout=5)
    workspace = QueryWorkspace(config, env.services.tab_store, lambda: 1000)
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    type_and_run(workspace, "select 1;", script=True)
    wait_idle(workspace, qtbot)
    assert workspace.show_library() is None
    workspace.save_current_query()  # nothing to save into: must not raise


# ---------------------------------------------------------------------------- history window


def test_the_window_lists_the_history_and_previews_the_selection(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1 as one;\nselect * from nowhere;", script=True)
    wait_idle(workspace, qtbot)
    dialog = library_of(workspace)
    tree = dialog.history_tree
    assert tree.topLevelItemCount() == 2
    assert top(tree, 0).text(1) == "select * from nowhere"
    assert top(tree, 0).text(3) == "failed"
    assert top(tree, 1).text(3) == "1 rows"
    assert top(tree, 1).text(0) == "just now"
    assert dialog.history_preview.toPlainText() == "select * from nowhere"
    assert "nowhere" in dialog.history_message.text()  # the error is spelled out
    assert not dialog.history_message.isHidden()
    tree.setCurrentItem(top(tree, 1))
    assert dialog.history_preview.toPlainText() == "select 1 as one"
    assert dialog.history_message.isHidden()


def test_search_and_failures_only_narrow_the_list(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1 as alpha;\nselect 2 as beta;\nselect x;", script=True)
    wait_idle(workspace, qtbot)
    dialog = library_of(workspace)
    dialog.search_edit.setText("BETA")
    assert [top(dialog.history_tree, i).text(1) for i in range(1)] == ["select 2 as beta"]
    assert dialog.history_tree.topLevelItemCount() == 1
    dialog.search_edit.setText("")
    dialog.errors_check.setChecked(True)
    assert dialog.history_tree.topLevelItemCount() == 1
    assert top(dialog.history_tree, 0).text(3) == "failed"
    dialog.search_edit.setText("no such thing")
    assert dialog.history_tree.topLevelItemCount() == 0
    assert dialog.history_preview.toPlainText() == ""
    assert not dialog.history_open.isEnabled()


def test_a_history_entry_opens_in_a_new_tab_or_is_inserted(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 42 as answer;", script=True)
    wait_idle(workspace, qtbot)
    dialog = library_of(workspace)
    tabs_before = workspace.tabs.count()
    dialog.history_open.click()
    assert workspace.tabs.count() == tabs_before + 1
    opened = workspace.current_tab()
    assert opened is not None
    assert opened.editor.text() == "select 42 as answer"
    assert opened.editor.dialect.id.value == "sqlite"
    workspace.new_tab()
    dialog.history_insert.click()
    inserted = workspace.current_tab()
    assert inserted is not None
    assert inserted.editor.text() == "select 42 as answer"


def test_deleting_and_clearing_the_history(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1;\nselect 2;\nselect 3;", script=True)
    wait_idle(workspace, qtbot)
    dialog = library_of(workspace)
    dialog.history_delete.click()
    assert dialog.history_tree.topLevelItemCount() == 2
    prompts.question_answer = QMessageBox.StandardButton.No
    dialog.history_clear.click()
    assert dialog.history_tree.topLevelItemCount() == 2
    prompts.question_answer = QMessageBox.StandardButton.Yes
    dialog.history_clear.click()
    assert dialog.history_tree.topLevelItemCount() == 0
    assert not dialog.history_clear.isEnabled()
    assert env.services.history.count(workspace.config.id) == 0


def test_the_open_window_follows_new_runs(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    dialog = library_of(workspace)
    assert dialog.history_tree.topLevelItemCount() == 0
    type_and_run(workspace, "select 1;", script=True)
    wait_idle(workspace, qtbot)
    assert dialog.history_tree.topLevelItemCount() == 1


def test_the_history_is_per_connection(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 'mine';", script=True)
    wait_idle(workspace, qtbot)
    env.services.history.record(
        "someone-else", "select 'theirs'", "sqlite", duration=0.1, outcome=HistoryOutcome.OK
    )
    dialog = library_of(workspace)
    assert [
        top(dialog.history_tree, i).text(1) for i in range(dialog.history_tree.topLevelItemCount())
    ] == ["select 'mine'"]


# ---------------------------------------------------------------------------- saved queries


class _Accepting:
    """Answers ``SaveQueryDialog.exec`` with what the person would have typed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.name = "Revenue"
        self.folder = ""
        self.only_this = False
        self.accept = True
        self.shown: list[SaveQueryDialog] = []

        def fake_exec(dialog: SaveQueryDialog) -> int:
            self.shown.append(dialog)
            dialog.name_edit.setText(self.name)
            dialog.folder_combo.setEditText(self.folder)
            dialog.scope_check.setChecked(self.only_this)
            return 1 if self.accept else 0

        monkeypatch.setattr(SaveQueryDialog, "exec", fake_exec)


@pytest.fixture
def saving(monkeypatch: pytest.MonkeyPatch) -> _Accepting:
    return _Accepting(monkeypatch)


def test_saving_the_editor_text(env: Env, qtbot: QtBot, saving: _Accepting) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select * from orders\nwhere total > 100;")
    saving.folder = "Reports / Monthly"
    workspace.save_current_query()
    (query,) = env.services.saved.all()
    assert (query.name, query.folder, query.dialect) == ("Revenue", "Reports/Monthly", "sqlite")
    assert query.sql == "select * from orders\nwhere total > 100;"
    assert query.connection_id == ""
    assert "Saved the query" in workspace.status.text()


def test_saving_only_the_selection_for_this_connection(
    env: Env, qtbot: QtBot, saving: _Accepting
) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select 1;\nselect 2;")
    cursor = tab.editor.textCursor()
    cursor.setPosition(10)
    cursor.setPosition(19, cursor.MoveMode.KeepAnchor)
    tab.editor.setTextCursor(cursor)
    saving.only_this = True
    workspace.save_current_query()
    (query,) = env.services.saved.all()
    assert query.sql == "select 2;"
    assert query.connection_id == workspace.config.id


def test_cancelling_the_save_dialog_or_saving_nothing_saves_nothing(
    env: Env, qtbot: QtBot, saving: _Accepting
) -> None:
    workspace = make(env, qtbot)
    workspace.save_current_query()  # an empty editor
    assert not saving.shown
    assert "nothing to save" in workspace.status.text()
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select 1;")
    saving.accept = False
    workspace.save_current_query()
    assert env.services.saved.all() == []


def test_the_save_dialog_wants_a_name(qtbot: QtBot) -> None:
    dialog = SaveQueryDialog("select 1", ["A", "B"])
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._accept()
    assert dialog.result() == 0  # still open: no name yet
    dialog.name_edit.setText("  Named ")
    dialog._accept()
    assert dialog.result() == 1
    assert dialog.name == "Named"
    assert [dialog.folder_combo.itemText(i) for i in range(2)] == ["A", "B"]


def test_the_saved_tab_lists_folders_and_opens_queries(
    env: Env, qtbot: QtBot, saving: _Accepting
) -> None:
    workspace = make(env, qtbot)
    env.services.saved.add("loose", "select 'loose';", "sqlite")
    env.services.saved.add("in a folder", "select 'inside';", "sqlite", folder="Reports")
    env.services.saved.add("elsewhere", "select 'x';", "sqlite", connection_id="another")
    dialog = library_of(workspace, "saved")
    assert dialog.tabs.currentIndex() == 1
    tree = dialog.saved_tree
    folder = top(tree, 0)
    assert folder.text(0) == "Reports"
    assert kid(folder, 0).text(0) == "in a folder"
    assert top(tree, 1).text(0) == "loose"
    assert tree.topLevelItemCount() == 2  # "elsewhere" belongs to another connection
    tree.setCurrentItem(kid(folder, 0))
    assert dialog.saved_preview.toPlainText() == "select 'inside';"
    before = workspace.tabs.count()
    dialog.saved_open.click()
    assert workspace.tabs.count() == before + 1
    tab = workspace.current_tab()
    assert tab is not None
    assert (tab.title, tab.editor.text()) == ("in a folder", "select 'inside';")


def test_renaming_moving_and_deleting_a_saved_query(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    workspace = make(env, qtbot)
    query = env.services.saved.add("old name", "select 1;", "sqlite")
    dialog = library_of(workspace, "saved")
    prompts.text_answers = [("new name", True)]
    dialog.saved_rename.click()
    assert env.services.saved.get(query.id).name == "new name"  # type: ignore[union-attr]
    prompts.text_answers = [("Archive", True)]
    dialog.saved_move.click()
    assert env.services.saved.get(query.id).folder == "Archive"  # type: ignore[union-attr]
    assert top(dialog.saved_tree, 0).text(0) == "Archive"
    prompts.question_answer = QMessageBox.StandardButton.No
    dialog.saved_delete.click()
    assert env.services.saved.get(query.id) is not None
    prompts.question_answer = QMessageBox.StandardButton.Yes
    dialog.saved_delete.click()
    assert env.services.saved.get(query.id) is None
    assert not dialog.saved_open.isEnabled()


def test_a_history_entry_can_be_saved_as_a_query(
    env: Env, qtbot: QtBot, saving: _Accepting
) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 7 as lucky;", script=True)
    wait_idle(workspace, qtbot)
    dialog = library_of(workspace)
    saving.name = "Lucky"
    dialog.history_save.click()
    (query,) = env.services.saved.all()
    assert (query.name, query.sql) == ("Lucky", "select 7 as lucky")
    assert dialog.saved_tree.topLevelItemCount() == 1  # the saved tab refreshed


def test_searching_saved_queries_looks_at_names_and_text(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    env.services.saved.add("Alpha", "select 1;", "sqlite")
    env.services.saved.add("Beta", "select count(*) from invoices;", "sqlite")
    dialog = library_of(workspace, "saved")
    dialog.saved_search.setText("invoices")
    assert dialog.saved_tree.topLevelItemCount() == 1
    assert top(dialog.saved_tree, 0).text(0) == "Beta"
    dialog.saved_search.setText("alpha")
    assert top(dialog.saved_tree, 0).text(0) == "Alpha"


# ---------------------------------------------------------------------------- main window


def test_the_query_menu_opens_the_library(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    config = env.add_sqlite("lite")
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.current_workspace() is not None, timeout=10000)
    workspace = window.current_workspace()
    assert workspace is not None
    shortcuts = {
        a.text().replace("&", ""): a.shortcut().toString()
        for m in window.menuBar().findChildren(type(window.menuBar().addMenu("x")))
        for a in m.actions()
    }
    assert shortcuts["Save query…"] == "Ctrl+S"
    assert shortcuts["History…"] == "Ctrl+H"
    assert shortcuts["Saved queries…"] == "Ctrl+Shift+H"
    assert workspace.has_library


def test_deleting_a_connection_forgets_its_history_and_own_queries(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = env.add_sqlite("doomed")
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.current_workspace() is not None, timeout=10000)
    env.services.history.record(
        config.id, "select 1", "sqlite", duration=0.0, outcome=HistoryOutcome.OK
    )
    env.services.saved.add("own", "select 1", "sqlite", connection_id=config.id)
    shared = env.services.saved.add("shared", "select 2", "sqlite")
    env.services.store.remove(config.id)
    window._refresh()
    assert env.services.history.count(config.id) == 0
    assert [q.id for q in env.services.saved.all()] == [shared.id]


# ---------------------------------------------------------------------------- formatting


def test_times_are_shown_the_way_people_say_them() -> None:
    now = time.mktime((2024, 3, 10, 15, 0, 0, 0, 0, -1))
    assert format_when(now - 10, now) == "just now"
    assert format_when(now - 5 * 60, now) == "5 min ago"
    assert format_when(now - 2 * 3600, now) == "today 13:00"
    assert format_when(now - 24 * 3600, now) == "yesterday 15:00"
    assert format_when(now - 5 * 86400, now) == "2024-03-05 15:00"
    assert format_duration(0.0123) == "12 ms"
    assert format_duration(2.55) == "2.5 s" or format_duration(2.55) == "2.6 s"
