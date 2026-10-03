from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from easydbms.core.connections import FileConnection
from easydbms.core.dialects import MYSQL, SQLITE, DialectId
from easydbms.core.queries import TabState
from easydbms.ui.workspace import QueryWorkspace

from .conftest import Env, Prompts, add_shop

SLOW = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"


def make(
    env: Env, qtbot: QtBot, config: FileConnection | None = None, row_limit: int = 1000
) -> QueryWorkspace:
    config = config or env.add_sqlite("lite")
    session = env.services.manager.activate(config.id).result(timeout=5)
    workspace = QueryWorkspace(config, env.services.tab_store, lambda: row_limit)
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.resize(800, 600)
    workspace.show()
    return workspace


def type_and_run(workspace: QueryWorkspace, sql: str, *, script: bool = False) -> None:
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText(sql)
    if script:
        workspace.run_script()
    else:
        tab.editor.go_to(len(sql))
        workspace.run_current()


def wait_idle(workspace: QueryWorkspace, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: not any(t.running for t in workspace.all_tabs()), timeout=15000)


def result_texts(workspace: QueryWorkspace) -> list[str]:
    tab = workspace.current_tab()
    assert tab is not None
    return [tab.results.tabs.tabText(i) for i in range(tab.results.count())]


# ---------------------------------------------------------------------------- running


def test_run_shows_the_result_grid(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1 as a, 'x' as b")
    wait_idle(workspace, qtbot)
    assert result_texts(workspace) == ["Result 1"]
    grid = workspace.current_tab().results.current_grid()  # type: ignore[union-attr]
    assert grid is not None
    model = grid.model()
    assert [model.data(model.index(0, c)) for c in range(2)] == ["1", "x"]
    assert "Done in" in workspace.status.text()


def test_run_executes_only_the_statement_under_the_cursor(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select 1 as first;\nselect 2 as second;")
    tab.editor.go_to(25)
    workspace.run_current()
    wait_idle(workspace, qtbot)
    grid = tab.results.current_grid()
    assert grid is not None
    assert grid.model().headerData(0, Qt.Orientation.Horizontal) == "second"
    assert tab.results.count() == 1


def test_run_script_makes_one_tab_per_statement(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(
        workspace,
        "create table t (a integer); insert into t values (1), (2); "
        "select * from t; select count(*) from t",
        script=True,
    )
    wait_idle(workspace, qtbot)
    assert result_texts(workspace) == ["OK 1", "OK 2", "Result 3", "Result 4"]


def test_an_error_stops_the_script_and_jumps_to_the_error(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1; select * from no_such_table; select 3", script=True)
    wait_idle(workspace, qtbot)
    assert result_texts(workspace) == ["Result 1", "Error 2", "Skipped 3"]
    assert "Failed after" in workspace.status.text()
    tab = workspace.current_tab()
    assert tab is not None
    assert tab.results.tabs.currentIndex() == 1
    assert tab.editor.textCursor().position() >= len("select 1; ")


def test_the_row_limit_cuts_results_off(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot, row_limit=5)
    type_and_run(
        workspace,
        "with recursive c(x) as (select 1 union all select x + 1 from c where x < 50) "
        "select x from c",
    )
    wait_idle(workspace, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    page = tab.results.current_page()
    assert page is not None
    assert page.model.rowCount() == 5  # type: ignore[attr-defined]
    assert page.model.result.truncated  # type: ignore[attr-defined]


def test_nothing_to_run_is_reported(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    workspace.run_current()
    assert "nothing to run" in workspace.status.text()


def test_without_a_session_nothing_runs(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    workspace.set_session(None)
    assert not workspace.run_button.isEnabled()
    type_and_run(workspace, "select 1")
    assert "not ready" in workspace.status.text()
    tab = workspace.current_tab()
    assert tab is not None
    assert tab.results.count() == 0


def test_stop_cancels_a_running_query(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, f"{SLOW}; select 1", script=True)
    qtbot.waitUntil(lambda: workspace.stop_button.isEnabled(), timeout=5000)
    assert not workspace.run_button.isEnabled()
    qtbot.wait(300)
    workspace.cancel()
    wait_idle(workspace, qtbot)
    assert result_texts(workspace) == ["Cancelled 1", "Skipped 2"]
    assert "Cancelled after" in workspace.status.text()
    assert workspace.run_button.isEnabled()
    type_and_run(workspace, "select 7")  # the connection is usable again
    wait_idle(workspace, qtbot)
    assert result_texts(workspace) == ["Result 1"]


# ---------------------------------------------------------------------------- confirmations


def test_dangerous_statements_ask_first_and_can_be_declined(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "create table u (a integer)", script=True)
    wait_idle(workspace, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    before = tab.results.count()
    prompts.question_answer = QMessageBox.StandardButton.No
    type_and_run(workspace, "delete from u", script=True)
    assert "deletes every row of u" in prompts.questions[-1]
    assert not tab.running
    assert tab.results.count() == before  # declined: nothing ran, previous results are kept


def test_confirming_runs_the_script(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "create table t (a integer)", script=True)
    wait_idle(workspace, qtbot)
    prompts.question_answer = QMessageBox.StandardButton.Yes
    type_and_run(workspace, "drop table t", script=True)
    wait_idle(workspace, qtbot)
    assert prompts.questions
    assert "drops t" in prompts.questions[-1]
    assert result_texts(workspace) == ["OK 1"]


def test_writes_on_a_production_connection_are_confirmed(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = env.add_sqlite("prod", production=True)
    workspace = make(env, qtbot, config)
    prompts.question_answer = QMessageBox.StandardButton.No
    type_and_run(workspace, "create table t (a integer)", script=True)
    assert "PRODUCTION" in prompts.questions[-1]
    assert workspace.current_tab().results.count() == 0  # type: ignore[union-attr]
    prompts.questions.clear()
    type_and_run(workspace, "select 1", script=True)  # reads need no confirmation
    wait_idle(workspace, qtbot)
    assert prompts.questions == []
    assert result_texts(workspace) == ["Result 1"]


def test_read_only_connections_report_refused_writes_as_errors(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("ro", read_only=True)
    workspace = make(env, qtbot, config)
    type_and_run(workspace, "create table t (a integer)", script=True)
    wait_idle(workspace, qtbot)
    assert result_texts(workspace) == ["Error 1"]


# ---------------------------------------------------------------------------- tabs


def test_a_new_workspace_has_one_empty_tab(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    assert workspace.tabs.count() == 1
    assert workspace.tabs.tabText(0) == "Query 1"


def test_new_tabs_get_free_numbers_and_closing_the_last_one_makes_a_fresh_tab(
    env: Env, qtbot: QtBot
) -> None:
    workspace = make(env, qtbot)
    workspace.new_tab()
    workspace.new_tab()
    assert [workspace.tabs.tabText(i) for i in range(3)] == ["Query 1", "Query 2", "Query 3"]
    workspace.close_tab(1)
    workspace.new_tab()
    assert [workspace.tabs.tabText(i) for i in range(3)] == ["Query 1", "Query 3", "Query 2"]
    for _ in range(3):
        workspace.close_current_tab()
    assert workspace.tabs.count() == 1
    assert workspace.tabs.tabText(0) == "Query 1"


def test_rename_a_tab(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    workspace = make(env, qtbot)
    prompts.text_answers = [("Report", True)]
    workspace.rename_tab(0)
    assert workspace.tabs.tabText(0) == "Report"
    prompts.text_answers = [("   ", True)]
    workspace.rename_tab(0)
    assert workspace.tabs.tabText(0) == "Report"  # blank names are ignored
    prompts.text_answers = [("", False)]
    workspace.rename_tab(0)
    assert workspace.tabs.tabText(0) == "Report"


def test_each_tab_keeps_its_own_results(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    type_and_run(workspace, "select 1")
    wait_idle(workspace, qtbot)
    workspace.new_tab()
    assert workspace.current_tab().results.count() == 0  # type: ignore[union-attr]
    workspace.tabs.setCurrentIndex(0)
    assert workspace.current_tab().results.count() == 1  # type: ignore[union-attr]


def test_the_dialect_is_per_tab(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    assert workspace.dialect_combo.currentData() == DialectId.SQLITE
    workspace.dialect_combo.setCurrentIndex(workspace.dialect_combo.findData(DialectId.MYSQL))
    workspace.dialect_combo.activated.emit(workspace.dialect_combo.currentIndex())
    assert workspace.current_tab().editor.dialect is MYSQL  # type: ignore[union-attr]
    workspace.new_tab()
    assert workspace.current_tab().editor.dialect is SQLITE  # type: ignore[union-attr]
    workspace.tabs.setCurrentIndex(0)
    assert workspace.dialect_combo.currentData() == DialectId.MYSQL


# ---------------------------------------------------------------------------- persistence


def test_tabs_are_saved_and_restored(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    workspace = make(env, qtbot, config)
    workspace.current_tab().editor.setPlainText("select 'é ✓'")  # type: ignore[union-attr]
    workspace.new_tab("Second", "select 2", MYSQL)
    workspace.tabs.setCurrentIndex(1)
    workspace.flush()
    assert env.services.tab_store.load(config.id) == [
        TabState("Query 1", "select 'é ✓'", "sqlite"),
        TabState("Second", "select 2", "mysql"),
    ]
    again = QueryWorkspace(config, env.services.tab_store, lambda: 1000)
    qtbot.addWidget(again)
    assert [again.tabs.tabText(i) for i in range(2)] == ["Query 1", "Second"]
    assert again.tabs.currentIndex() == 1
    assert again.current_tab().editor.text() == "select 2"  # type: ignore[union-attr]
    assert again.current_tab().editor.dialect is MYSQL  # type: ignore[union-attr]


def test_edits_are_saved_automatically_after_a_pause(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    workspace = make(env, qtbot, config)
    workspace.current_tab().editor.setPlainText("select 42")  # type: ignore[union-attr]
    qtbot.waitUntil(lambda: bool(env.services.tab_store.load(config.id)), timeout=3000)
    assert env.services.tab_store.load(config.id)[0].sql == "select 42"


def test_shutdown_flushes_pending_edits(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    workspace = make(env, qtbot, config)
    workspace.current_tab().editor.setPlainText("select 'last words'")  # type: ignore[union-attr]
    workspace.shutdown()
    assert env.services.tab_store.load(config.id)[0].sql == "select 'last words'"


# ---------------------------------------------------------------------------- format


def test_format_rewrites_the_editor_text_undoably(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select a,b from t where x=1")
    workspace.format_current()
    assert tab.editor.text() == "SELECT\n  a,\n  b\nFROM t\nWHERE\n  x = 1;"
    assert workspace.status.text() == "Formatted."
    tab.editor.undo()
    assert tab.editor.text() == "select a,b from t where x=1"


def test_format_only_touches_the_selection(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select 1;\nselect a,b from t")
    cursor = tab.editor.textCursor()
    cursor.setPosition(10)
    cursor.setPosition(len(tab.editor.text()), cursor.MoveMode.KeepAnchor)
    tab.editor.setTextCursor(cursor)
    workspace.format_current()
    assert tab.editor.text().startswith("select 1;\nSELECT")


def test_format_reports_syntax_errors_and_leaves_the_text(env: Env, qtbot: QtBot) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select 1;\nselect 'oops")
    workspace.format_current()
    assert tab.editor.text() == "select 1;\nselect 'oops"
    assert "unterminated" in workspace.status.text()
    assert "line 2" in workspace.status.text()
    assert tab.editor.textCursor().blockNumber() == 1


@pytest.mark.parametrize("text", ["", "   \n "])
def test_formatting_nothing_does_nothing(env: Env, qtbot: QtBot, text: str) -> None:
    workspace = make(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText(text)
    workspace.format_current()
    assert tab.editor.text() == text


# ---------------------------------------------------------------------------- tables


def shop_workspace(env: Env, qtbot: QtBot, rows: int = 25) -> QueryWorkspace:
    from .conftest import add_shop

    config = add_shop(env, "Shop", rows)
    session = env.services.manager.activate(config.id).result(timeout=5)
    qtbot.waitUntil(lambda: session.schema is not None, timeout=10000)
    workspace = QueryWorkspace(config, env.services.tab_store, lambda: 10)
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.resize(800, 600)
    workspace.show()
    return workspace


def table_of(workspace: QueryWorkspace, name: str):  # type: ignore[no-untyped-def]
    assert workspace._session is not None
    assert workspace._session.schema is not None
    table = workspace._session.schema.find(name)
    assert table is not None
    return table


def test_opening_a_table_adds_a_data_tab_that_loads_its_first_page(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    tab = workspace.open_table(table_of(workspace, "book"))
    assert tab is not None
    qtbot.waitUntil(lambda: not tab._loading, timeout=5000)
    results = workspace.current_tab().results  # type: ignore[union-attr]
    assert results.table_count() == 1
    assert results.tabs.tabText(0) == "▦ book"
    assert results.tabs.currentWidget() is tab
    model = tab.grid.model()
    assert model.rowCount() == 10
    assert "rows 1-10" in tab.summary.text()


def test_opening_the_same_table_again_reuses_its_tab(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    first = workspace.open_table(table_of(workspace, "book"))
    other = workspace.open_table(table_of(workspace, "author"))
    again = workspace.open_table(table_of(workspace, "book"))
    assert again is first
    assert other is not first
    results = workspace.current_tab().results  # type: ignore[union-attr]
    assert results.table_count() == 2
    assert results.tabs.currentWidget() is first


def test_running_a_query_keeps_the_table_tabs(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    workspace.open_table(table_of(workspace, "book"))
    type_and_run(workspace, "select 1 as a")
    wait_idle(workspace, qtbot)
    results = workspace.current_tab().results  # type: ignore[union-attr]
    assert results.table_count() == 1
    assert results.count() == 1  # the new statement result
    texts = [results.tabs.tabText(i) for i in range(results.tabs.count())]
    assert "▦ book" in texts
    assert "Result 1" in texts
    type_and_run(workspace, "select 2 as b")  # the old statement result is replaced, not stacked
    wait_idle(workspace, qtbot)
    assert results.count() == 1
    assert results.table_count() == 1


def test_table_tabs_can_be_closed_but_statement_results_have_no_close_button(
    env: Env, qtbot: QtBot
) -> None:
    from PySide6.QtWidgets import QTabBar

    workspace = shop_workspace(env, qtbot)
    workspace.open_table(table_of(workspace, "book"))
    type_and_run(workspace, "select 1")
    wait_idle(workspace, qtbot)
    results = workspace.current_tab().results  # type: ignore[union-attr]
    bar = results.tabs.tabBar()
    for index in range(results.tabs.count()):
        button = bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        is_table = results.tabs.tabText(index).startswith("▦")
        assert (button is not None) == is_table
    book_index = next(i for i in range(results.tabs.count()) if results.tabs.tabText(i) == "▦ book")
    results.tabs.tabCloseRequested.emit(book_index)
    assert results.table_count() == 0
    assert results.count() == 1


def test_table_tabs_belong_to_their_query_tab(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    first = workspace.current_tab()
    workspace.open_table(table_of(workspace, "book"))
    second = workspace.new_tab()
    assert second.results.table_count() == 0
    assert first is not None
    assert first.results.table_count() == 1


def test_inserting_a_name_and_select_star_go_to_the_editor(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select ")
    cursor = tab.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    tab.editor.setTextCursor(cursor)
    workspace.insert_text("title")
    assert tab.editor.text() == "select title"

    tab.editor.setPlainText("")
    workspace.select_all_from(table_of(workspace, "book"))
    assert tab.editor.text() == "SELECT * FROM book LIMIT 100;"
    workspace.select_all_from(table_of(workspace, "author"))
    assert tab.editor.text() == "SELECT * FROM book LIMIT 100;\nSELECT * FROM author LIMIT 100;"


def test_a_table_tab_without_a_session_reports_the_problem(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    table = table_of(workspace, "book")
    workspace.set_session(None)
    tab = workspace.open_table(table)
    assert tab is not None
    assert "not ready" in tab._message.text()


def test_closing_a_query_tab_stops_its_table_tabs(env: Env, qtbot: QtBot) -> None:
    workspace = shop_workspace(env, qtbot)
    workspace.new_tab()
    table_tab = workspace.open_table(table_of(workspace, "book"))
    assert table_tab is not None
    workspace.close_current_tab()
    assert not table_tab.alive


# ---------------------------------------------------------------------------- autocomplete


def make_completing(env: Env, qtbot: QtBot, keyword_case: str = "upper") -> QueryWorkspace:
    config = add_shop(env, "Shop")
    session = env.services.manager.activate(config.id).result(timeout=5)
    qtbot.waitUntil(lambda: session.schema is not None, timeout=10000)
    workspace = QueryWorkspace(
        config,
        env.services.tab_store,
        lambda: 1000,
        usage=env.services.usage_store,
        keyword_case=lambda: keyword_case,
    )
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.resize(800, 600)
    workspace.show()
    return workspace


def test_every_tab_completes_with_the_schema_of_the_session(env: Env, qtbot: QtBot) -> None:
    workspace = make_completing(env, qtbot)
    second = workspace.new_tab()
    for tab in (workspace.current_tab(), second):
        assert tab is not None
        assert tab.editor.completion.enabled
    second.editor.completion.debounce_ms = 5
    QTest.keyClicks(second.editor, "SELECT * FROM aut")
    qtbot.waitUntil(lambda: second.editor.completion.visible, timeout=5000)
    model = second.editor.completion.popup.model
    assert model.item(0) is not None
    assert model.item(0).label == "author"  # type: ignore[union-attr]


def test_the_complete_action_opens_the_list(env: Env, qtbot: QtBot) -> None:
    workspace = make_completing(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    workspace.complete()
    qtbot.waitUntil(lambda: tab.editor.completion.visible, timeout=5000)


def test_accepted_suggestions_are_counted_for_this_connection(env: Env, qtbot: QtBot) -> None:
    workspace = make_completing(env, qtbot)
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.completion.debounce_ms = 5
    QTest.keyClicks(tab.editor, "SELECT * FROM boo")
    qtbot.waitUntil(lambda: tab.editor.completion.visible, timeout=5000)
    QTest.keyClick(tab.editor, Qt.Key.Key_Tab)
    key = "table:main.book"
    qtbot.waitUntil(
        lambda: env.services.usage_store.counts(workspace.config.id).get(key) == 1, timeout=5000
    )


def test_the_keyword_case_is_read_when_asking(env: Env, qtbot: QtBot) -> None:
    case = {"value": "lower"}
    config = add_shop(env, "Shop")
    session = env.services.manager.activate(config.id).result(timeout=5)
    workspace = QueryWorkspace(
        config, env.services.tab_store, lambda: 1000, keyword_case=lambda: case["value"]
    )
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.show()
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.completion.debounce_ms = 5
    QTest.keyClicks(tab.editor, "SEL")
    qtbot.waitUntil(lambda: tab.editor.completion.visible, timeout=5000)
    labels = [i.label for i in iter_items(tab)]
    assert "select" in labels
    tab.editor.completion.hide()
    case["value"] = "upper"
    tab.editor.setPlainText("")
    QTest.keyClicks(tab.editor, "SEL")
    qtbot.waitUntil(lambda: "SELECT" in [i.label for i in iter_items(tab)], timeout=5000)


def iter_items(tab: object) -> list:  # type: ignore[type-arg]
    model = tab.editor.completion.popup.model  # type: ignore[attr-defined]
    return [m for row in range(model.rowCount()) if (m := model.item(row))]


def test_closing_the_workspace_stops_the_completion_worker(env: Env, qtbot: QtBot) -> None:
    workspace = make_completing(env, qtbot)
    workspace.shutdown()
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.completion.debounce_ms = 5
    QTest.keyClicks(tab.editor, "sel")  # no worker any more: must not raise or hang
    qtbot.wait(60)
    assert not tab.editor.completion.visible
