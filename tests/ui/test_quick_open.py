from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from easydbms.core.dialects import DialectId
from easydbms.core.schema import Column, DatabaseSchema, Table, TableKind
from easydbms.ui.quick_open import QuickOpenDialog


def schema() -> DatabaseSchema:
    def table(name: str, *columns: str, kind: TableKind = TableKind.TABLE) -> Table:
        return Table("main", name, kind, tuple(Column(c, "int") for c in columns))

    return DatabaseSchema(
        DialectId.SQLITE,
        ("main",),
        "main",
        (
            table("orders", "id", "customer_id"),
            table("order_items", "order_id", "sku"),
            table("customers", "id", "name"),
            table("v_orders", "id", kind=TableKind.VIEW),
        ),
    )


def labels(dialog: QuickOpenDialog) -> list[str]:
    return [dialog.list.item(i).text().split("   ")[0] for i in range(dialog.list.count())]


def test_everything_is_listed_without_a_query(qtbot: QtBot) -> None:
    dialog = QuickOpenDialog(schema())
    qtbot.addWidget(dialog)
    assert labels(dialog) == ["customers", "order_items", "orders", "v_orders"]
    assert dialog.list.currentRow() == 0


def test_prefix_matches_come_first_then_inner_matches_then_columns(qtbot: QtBot) -> None:
    dialog = QuickOpenDialog(schema())
    qtbot.addWidget(dialog)
    dialog.edit.setText("order")
    found = labels(dialog)
    assert found[:3] == ["order_items", "orders", "v_orders"]  # prefix before inner matches
    assert "order_items.order_id" in found  # a column that contains the text
    assert found.index("order_items.order_id") > 2  # columns after tables
    assert "orders.id" not in found  # neither the table nor the column name matches


def test_column_names_are_searchable(qtbot: QtBot) -> None:
    dialog = QuickOpenDialog(schema())
    qtbot.addWidget(dialog)
    dialog.edit.setText("sku")
    assert labels(dialog) == ["order_items.sku"]
    dialog.edit.setText("zzz")
    assert labels(dialog) == []


def test_views_are_marked(qtbot: QtBot) -> None:
    dialog = QuickOpenDialog(schema())
    qtbot.addWidget(dialog)
    dialog.edit.setText("v_orders")
    assert "view" in dialog.list.item(0).text()


def test_arrow_keys_move_the_selection_and_enter_chooses(qtbot: QtBot) -> None:
    dialog = QuickOpenDialog(schema())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.edit.setText("order")
    QTest.keyClick(dialog.edit, Qt.Key.Key_Down)
    assert dialog.list.currentRow() == 1
    QTest.keyClick(dialog.edit, Qt.Key.Key_Up)
    QTest.keyClick(dialog.edit, Qt.Key.Key_Up)
    assert dialog.list.currentRow() == 0
    dialog.edit.setText("order_items.sku")
    dialog.edit.setText("sku")
    dialog.accept()
    assert dialog.chosen is not None
    table, column = dialog.chosen
    assert (table.name, column) == ("order_items", "sku")


def test_accepting_an_empty_list_chooses_nothing(qtbot: QtBot) -> None:
    dialog = QuickOpenDialog(schema())
    qtbot.addWidget(dialog)
    dialog.edit.setText("nothing like this")
    dialog.accept()
    assert dialog.chosen is None
