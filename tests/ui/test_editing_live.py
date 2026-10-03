"""The whole path in the GUI — edit a cell, review, Alt+S, written — on every configured engine."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from easydbms.core.db import DatabaseClient
from easydbms.core.session import SchemaState
from easydbms.ui.results import TableTab
from easydbms.ui.results.editable_model import EditableModel
from easydbms.ui.workspace import QueryWorkspace
from tests.core.conftest import Target, target  # noqa: F401  (the engine fixture)

from .conftest import Env, Prompts

_TS = {"postgresql": "TIMESTAMP", "mysql": "DATETIME", "sqlite": "DATETIME"}


@dataclass
class Live:
    target: Target
    name: str
    tab: TableTab
    reader: DatabaseClient

    def rows(self) -> list[tuple[Any, ...]]:
        quoted = self.target.quote(self.name)
        return list(self.reader.execute(f"SELECT * FROM {quoted} ORDER BY id").rows)

    @property
    def model(self) -> EditableModel:
        model = self.tab.grid.model()
        assert isinstance(model, EditableModel)
        return model


@pytest.fixture
def live(env: Env, qtbot: QtBot, target: Target) -> Iterator[Live]:  # noqa: F811
    name = target.table()
    quoted = target.quote(name)
    reader = target.client()
    reader.connect()
    reader.execute(
        f"CREATE TABLE {quoted} (id INTEGER PRIMARY KEY, title VARCHAR(60) NOT NULL, "
        f"pages INTEGER, price NUMERIC(10,2), added {_TS[target.name]}, note VARCHAR(100))"
    )
    reader.execute(
        f"INSERT INTO {quoted} (id, title, pages, price, added, note) VALUES "
        "(1, 'Dune', 412, 9.90, '2020-05-05 10:00:00', NULL), "
        "(2, 'Emma', 474, 7.50, NULL, 'classic'), (3, 'Ulysses', 730, NULL, NULL, NULL)"
    )
    env.services.store.save(target.config)
    session = env.services.manager.activate(target.config.id, target.password).result(timeout=15)
    qtbot.waitUntil(lambda: session.schema_state is SchemaState.READY, timeout=15000)
    workspace = QueryWorkspace(
        target.config,
        env.services.tab_store,
        lambda: 100,
        edit_keys=env.services.edit_keys,
    )
    qtbot.addWidget(workspace)
    workspace.set_session(session)
    workspace.resize(1000, 700)
    workspace.show()
    assert session.schema is not None
    table = next(t for t in session.schema.tables if t.name == name)
    tab = workspace.open_table(table)
    assert tab is not None
    qtbot.waitUntil(lambda: not tab._loading and tab.grid.model() is not None, timeout=15000)
    try:
        yield Live(target, name, tab, reader)
    finally:
        tab.editor.discard()
        reader.disconnect()


def test_edit_a_cell_then_alt_s_writes_it(live: Live, qtbot: QtBot, prompts: Prompts) -> None:
    columns = live.model.result.columns
    assert live.model.edit_cell_text(0, columns.index("title"), "Dune Messiah")
    assert live.model.edit_cell_text(0, columns.index("pages"), "256")
    assert live.model.edit_cell_text(1, columns.index("note"), "")  # text: an empty string
    assert live.model.set_null(2, columns.index("note")) is False  # already NULL: no change
    live.tab.grid.setFocus()
    QTest.keyClick(live.tab.grid, Qt.Key.Key_S, Qt.KeyboardModifier.AltModifier)
    qtbot.waitUntil(lambda: live.tab.pending == 0, timeout=15000)
    sql = prompts.previews[0].sql.toPlainText()
    assert "UPDATE" in sql
    assert "Dune Messiah" in sql
    rows = {r[0]: r for r in live.rows()}
    assert rows[1][1:3] == ("Dune Messiah", 256)
    assert rows[2][5] == ""
    assert "Applied 2 changes" in live.tab.editor.bar.status.text()


def test_new_and_deleted_rows_are_written(live: Live, qtbot: QtBot, prompts: Prompts) -> None:
    columns = live.model.result.columns
    live.tab.grid.selectRow(2)
    live.tab.editor.delete_rows()
    row = live.tab.editor.add_row()
    assert row is not None
    assert live.model.edit_cell_text(row, columns.index("id"), "10")
    assert live.model.edit_cell_text(row, columns.index("title"), "Brand new")
    assert live.model.edit_cell_text(row, columns.index("price"), "12.5")
    assert live.model.edit_cell_text(row, columns.index("added"), "2024-02-29 13:45:10")
    live.tab.editor.request_apply()
    qtbot.waitUntil(lambda: live.tab.pending == 0, timeout=15000)
    rows = live.rows()
    assert [r[0] for r in rows] == [1, 2, 10]
    assert rows[2][1] == "Brand new"
    assert str(rows[2][4]).startswith("2024-02-29 13:45:10")


def test_a_row_changed_elsewhere_is_a_conflict_and_nothing_is_written(
    live: Live, qtbot: QtBot, prompts: Prompts
) -> None:
    columns = live.model.result.columns
    quoted = live.target.quote(live.name)
    live.model.edit_cell_text(0, columns.index("title"), "Mine")
    live.model.edit_cell_text(1, columns.index("title"), "Also mine")
    live.reader.execute(f"UPDATE {quoted} SET title = 'Theirs' WHERE id = 1")
    live.tab.editor.request_apply()
    qtbot.waitUntil(lambda: bool(live.tab.editor.bar.problem.text()), timeout=15000)
    assert "someone else" in live.tab.editor.bar.problem.text()
    assert live.tab.pending == 2
    titles = {r[0]: r[1] for r in live.rows()}
    assert titles == {1: "Theirs", 2: "Emma", 3: "Ulysses"}


def test_a_constraint_error_rolls_everything_back(
    live: Live, qtbot: QtBot, prompts: Prompts
) -> None:
    columns = live.model.result.columns
    live.model.edit_cell_text(0, columns.index("title"), "Fine")
    row = live.tab.editor.add_row()
    assert row is not None
    live.model.edit_cell_text(row, columns.index("id"), "2")  # a duplicate key
    live.model.edit_cell_text(row, columns.index("title"), "Dup")
    live.tab.editor.request_apply()
    qtbot.waitUntil(lambda: bool(live.tab.editor.bar.problem.text()), timeout=15000)
    assert "Nothing was written" in live.tab.editor.bar.problem.text()
    assert live.rows()[0][1] == "Dune"
    assert live.model.error_rows() == [3]
