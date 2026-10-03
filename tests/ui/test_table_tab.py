from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from easydbms.core.db import QueryError, QueryResult, create_client
from easydbms.core.schema import introspect
from easydbms.ui.results import ResultTableModel, TableTab

from .conftest import Env, add_shop


def grid_model(tab: TableTab) -> ResultTableModel:
    model = tab.grid.model()
    assert isinstance(model, ResultTableModel)
    return model


class Rig:
    """A TableTab over a real SQLite table with 25 books, page size 10."""

    def __init__(self, env: Env, qtbot: QtBot) -> None:
        config = add_shop(env, "Shop", rows=25)
        self.client = create_client(config, None)
        self.client.connect()
        self.pool = ThreadPoolExecutor(max_workers=1)
        table = introspect(self.client).find("book")
        assert table is not None
        self.page_size = 10
        self.sqls: list[str] = []
        self.tab = TableTab(table, "book", self.client.dialect, self.load, lambda: self.page_size)
        qtbot.addWidget(self.tab)
        self.tab.resize(700, 400)
        self.tab.show()
        self.qtbot = qtbot

    def load(self, sql: str, max_rows: int) -> Future[QueryResult]:
        self.sqls.append(sql)
        return self.pool.submit(self.client.execute, sql, max_rows=max_rows)

    def wait(self) -> None:
        self.qtbot.waitUntil(lambda: not self.tab._loading, timeout=5000)

    def titles(self) -> list[object]:
        model = grid_model(self.tab)
        return [model.value(r, 2) for r in range(model.rowCount())]

    def ids(self) -> list[int]:
        model = grid_model(self.tab)
        return [model.value(r, 0) for r in range(model.rowCount())]

    def close(self) -> None:
        self.tab.shutdown()
        self.pool.shutdown(wait=True)
        self.client.disconnect()


@pytest.fixture
def rig(env: Env, qtbot: QtBot) -> Iterator[Rig]:
    value = Rig(env, qtbot)
    yield value
    value.close()


def test_the_first_page_loads_with_one_extra_row_to_detect_a_next_page(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    assert rig.ids() == list(range(1, 11))
    assert "LIMIT 11" in rig.sqls[0]
    assert "ORDER BY" in rig.sqls[0]
    assert rig.tab.next_button.isEnabled()
    assert not rig.tab.prev_button.isEnabled()
    assert "rows 1-10" in rig.tab.summary.text()


def test_next_and_previous_page(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    rig.tab.next_button.click()
    rig.wait()
    assert rig.ids() == list(range(11, 21))
    assert "OFFSET 10" in rig.sqls[-1]
    assert rig.tab.prev_button.isEnabled()
    rig.tab.next_button.click()
    rig.wait()
    assert rig.ids() == list(range(21, 26))
    assert not rig.tab.next_button.isEnabled()  # last page
    assert "rows 21-25" in rig.tab.summary.text()
    rig.tab.prev_button.click()
    rig.wait()
    assert rig.ids() == list(range(11, 21))


def test_reload_repeats_the_same_page(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    rig.tab.next_button.click()
    rig.wait()
    rig.tab.reload_button.click()
    rig.wait()
    assert rig.ids() == list(range(11, 21))
    assert rig.sqls[-1] == rig.sqls[-2]


def test_clicking_a_header_sorts_on_the_server_in_three_steps(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    title = 2
    header = rig.tab.grid.horizontalHeader()
    rig.tab.next_button.click()
    rig.wait()

    rig.tab._on_header_clicked(title)
    rig.wait()
    assert rig.tab.sort == ("title", False)
    assert rig.tab.offset == 0  # a new order starts from the first page
    assert '"title" ASC' in rig.sqls[-1]
    assert header.sortIndicatorSection() == title
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    assert rig.titles() == sorted(rig.titles())  # type: ignore[type-var]

    rig.tab._on_header_clicked(title)
    rig.wait()
    assert rig.tab.sort == ("title", True)
    assert '"title" DESC' in rig.sqls[-1]
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    assert rig.titles()[0] == "Book 025"

    rig.tab._on_header_clicked(title)
    rig.wait()
    assert rig.tab.sort is None
    assert header.sortIndicatorSection() == -1
    assert rig.ids() == list(range(1, 11))

    rig.tab._on_header_clicked(0)  # another column starts ascending
    rig.wait()
    assert rig.tab.sort == ("id", False)


def test_a_where_filter_runs_on_the_server(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    rig.tab.filter_edit.setText("published >= 2015 AND title LIKE 'Book 0%'")
    rig.tab.apply_filter()
    rig.wait()
    assert rig.tab.where == "published >= 2015 AND title LIKE 'Book 0%'"
    assert "WHERE (" in rig.sqls[-1]
    assert rig.ids()
    model = grid_model(rig.tab)
    assert all(model.value(r, 3) >= 2015 for r in range(model.rowCount()))

    rig.tab.filter_edit.setText("title = 'no such book'")
    rig.tab.apply_filter()
    rig.wait()
    assert rig.tab._message.text() == "No rows match the filter."

    rig.tab.filter_edit.clear()
    rig.tab.apply_filter()
    rig.wait()
    assert rig.ids() == list(range(1, 11))


def test_a_filter_with_a_second_statement_is_refused_without_running_anything(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    count = len(rig.sqls)
    rig.tab.filter_edit.setText("1=1; DROP TABLE book")
    rig.tab.apply_filter()
    assert len(rig.sqls) == count  # nothing was sent
    assert rig.tab.filter_edit.property("invalid") is True
    assert "Only one condition" in rig.tab._message.text()
    rig.tab.filter_edit.setText("id > 1")  # typing clears the red border
    assert rig.tab.filter_edit.property("invalid") is False


def test_a_sql_error_is_shown_and_does_not_break_the_tab(rig: Rig) -> None:
    rig.tab.load()
    rig.wait()
    rig.tab.filter_edit.setText("no_such_column = 1")
    rig.tab.apply_filter()
    rig.wait()
    assert "no_such_column" in rig.tab._message.text()
    assert rig.tab.summary.text() == "Could not load the rows"
    rig.tab.filter_edit.clear()
    rig.tab.apply_filter()
    rig.wait()
    assert rig.ids() == list(range(1, 11))


def test_an_empty_table_says_so(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Empty")
    client = create_client(config, None)
    client.connect()
    table = introspect(client).find("tag")
    assert table is not None
    pool = ThreadPoolExecutor(max_workers=1)
    tab = TableTab(
        table,
        "tag",
        client.dialect,
        lambda sql, n: pool.submit(client.execute, sql, max_rows=n),
        lambda: 10,
    )
    qtbot.addWidget(tab)
    tab.load()
    qtbot.waitUntil(lambda: not tab._loading, timeout=5000)
    assert tab._message.text() == "This table is empty."
    assert not tab.next_button.isEnabled()
    tab.shutdown()
    pool.shutdown()
    client.disconnect()


def test_a_page_size_change_applies_to_the_next_load(rig: Rig) -> None:
    rig.page_size = 5
    rig.tab.load()
    rig.wait()
    assert len(rig.ids()) == 5
    rig.tab.next_button.click()
    rig.wait()
    assert rig.ids() == [6, 7, 8, 9, 10]


class Manual:
    """A loader whose futures the test completes by hand, to reorder the answers."""

    def __init__(self) -> None:
        self.futures: list[Future[QueryResult]] = []

    def __call__(self, sql: str, max_rows: int) -> Future[QueryResult]:
        future: Future[QueryResult] = Future()
        self.futures.append(future)
        return future


def result(*ids: int) -> QueryResult:
    return QueryResult(("id", "title"), tuple((i, f"t{i}") for i in ids), None, False, 0.001)


def test_a_late_answer_for_an_old_request_is_ignored(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Late")
    client = create_client(config, None)
    client.connect()
    table = introspect(client).find("book")
    assert table is not None
    manual = Manual()
    tab = TableTab(table, "book", client.dialect, manual, lambda: 10)
    qtbot.addWidget(tab)
    tab.load()
    first = manual.futures[0]
    tab._loading = False  # as if the user got impatient and reloaded
    tab.reload()
    second = manual.futures[1]
    second.set_result(result(7, 8))
    qtbot.waitUntil(lambda: grid_model(tab) is not None, timeout=2000)
    first.set_result(result(1, 2, 3))  # arrives late
    qtbot.wait(100)
    model = grid_model(tab)
    assert [model.value(r, 0) for r in range(model.rowCount())] == [7, 8]
    client.disconnect()


def test_failures_of_the_loader_itself_are_reported(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Gone")
    client = create_client(config, None)
    client.connect()
    table = introspect(client).find("book")
    assert table is not None

    def refusing(sql: str, max_rows: int) -> Future[QueryResult]:
        raise RuntimeError("the connection is not ready")

    tab = TableTab(table, "book", client.dialect, refusing, lambda: 10)
    qtbot.addWidget(tab)
    tab.load()
    assert "not ready" in tab._message.text()
    assert not tab._loading

    failing = Manual()
    tab2 = TableTab(table, "book", client.dialect, failing, lambda: 10)
    qtbot.addWidget(tab2)
    tab2.load()
    failing.futures[0].set_exception(QueryError("boom"))
    qtbot.waitUntil(lambda: "boom" in tab2._message.text(), timeout=2000)
    client.disconnect()


def test_nothing_happens_after_shutdown(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Closed")
    client = create_client(config, None)
    client.connect()
    table = introspect(client).find("book")
    assert table is not None
    manual = Manual()
    tab = TableTab(table, "book", client.dialect, manual, lambda: 10)
    qtbot.addWidget(tab)
    tab.load()
    tab.shutdown()
    manual.futures[0].set_result(result(1))
    qtbot.wait(100)
    assert tab.grid.model() is None
    client.disconnect()


def test_header_clicks_are_ignored_while_loading(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Busy")
    client = create_client(config, None)
    client.connect()
    table = introspect(client).find("book")
    assert table is not None
    manual = Manual()
    tab = TableTab(table, "book", client.dialect, manual, lambda: 10)
    qtbot.addWidget(tab)
    tab.load()
    manual.futures[0].set_result(result(1, 2))
    qtbot.waitUntil(lambda: not tab._loading, timeout=2000)
    tab.reload()
    count = len(manual.futures)
    tab._on_header_clicked(0)  # a request is in flight
    assert len(manual.futures) == count
    client.disconnect()
