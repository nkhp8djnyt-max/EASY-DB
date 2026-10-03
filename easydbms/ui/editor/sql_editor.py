"""The SQL text editor: line numbers, current line, bracket matching, comment toggle."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFontDatabase,
    QHideEvent,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from ...core.dialects import Dialect, Statement, TokenKind, split_statements, statement_at, tokenize
from ..theme import current_syntax, current_tokens
from .completion import CompletionController, CompletionSource
from .highlighter import SqlHighlighter

_BRACKET_SCAN_LIMIT = 50_000
_INDENT = "    "
_PARAGRAPH_SEPARATOR = chr(0x2029)  # what QTextCursor.selectedText() uses for newlines


class _LineNumbers(QWidget):
    def __init__(self, editor: SqlEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self._editor.paint_line_numbers(event)


class SqlEditor(QPlainTextEdit):
    def __init__(self, dialect: Dialect, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dialect = dialect
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * len(_INDENT))
        self._numbers = _LineNumbers(self)
        self._highlighter = SqlHighlighter(self.document(), dialect)
        self.completion = CompletionController(self)
        self.blockCountChanged.connect(self._update_number_width)
        self.updateRequest.connect(self._scroll_numbers)
        self.cursorPositionChanged.connect(self._update_selections)
        self._update_number_width()
        self._update_selections()

    # ------------------------------------------------------------------ dialect and theme

    @property
    def dialect(self) -> Dialect:
        return self._dialect

    def set_dialect(self, dialect: Dialect) -> None:
        self._dialect = dialect
        self._highlighter.set_dialect(dialect)
        self.completion.hide()

    def refresh_theme(self) -> None:
        self._highlighter.set_colors(current_syntax())
        self._highlighter.rehighlight()
        self._update_selections()
        self._numbers.update()

    # ------------------------------------------------------------------ autocomplete

    def set_completion(self, source: CompletionSource | None) -> None:
        """Turn autocomplete on with ``source`` (``None`` turns it off)."""
        self.completion.set_source(source)

    def complete(self) -> None:
        """Open the suggestions now, even where they would not appear by themselves."""
        self.completion.trigger(forced=True)

    # ------------------------------------------------------------------ statements

    def text(self) -> str:
        return self.toPlainText()

    def statements_to_run(self) -> list[Statement]:
        """The selection (split into statements) or, without one, the statement at the cursor."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            return [
                Statement(
                    start + s.start,
                    start + s.end,
                    s.text,
                    s.body,
                    s.keyword,
                )
                for s in split_statements(self._selected_text(cursor), self._dialect)
            ]
        found = statement_at(self.text(), cursor.position(), self._dialect)
        return [found] if found is not None else []

    @staticmethod
    def _selected_text(cursor: QTextCursor) -> str:
        return cursor.selectedText().replace(_PARAGRAPH_SEPARATOR, chr(10))

    def all_statements(self) -> list[Statement]:
        return split_statements(self.text(), self._dialect)

    def go_to(self, offset: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(max(0, min(offset, len(self.text()))))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()

    # ------------------------------------------------------------------ line numbers

    def line_number_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_number_width(self, _: int = 0) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _scroll_numbers(self, rect: QRect, dy: int) -> None:
        if dy:
            self._numbers.scroll(0, dy)
        else:
            self._numbers.update(0, rect.y(), self._numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_number_width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        area = self.contentsRect()
        self._numbers.setGeometry(
            QRect(area.left(), area.top(), self.line_number_width(), area.height())
        )

    def paint_line_numbers(self, event: QPaintEvent) -> None:
        tokens = current_tokens()
        painter = QPainter(self._numbers)
        painter.fillRect(event.rect(), QColor(tokens.panel))
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(tokens.text if number == current else tokens.text_muted))
                painter.drawText(
                    0, top, self._numbers.width() - 8, height,
                    Qt.AlignmentFlag.AlignRight, str(number + 1),
                )  # fmt: skip
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1

    # ------------------------------------------------------------------ current line, brackets

    def _update_selections(self) -> None:
        colors = current_syntax()
        selections: list[QTextEdit.ExtraSelection] = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(colors.current_line))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        selections.append(line)
        pair = self._matching_brackets()
        if pair is not None:
            for position in pair:
                mark = QTextEdit.ExtraSelection()
                mark.format.setBackground(QColor(colors.bracket))
                mark.cursor = self.textCursor()
                mark.cursor.setPosition(position)
                mark.cursor.setPosition(position + 1, QTextCursor.MoveMode.KeepAnchor)
                selections.append(mark)
        self.setExtraSelections(selections)

    def _matching_brackets(self) -> tuple[int, int] | None:
        text = self.text()
        if len(text) > _BRACKET_SCAN_LIMIT:
            return None
        position = self.textCursor().position()
        wanted = {position, position - 1}
        stack: list[int] = []
        found: dict[int, int] = {}
        for token in tokenize(text, self._dialect):
            if token.kind is not TokenKind.PUNCT or token.text not in "()":
                continue
            if token.text == "(":
                stack.append(token.start)
            elif stack:
                opener = stack.pop()
                found[opener] = token.start
                found[token.start] = opener
        for candidate in wanted:
            if candidate in found:
                return candidate, found[candidate]
        return None

    # ------------------------------------------------------------------ editing

    def event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and self.completion.wants_key(event.key())
        ):
            event.accept()  # Esc closes the list instead of stopping the query
            return True
        return super().event(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.completion.focus_lost()
        super().focusOutEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        self.completion.hide()
        super().hideEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.completion.handle_key(event):
            return
        self._edit_key(event)
        self.completion.after_key(event)

    def _edit_key(self, event: QKeyEvent) -> None:
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and event.key() == Qt.Key.Key_Slash:
            self.toggle_comment()
            return
        if event.key() == Qt.Key.Key_Tab and not ctrl:
            self._indent(+1)
            return
        if event.key() == Qt.Key.Key_Backtab:
            self._indent(-1)
            return
        super().keyPressEvent(event)

    def toggle_comment(self) -> None:
        """Comment or uncomment the selected lines (or the current one) with ``-- ``."""
        cursor = self.textCursor()
        first, last = self._selected_blocks(cursor)
        blocks = [self.document().findBlockByNumber(n) for n in range(first, last + 1)]
        texts = [b.text() for b in blocks]
        filled = [t for t in texts if t.strip()]
        if not filled:
            return
        uncomment = all(t.lstrip().startswith("--") for t in filled)
        indent = min(len(t) - len(t.lstrip()) for t in filled)
        cursor.beginEditBlock()
        for block, text in zip(blocks, texts, strict=True):
            if not text.strip():
                continue
            edit = QTextCursor(block)
            if uncomment:
                start = len(text) - len(text.lstrip())
                width = 3 if text[start : start + 3] == "-- " else 2
                edit.setPosition(block.position() + start)
                edit.setPosition(block.position() + start + width, QTextCursor.MoveMode.KeepAnchor)
                edit.removeSelectedText()
            else:
                edit.setPosition(block.position() + indent)
                edit.insertText("-- ")
        cursor.endEditBlock()

    def _indent(self, direction: int) -> None:
        cursor = self.textCursor()
        if direction > 0 and not cursor.hasSelection():
            cursor.insertText(_INDENT)
            return
        first, last = self._selected_blocks(cursor)
        cursor.beginEditBlock()
        for number in range(first, last + 1):
            block = self.document().findBlockByNumber(number)
            edit = QTextCursor(block)
            if direction > 0:
                edit.insertText(_INDENT)
            else:
                text = block.text()
                remove = min(len(_INDENT), len(text) - len(text.lstrip(" ")))
                edit.setPosition(block.position())
                edit.setPosition(block.position() + remove, QTextCursor.MoveMode.KeepAnchor)
                edit.removeSelectedText()
        cursor.endEditBlock()

    def _selected_blocks(self, cursor: QTextCursor) -> tuple[int, int]:
        document = self.document()
        first = document.findBlock(cursor.selectionStart()).blockNumber()
        end = cursor.selectionEnd()
        last = document.findBlock(end).blockNumber()
        if cursor.hasSelection() and document.findBlock(end).position() == end and last > first:
            last -= 1  # a selection ending at the start of a line does not include that line
        return first, last
