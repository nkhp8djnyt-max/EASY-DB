from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from pytestqt.qtbot import QtBot

from sql_erd_studio.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect
from sql_erd_studio.ui.editor import SqlEditor
from sql_erd_studio.ui.theme import current_syntax


@pytest.fixture
def editor(qtbot: QtBot) -> SqlEditor:
    widget = SqlEditor(POSTGRESQL)
    qtbot.addWidget(widget)
    widget.resize(600, 300)
    widget.show()
    return widget


def spans(editor: SqlEditor, block: int = 0) -> list[tuple[str, str]]:
    """``(text, foreground colour name)`` of the highlighted ranges of a block."""
    layout = editor.document().findBlockByNumber(block).layout()
    assert layout is not None
    text = editor.document().findBlockByNumber(block).text()
    return [
        (text[r.start : r.start + r.length], r.format.foreground().color().name())
        for r in layout.formats()
    ]


def press(
    editor: SqlEditor, key: Qt.Key, modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
) -> None:
    editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


# ---------------------------------------------------------------------------- highlighting


def test_keywords_functions_strings_numbers_and_comments_get_colours(editor: SqlEditor) -> None:
    editor.setPlainText("SELECT count(*), 'a;b', 42 FROM t -- tail")
    colors = current_syntax()
    found = dict(spans(editor))
    assert found["SELECT"] == colors.keyword
    assert found["FROM"] == colors.keyword
    assert found["count"] == colors.function
    assert found["'a;b'"] == colors.string
    assert found["42"] == colors.number
    assert found["-- tail"] == colors.comment
    assert "t" not in found  # plain identifiers keep the default colour


def test_a_function_name_without_a_call_is_not_a_function(editor: SqlEditor) -> None:
    editor.setPlainText("SELECT count FROM t")
    assert "count" not in dict(spans(editor))


def test_multi_line_block_comments_and_strings_carry_over(editor: SqlEditor) -> None:
    editor.setPlainText("/* first\nsecond */ SELECT 1;\nSELECT 'open\nstill open' , 2")
    colors = current_syntax()
    assert ("first", colors.comment) in spans(editor, 0)[-1:] or spans(editor, 0)[0][
        1
    ] == colors.comment
    second = dict(spans(editor, 1))
    assert second["second */"] == colors.comment
    assert second["SELECT"] == colors.keyword  # the comment ended, so normal highlighting resumed
    assert dict(spans(editor, 3))["still open'"] == colors.string
    assert dict(spans(editor, 3))["2"] == colors.number


def test_dollar_quoted_bodies_span_lines_in_postgresql(editor: SqlEditor) -> None:
    editor.setPlainText("SELECT $f$ body\nSELECT 1 $f$, 7")
    colors = current_syntax()
    assert dict(spans(editor, 1))["SELECT 1 $f$"] == colors.string
    assert dict(spans(editor, 1))["7"] == colors.number


def test_switching_dialect_re_highlights(editor: SqlEditor) -> None:
    editor.setPlainText("SELECT `a` FROM t # note")
    editor.set_dialect(MYSQL)
    colors = current_syntax()
    found = dict(spans(editor))
    assert found["`a`"] == colors.identifier
    assert found["# note"] == colors.comment
    editor.set_dialect(
        POSTGRESQL
    )  # "#" is no comment in PostgreSQL, backticks are not quotes there
    assert "# note" not in dict(spans(editor))


# ---------------------------------------------------------------------------- statements


@pytest.mark.parametrize("dialect", [POSTGRESQL, MYSQL, SQLITE], ids=lambda d: d.id.value)
def test_statement_under_the_cursor(qtbot: QtBot, dialect: Dialect) -> None:
    editor = SqlEditor(dialect)
    qtbot.addWidget(editor)
    editor.setPlainText("select 1;\n\nselect 2;\nselect 3")
    for offset, expected in ((3, "select 1;"), (12, "select 2;"), (25, "select 3")):
        editor.go_to(offset)
        (found,) = editor.statements_to_run()
        assert found.text == expected


def test_a_selection_is_split_into_statements_with_absolute_offsets(editor: SqlEditor) -> None:
    editor.setPlainText("select 0;\nselect 1; select 2;\nselect 3")
    cursor = editor.textCursor()
    cursor.setPosition(10)
    cursor.setPosition(30, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    statements = editor.statements_to_run()
    assert [s.text for s in statements] == ["select 1;", "select 2;"]
    for statement in statements:
        assert editor.text()[statement.start : statement.end].strip() == statement.text


def test_an_empty_editor_has_nothing_to_run(editor: SqlEditor) -> None:
    assert editor.statements_to_run() == []
    assert editor.all_statements() == []


def test_all_statements(editor: SqlEditor) -> None:
    editor.setPlainText("select 1; select 'a;b'; select 3")
    assert [s.body for s in editor.all_statements()] == ["select 1", "select 'a;b'", "select 3"]


def test_go_to_moves_and_clamps_the_cursor(editor: SqlEditor) -> None:
    editor.setPlainText("select 1")
    editor.go_to(4)
    assert editor.textCursor().position() == 4
    editor.go_to(999)
    assert editor.textCursor().position() == 8
    editor.go_to(-5)
    assert editor.textCursor().position() == 0


# ---------------------------------------------------------------------------- editing


def test_toggle_comment_on_the_current_line_and_back(editor: SqlEditor) -> None:
    editor.setPlainText("select 1\nselect 2")
    editor.go_to(2)
    press(editor, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
    assert editor.text() == "-- select 1\nselect 2"
    press(editor, Qt.Key.Key_Slash, Qt.KeyboardModifier.ControlModifier)
    assert editor.text() == "select 1\nselect 2"


def test_toggle_comment_on_a_selection_keeps_indentation_and_blank_lines(editor: SqlEditor) -> None:
    editor.setPlainText("  a\n\n    b\nc")
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)
    editor.toggle_comment()
    lines = editor.text().split("\n")
    assert [line.lstrip().startswith("-- ") for line in lines if line.strip()] == [True, True, True]
    assert lines[1] == ""
    editor.toggle_comment()
    assert editor.text() == "  a\n\n    b\nc"


def test_a_partly_commented_selection_is_commented_not_uncommented(editor: SqlEditor) -> None:
    editor.setPlainText("-- a\nb")
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)
    editor.toggle_comment()
    assert editor.text() == "-- -- a\n-- b"


def test_tab_inserts_spaces_and_shift_tab_removes_them(editor: SqlEditor) -> None:
    editor.setPlainText("a")
    editor.go_to(0)
    press(editor, Qt.Key.Key_Tab)
    assert editor.text() == "    a"
    press(editor, Qt.Key.Key_Backtab)
    assert editor.text() == "a"


def test_tab_on_a_selection_indents_every_line(editor: SqlEditor) -> None:
    editor.setPlainText("a\nb\nc")
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)
    press(editor, Qt.Key.Key_Tab)
    assert editor.text() == "    a\n    b\n    c"
    press(editor, Qt.Key.Key_Backtab)
    assert editor.text() == "a\nb\nc"


# ---------------------------------------------------------------------------- chrome


def test_line_number_gutter_grows_with_the_document(editor: SqlEditor) -> None:
    narrow = editor.line_number_width()
    editor.setPlainText("\n".join("x" for _ in range(1500)))
    assert editor.line_number_width() > narrow
    assert editor.viewportMargins().left() == editor.line_number_width()


def test_the_current_line_and_matching_brackets_are_marked(editor: SqlEditor) -> None:
    editor.setPlainText("select (1 + (2 * 3))")
    editor.go_to(7)  # right after the first "("
    marked = [s.cursor.selectedText() for s in editor.extraSelections() if s.cursor.hasSelection()]
    assert marked == ["(", ")"]
    positions = sorted(
        s.cursor.selectionStart() for s in editor.extraSelections() if s.cursor.hasSelection()
    )
    assert positions == [7, 19]
    editor.go_to(0)
    assert not [s for s in editor.extraSelections() if s.cursor.hasSelection()]


def test_brackets_inside_strings_do_not_count(editor: SqlEditor) -> None:
    editor.setPlainText("select ')' , (1)")
    editor.go_to(13)
    positions = sorted(
        s.cursor.selectionStart() for s in editor.extraSelections() if s.cursor.hasSelection()
    )
    assert positions == [13, 15]


def test_theme_refresh_keeps_working(editor: SqlEditor) -> None:
    editor.setPlainText("select 1")
    editor.refresh_theme()
    assert dict(spans(editor))["select"] == current_syntax().keyword
