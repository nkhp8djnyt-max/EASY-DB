from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QWidget
from pytestqt.qtbot import QtBot

from sql_erd_studio.core.db import QueryError, QueryResult
from sql_erd_studio.core.dialects import Statement
from sql_erd_studio.core.queries import Outcome, StatementOutcome
from sql_erd_studio.ui.results import ResultGrid, ResultsPanel, ResultTableModel, format_cell


def result(
    columns: tuple[str, ...], rows: list[tuple[object, ...]], truncated: bool = False
) -> QueryResult:
    return QueryResult(columns, tuple(rows), None, truncated, 0.0123)


def cell(
    model: ResultTableModel, row: int, column: int, role: int = Qt.ItemDataRole.DisplayRole
) -> object:
    return model.data(model.index(row, column), role)


STATEMENT = Statement(0, 8, "select 1", "select 1", "SELECT")


def ok(res: QueryResult) -> StatementOutcome:
    return StatementOutcome(STATEMENT, Outcome.OK, res)


# ---------------------------------------------------------------------------- formatting


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (1.5, "1.5"),
        (Decimal("1E+2"), "100"),
        (Decimal("12.50"), "12.50"),
        (date(2024, 1, 2), "2024-01-02"),
        (datetime(2024, 1, 2, 3, 4, 5), "2024-01-02 03:04:05"),
        (b"\x00\xff", "\\x00ff"),
        ("multi\nline", "multi⏎line"),
        ("tab\there", "tab\there"),
    ],
)
def test_format_cell(value: object, text: str) -> None:
    assert format_cell(value) == text


def test_long_text_and_binary_are_truncated_for_display() -> None:
    assert len(format_cell("x" * 5000)) == 401
    assert format_cell("x" * 5000).endswith("…")
    assert format_cell(b"\x01" * 1000).endswith("…")


# ---------------------------------------------------------------------------- model


def test_model_shape_headers_and_values() -> None:
    model = ResultTableModel(result(("id", "name"), [(1, "a"), (2, None)]))
    assert (model.rowCount(), model.columnCount()) == (2, 2)
    assert model.headerData(1, Qt.Orientation.Horizontal) == "name"
    assert model.headerData(1, Qt.Orientation.Vertical) == "2"
    assert cell(model, 0, 1) == "a"
    assert cell(model, 1, 1) == "NULL"
    assert cell(model, 1, 1, Qt.ItemDataRole.UserRole) is None
    assert cell(model, 0, 0, Qt.ItemDataRole.UserRole) == 1


def test_numbers_align_right_text_left_and_null_is_muted() -> None:
    model = ResultTableModel(result(("n", "t"), [(1, "a"), (None, "b")]))
    right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    assert cell(model, 0, 0, Qt.ItemDataRole.TextAlignmentRole) == right
    assert cell(model, 0, 1, Qt.ItemDataRole.TextAlignmentRole) == left
    assert cell(model, 1, 0, Qt.ItemDataRole.ForegroundRole) is not None
    assert cell(model, 0, 0, Qt.ItemDataRole.ForegroundRole) is None


def test_sorting_is_vectorised_with_nulls_last_both_ways() -> None:
    model = ResultTableModel(result(("v",), [(3,), (None,), (1,), (2,)]))
    model.sort(0, Qt.SortOrder.AscendingOrder)
    assert [cell(model, r, 0) for r in range(4)] == ["1", "2", "3", "NULL"]
    model.sort(0, Qt.SortOrder.DescendingOrder)
    assert [cell(model, r, 0) for r in range(4)] == ["3", "2", "1", "NULL"]
    model.sort(-1)
    assert [cell(model, r, 0) for r in range(4)] == ["3", "NULL", "1", "2"]  # original order


def test_filter_and_sort_compose() -> None:
    model = ResultTableModel(
        result(("name", "n"), [("apple", 3), ("banana", 1), ("avocado", 2), ("cherry", 9)])
    )
    model.set_filter("A")
    assert model.rowCount() == 3
    assert model.total_rows == 4
    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert [cell(model, r, 0) for r in range(3)] == ["banana", "avocado", "apple"]
    model.set_filter("")
    assert model.rowCount() == 4
    assert model.sort_state == (1, False)  # the sort survives clearing the filter
    assert model.source_row(0) == 1


def test_model_signals_a_reset_when_the_view_changes(qtbot: QtBot) -> None:
    model = ResultTableModel(result(("a",), [(1,), (2,)]))
    with qtbot.waitSignal(model.modelReset):
        model.set_filter("2")


def test_an_empty_result_has_columns_but_no_rows() -> None:
    model = ResultTableModel(result(("a", "b"), []))
    assert (model.rowCount(), model.columnCount()) == (0, 2)
    model.sort(0)
    model.set_filter("x")
    assert model.rowCount() == 0


# ---------------------------------------------------------------------------- grid


@pytest.fixture
def grid(qtbot: QtBot) -> ResultGrid:
    widget = ResultGrid()
    qtbot.addWidget(widget)
    widget.resize(500, 300)
    widget.set_result_model(
        ResultTableModel(result(("id", "note"), [(1, "a\tb"), (2, None), (3, "c")]))
    )
    widget.show()
    return widget


def test_columns_are_sized_from_content(grid: ResultGrid) -> None:
    assert 60 <= grid.columnWidth(0) <= 420
    assert grid.columnWidth(1) >= 60


def test_copying_a_selection_gives_tab_separated_text(grid: ResultGrid) -> None:
    grid.selectAll()
    assert (
        grid.selected_text() == "1\ta b\n2\t\n3\tc"
    )  # NULL is empty, tabs inside cells are flattened
    assert grid.selected_text(header=True).splitlines()[0] == "id\tnote"
    grid.copy_selection()
    assert QGuiApplication.clipboard().text() == "1\ta b\n2\t\n3\tc"


def test_copying_a_partial_selection(grid: ResultGrid) -> None:
    model = grid.model()
    grid.selectionModel().select(model.index(0, 0), grid.selectionModel().SelectionFlag.Select)
    grid.selectionModel().select(model.index(2, 1), grid.selectionModel().SelectionFlag.Select)
    assert grid.selected_text() == "1\t\n\tc"


def test_nothing_selected_copies_nothing(grid: ResultGrid) -> None:
    grid.clearSelection()
    assert grid.selected_text() == ""


def test_clicking_a_header_sorts_the_grid(grid: ResultGrid, qtbot: QtBot) -> None:
    grid.horizontalHeader().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
    model = grid.model()
    assert [model.data(model.index(r, 0)) for r in range(3)] == ["3", "2", "1"]


# ---------------------------------------------------------------------------- panel


def texts(page: QWidget) -> str:
    return " | ".join(label.text() for label in page.findChildren(QLabel))


@pytest.fixture
def panel(qtbot: QtBot) -> ResultsPanel:
    widget = ResultsPanel()
    qtbot.addWidget(widget)
    widget.resize(600, 300)
    widget.show()
    return widget


def test_a_fresh_panel_shows_a_hint(panel: ResultsPanel) -> None:
    assert panel.count() == 0
    assert not panel.tabs.isVisible()


def test_a_select_gets_a_grid_tab_with_a_summary(panel: ResultsPanel) -> None:
    panel.add_outcome(ok(result(("a",), [(1,), (2,)], truncated=True)))
    assert panel.tabs.tabText(0) == "Result 1"
    page = panel.current_page()
    assert page is not None
    shown = texts(page)
    assert "2 rows" in shown
    assert "cut off at 2 rows" in shown
    assert "12 ms" in shown
    assert panel.current_grid() is not None


def test_an_empty_select_shows_the_empty_state(panel: ResultsPanel) -> None:
    panel.add_outcome(ok(result(("a",), [])))
    page = panel.current_page()
    assert page is not None
    assert "Your query returned no data" in texts(page)
    assert panel.current_grid() is not None
    assert not panel.current_grid().isVisible()  # type: ignore[union-attr]


def test_commands_report_affected_rows(panel: ResultsPanel) -> None:
    insert = Statement(0, 20, "insert into t values (1)", "insert into t values (1)", "INSERT")
    panel.add_outcome(StatementOutcome(insert, Outcome.OK, QueryResult((), (), 3, False, 0.004)))
    assert panel.tabs.tabText(0) == "OK 1"
    page = panel.current_page()
    assert page is not None
    assert "Query OK" in texts(page)
    assert "3 rows affected" in texts(page)
    assert panel.current_grid() is None


def test_errors_get_a_red_tab_and_report_where_they_happened(
    panel: ResultsPanel, qtbot: QtBot
) -> None:
    statement = Statement(40, 50, "x", "x", "")
    error = QueryError('syntax error at or near "x"', code="42601", position=8)
    with qtbot.waitSignal(panel.errorLocated) as located:
        panel.add_outcome(StatementOutcome(statement, Outcome.ERROR, error=error))
    assert located.args == [40, 8]
    assert panel.tabs.tabText(0) == "Error 1"
    page = panel.current_page()
    assert page is not None
    assert "syntax error" in texts(page)
    assert "Query failed" in texts(page)


def test_an_error_without_a_position_reports_zero(panel: ResultsPanel, qtbot: QtBot) -> None:
    with qtbot.waitSignal(panel.errorLocated) as located:
        panel.add_outcome(StatementOutcome(STATEMENT, Outcome.ERROR, error=QueryError("boom")))
    assert located.args == [0, 0]


def test_cancelled_and_skipped_statements_are_labelled(panel: ResultsPanel) -> None:
    panel.add_outcome(StatementOutcome(STATEMENT, Outcome.CANCELLED))
    panel.add_outcome(StatementOutcome(STATEMENT, Outcome.SKIPPED))
    assert [panel.tabs.tabText(i) for i in range(2)] == ["Cancelled 1", "Skipped 2"]


def test_the_first_tab_is_selected_but_an_error_steals_focus(panel: ResultsPanel) -> None:
    panel.add_outcome(ok(result(("a",), [(1,)])))
    panel.add_outcome(ok(result(("b",), [(2,)])))
    assert panel.tabs.currentIndex() == 0
    panel.add_outcome(StatementOutcome(STATEMENT, Outcome.ERROR, error=QueryError("e")))
    assert panel.tabs.currentIndex() == 2


def test_clear_resets_numbering(panel: ResultsPanel) -> None:
    panel.add_outcome(ok(result(("a",), [(1,)])))
    panel.clear()
    assert panel.count() == 0
    panel.add_outcome(ok(result(("a",), [(1,)])))
    assert panel.tabs.tabText(0) == "Result 1"


def test_filtering_in_a_page_updates_the_summary(panel: ResultsPanel) -> None:
    panel.add_outcome(ok(result(("n",), [("alpha",), ("beta",), ("alphabet",)])))
    page = panel.current_page()
    assert page is not None
    page.filter_edit.setText("alpha")  # type: ignore[attr-defined]
    assert "2 of 3 rows" in texts(page)


def test_ddl_without_a_row_count_is_a_plain_ok_message(panel: ResultsPanel) -> None:
    create = Statement(0, 10, "create table t (a int)", "create table t (a int)", "CREATE")
    panel.add_outcome(StatementOutcome(create, Outcome.OK, QueryResult((), (), None, False, 0.001)))
    page = panel.current_page()
    assert page is not None
    assert panel.tabs.tabText(0) == "OK 1"
    assert "rows affected" not in texts(page)
    assert "Query OK" in texts(page)


def test_a_select_with_no_columns_known_is_still_a_result_grid(panel: ResultsPanel) -> None:
    panel.add_outcome(ok(QueryResult((), (), None, False, 0.001)))
    assert panel.tabs.tabText(0) == "Result 1"
