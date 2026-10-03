"""The autocomplete popup: when it opens, which keys it takes, what accepting does."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from easydbms.core.autocomplete import Completer, KeywordCase, Kind, UsageStore
from easydbms.core.dialects import MYSQL, POSTGRESQL
from easydbms.core.schema import DatabaseSchema
from easydbms.core.storage import AppDatabase
from easydbms.ui.editor import CompletionSource, SqlEditor
from tests.core.autocomplete.conftest import shop_schema

K = Qt.Key


@dataclass
class Rig:
    editor: SqlEditor
    usage: UsageStore
    connection: str = "c1"
    schema: DatabaseSchema | None = field(default_factory=shop_schema)
    case: KeywordCase = KeywordCase.UPPER

    def type(self, text: str) -> None:
        QTest.keyClicks(self.editor, text)

    def press(
        self, key: K, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
    ) -> None:
        QTest.keyClick(self.editor, key, modifiers)

    @property
    def popup(self):  # type: ignore[no-untyped-def]
        return self.editor.completion.popup

    def labels(self) -> list[str]:
        model = self.popup.model
        return [item.label for row in range(model.rowCount()) if (item := model.item(row))]


@pytest.fixture
def rig(qtbot: QtBot, tmp_path: Path) -> Iterator[Rig]:
    editor = SqlEditor(POSTGRESQL)
    qtbot.addWidget(editor)
    editor.resize(700, 300)
    editor.show()
    editor.setFocus()
    db = AppDatabase(tmp_path / "app.db")
    usage = UsageStore(db)
    pool = ThreadPoolExecutor(max_workers=1)
    state = Rig(editor, usage)
    source = CompletionSource(
        completer=Completer(),
        executor=pool,
        schema=lambda: state.schema,
        keyword_case=lambda: state.case,
        counts=lambda: usage.counts(state.connection),
        record=lambda key: usage.record(state.connection, key),
    )
    editor.set_completion(source)
    editor.completion.debounce_ms = 5
    yield state
    editor.completion.hide()
    pool.shutdown(wait=True)
    db.close()


def open_list(rig: Rig, qtbot: QtBot) -> None:
    qtbot.waitUntil(lambda: rig.editor.completion.visible, timeout=3000)


# ---------------------------------------------------------------------------- opening


def test_typing_opens_the_list_after_a_pause(rig: Rig, qtbot: QtBot) -> None:
    assert not rig.editor.completion.visible
    rig.type("sel")
    open_list(rig, qtbot)
    assert rig.labels()[:2] == ["sel", "SELECT"]
    assert rig.popup.current() is not None
    assert rig.popup.current().kind is Kind.SNIPPET  # type: ignore[union-attr]


def test_the_list_follows_what_is_typed(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM cus")
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["customers"], timeout=3000)
    rig.type("t")
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["customers"] and rig.popup.model.prefix == "cust")
    rig.type("xyzxyz")  # nothing matches any more: the list goes away
    qtbot.waitUntil(lambda: not rig.editor.completion.visible, timeout=3000)


def test_a_space_after_from_opens_the_tables(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM ")
    open_list(rig, qtbot)
    assert {"customers", "orders"} <= set(rig.labels())


def test_a_space_after_other_words_does_not_open_it(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT ")
    qtbot.wait(80)
    assert not rig.editor.completion.visible


def test_a_dot_opens_the_columns_of_the_alias(rig: Rig, qtbot: QtBot) -> None:
    rig.editor.setPlainText("SELECT  FROM orders o")
    cursor = rig.editor.textCursor()
    cursor.setPosition(7)
    rig.editor.setTextCursor(cursor)
    rig.type("o.")
    open_list(rig, qtbot)
    assert rig.labels() == ["customer_id", "id", "placed_at", "status", "total"]


def test_ctrl_space_forces_the_list(rig: Rig, qtbot: QtBot) -> None:
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    open_list(rig, qtbot)
    assert "SELECT" in rig.labels()
    assert rig.editor.text() == ""  # the key itself types nothing


def test_nothing_opens_in_strings_and_comments(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT 'sel")
    qtbot.wait(80)
    assert not rig.editor.completion.visible
    rig.editor.setPlainText("-- sel")
    cursor = rig.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    rig.editor.setTextCursor(cursor)
    rig.type("e")
    qtbot.wait(80)
    assert not rig.editor.completion.visible


def test_without_a_source_nothing_happens(rig: Rig, qtbot: QtBot) -> None:
    rig.editor.set_completion(None)
    rig.type("sel")
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(60)
    assert not rig.editor.completion.visible
    assert rig.editor.text() == "sel"


def test_it_works_without_a_schema(rig: Rig, qtbot: QtBot) -> None:
    rig.schema = None
    rig.type("sel")
    open_list(rig, qtbot)
    assert "SELECT" in rig.labels()


# ---------------------------------------------------------------------------- keys


def test_tab_accepts_the_selected_row(rig: Rig, qtbot: QtBot) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT * FROM "
    assert not rig.editor.completion.visible


def test_enter_accepts_too(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM cust")
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["customers"])
    rig.press(K.Key_Return)
    assert rig.editor.text() == "SELECT * FROM customers"
    assert rig.editor.document().blockCount() == 1


def test_escape_closes_without_changing_the_text(rig: Rig, qtbot: QtBot) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    rig.press(K.Key_Escape)
    assert not rig.editor.completion.visible
    assert rig.editor.text() == "sel"


def test_escape_before_the_list_opens_cancels_it(rig: Rig, qtbot: QtBot) -> None:
    rig.editor.completion.debounce_ms = 60
    rig.type("sel")
    rig.press(K.Key_Escape)
    qtbot.wait(150)
    assert not rig.editor.completion.visible


def test_escape_is_kept_from_menu_shortcuts_only_while_open(rig: Rig, qtbot: QtBot) -> None:
    controller = rig.editor.completion
    assert not controller.wants_key(K.Key_Escape)
    rig.type("sel")
    open_list(rig, qtbot)
    assert controller.wants_key(K.Key_Escape)
    event = QKeyEvent(QEvent.Type.ShortcutOverride, K.Key_Escape, Qt.KeyboardModifier.NoModifier)
    rig.editor.event(event)
    assert event.isAccepted()
    other = QKeyEvent(QEvent.Type.ShortcutOverride, K.Key_F5, Qt.KeyboardModifier.NoModifier)
    rig.editor.event(other)
    assert not other.isAccepted()


def test_arrows_move_the_selection_and_wrap(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM ")
    open_list(rig, qtbot)
    first = rig.popup.current()
    rig.press(K.Key_Down)
    second = rig.popup.current()
    assert second is not first
    assert rig.editor.textCursor().position() == len("SELECT * FROM ")  # the cursor stays put
    rig.press(K.Key_Up)
    assert rig.popup.current() == first
    rig.press(K.Key_Up)  # wraps to the last row
    assert rig.popup.current() == rig.popup.model.item(rig.popup.row_count() - 1)
    rig.press(K.Key_Down)
    assert rig.popup.current() == first
    rig.press(K.Key_Down)
    rig.press(K.Key_Tab)
    assert rig.editor.text().startswith("SELECT * FROM ")
    assert rig.editor.text() != "SELECT * FROM "


def test_page_keys_do_not_wrap(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM ")
    open_list(rig, qtbot)
    rig.press(K.Key_PageDown)
    assert rig.popup.view.currentIndex().row() == min(8, rig.popup.row_count() - 1)
    rig.press(K.Key_PageUp)
    rig.press(K.Key_PageUp)
    assert rig.popup.view.currentIndex().row() == 0


def test_tab_indents_and_enter_breaks_the_line_when_nothing_is_open(rig: Rig, qtbot: QtBot) -> None:
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "    "
    rig.press(K.Key_Return)
    assert rig.editor.document().blockCount() == 2


def test_ctrl_enter_does_not_accept(rig: Rig, qtbot: QtBot) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    rig.press(K.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not rig.editor.text().startswith("SELECT")


def test_moving_the_cursor_away_closes_the_list(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM cus")
    open_list(rig, qtbot)
    rig.press(K.Key_Left)
    assert not rig.editor.completion.visible
    rig.type("t")
    open_list(rig, qtbot)
    rig.press(K.Key_Home)
    assert not rig.editor.completion.visible


def test_punctuation_closes_the_list(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM cus")
    open_list(rig, qtbot)
    rig.type(",")
    assert not rig.editor.completion.visible


def test_losing_focus_closes_the_list(
    rig: Rig, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    monkeypatch.setattr(rig.popup, "isActiveWindow", lambda: False)  # focus went to another window
    monkeypatch.setattr(rig.editor, "hasFocus", lambda: False)
    rig.editor.completion.focus_lost()  # what focusOutEvent calls
    qtbot.waitUntil(lambda: not rig.editor.completion.visible, timeout=3000)


def test_switching_the_dialect_closes_the_list(rig: Rig, qtbot: QtBot) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    rig.editor.set_dialect(MYSQL)
    assert not rig.editor.completion.visible


# ---------------------------------------------------------------------------- accepting


def test_accepting_replaces_the_whole_word_under_the_cursor(rig: Rig, qtbot: QtBot) -> None:
    rig.editor.setPlainText("SELECT * FROM cust WHERE 1 = 1")
    cursor = rig.editor.textCursor()
    cursor.setPosition(len("SELECT * FROM cu"))
    rig.editor.setTextCursor(cursor)
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["customers"])
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT * FROM customers WHERE 1 = 1"
    assert rig.editor.textCursor().position() == len("SELECT * FROM customers")


def test_accepting_after_more_typing_than_the_list_knows(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM cu")
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["customers"])
    rig.editor.completion.debounce_ms = 5000  # the list for "cus" will not arrive in time
    rig.type("s")
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT * FROM customers"


def test_a_function_gets_parentheses_and_the_cursor_goes_inside(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT coun")
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: "COUNT" in rig.labels()[:3])
    while rig.popup.current().label != "COUNT":  # type: ignore[union-attr]
        rig.press(K.Key_Down)
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT COUNT()"
    assert rig.editor.textCursor().position() == len("SELECT COUNT(")


def test_a_snippet_places_the_cursor(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM orders o ij")
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["ij"])
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT * FROM orders o INNER JOIN  ON "
    assert rig.editor.textCursor().position() == len("SELECT * FROM orders o INNER JOIN ")


def test_an_alias_reopens_the_list_with_its_columns(rig: Rig, qtbot: QtBot) -> None:
    rig.editor.setPlainText("SELECT  FROM orders o")
    cursor = rig.editor.textCursor()
    cursor.setPosition(7)
    rig.editor.setTextCursor(cursor)
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: "o" in rig.labels())
    while rig.popup.current().label != "o":  # type: ignore[union-attr]
        rig.press(K.Key_Down)
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT o. FROM orders o"
    qtbot.waitUntil(lambda: rig.labels() == ["customer_id", "id", "placed_at", "status", "total"])


def test_the_join_condition_is_inserted_whole(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM orders o JOIN customers c ON ")
    open_list(rig, qtbot)
    assert rig.popup.current().kind is Kind.JOIN  # type: ignore[union-attr]
    rig.press(K.Key_Tab)
    assert rig.editor.text().endswith("ON o.customer_id = c.id")


def test_star_expansion_replaces_the_star(rig: Rig, qtbot: QtBot) -> None:
    rig.editor.setPlainText("SELECT * FROM customers")
    cursor = rig.editor.textCursor()
    cursor.setPosition(len("SELECT *"))
    rig.editor.setTextCursor(cursor)
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    open_list(rig, qtbot)
    assert rig.popup.current().kind is Kind.STAR  # type: ignore[union-attr]
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT id, name, email, created_at FROM customers"


def test_double_click_accepts(rig: Rig, qtbot: QtBot) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    rig.popup.view.doubleClicked.emit(rig.popup.model.index(0))
    assert rig.editor.text() == "SELECT * FROM "


def test_accepting_is_one_undo_step(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM cust")
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["customers"])
    rig.press(K.Key_Tab)
    assert rig.editor.text() == "SELECT * FROM customers"
    rig.editor.undo()
    assert rig.editor.text() == "SELECT * FROM cust"


# ---------------------------------------------------------------------------- settings, learning


def test_keyword_case_setting_is_used(rig: Rig, qtbot: QtBot) -> None:
    rig.case = KeywordCase.LOWER
    rig.type("SEL")
    open_list(rig, qtbot)
    assert "select" in rig.labels()
    rig.press(K.Key_Escape)
    rig.editor.setPlainText("")
    rig.case = KeywordCase.PRESERVE
    rig.type("sel")
    qtbot.waitUntil(lambda: rig.editor.completion.visible)
    assert "select" in rig.labels()


def test_accepting_is_counted_and_changes_the_next_ranking(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM ")
    open_list(rig, qtbot)
    assert rig.labels()[:1] == ["categories"]
    rig.press(K.Key_Escape)
    rig.editor.setPlainText("SELECT * FROM or")
    cursor = rig.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    rig.editor.setTextCursor(cursor)
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    open_list(rig, qtbot)
    qtbot.waitUntil(lambda: rig.labels()[:2] == ["order_items", "orders"])
    rig.press(K.Key_Down)
    rig.press(K.Key_Tab)  # orders
    assert rig.editor.text() == "SELECT * FROM orders"
    qtbot.waitUntil(lambda: rig.usage.counts("c1").get("table:public.orders") == 1)
    rig.editor.setPlainText("SELECT * FROM or")
    cursor = rig.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    rig.editor.setTextCursor(cursor)
    rig.press(K.Key_Space, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: rig.labels()[:1] == ["orders"])


def test_the_popup_never_takes_the_keyboard_focus(rig: Rig, qtbot: QtBot) -> None:
    rig.type("sel")
    open_list(rig, qtbot)
    assert rig.popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert rig.popup.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    rig.type("e")  # typing still reaches the editor
    assert rig.editor.text() == "sele"


def test_the_popup_stays_on_screen(rig: Rig, qtbot: QtBot) -> None:
    rig.type("SELECT * FROM ")
    open_list(rig, qtbot)
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen().availableGeometry()
    assert screen.contains(rig.popup.geometry().topLeft())
    assert rig.popup.width() >= 300
    assert rig.popup.view.height() <= 24 * 10 + 2
