"""Autocomplete for one editor: when to ask, off-thread computation, the popup and its keys.

The text is analysed in a worker thread (a 150 ms pause after the last keystroke starts it); the
answer comes back through a queued signal and is dropped when the text moved on in the meantime.
The popup never takes the keyboard focus, so typing continues; ``handle_key`` lets the editor give
the list keys (Up/Down, Tab/Enter, Esc) to the popup while it is open.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor

from ...core.autocomplete import Completer, Completion, Completions, KeywordCase, Kind
from ...core.schema import DatabaseSchema
from .completion_popup import CompletionPopup

if TYPE_CHECKING:
    from .sql_editor import SqlEditor

DEBOUNCE_MS = 150
#: Words after which a space opens the list by itself (tables, ready ``ON`` conditions).
AUTO_AFTER_SPACE = frozenset({"FROM", "JOIN", "INTO", "UPDATE", "ON"})
_EXPANDING = frozenset({Kind.SNIPPET, Kind.FUNCTION, Kind.STAR, Kind.JOIN})  # typed text grows
_LAST_WORD = re.compile(r"(\w+)\s$")
_PAGE = 8


@dataclass(frozen=True, slots=True)
class CompletionSource:
    """Everything an editor needs to complete; supplied by whoever owns the connection."""

    completer: Completer
    #: Runs the analysis (a single worker keeps answers in order).
    executor: Executor
    schema: Callable[[], DatabaseSchema | None]
    keyword_case: Callable[[], KeywordCase]
    #: ``usage_key -> times accepted`` (asked on the GUI thread, read on the worker).
    counts: Callable[[], Mapping[str, int]]
    #: Count an accepted suggestion (called on the worker thread).
    record: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class _Answer:
    result: Completions
    text: str
    position: int
    forced: bool


class CompletionController(QObject):
    _ready = Signal(int, object)

    def __init__(self, editor: SqlEditor) -> None:
        super().__init__(editor)
        self._editor = editor
        self._source: CompletionSource | None = None
        self.debounce_ms = DEBOUNCE_MS
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._wanted_forced = False
        self._request = 0
        self._answer: _Answer | None = None
        self.popup = CompletionPopup(editor)
        self.popup.activated.connect(self.accept)
        self._ready.connect(self._on_ready)
        editor.cursorPositionChanged.connect(self._on_cursor_moved)

    # ------------------------------------------------------------------ state

    def set_source(self, source: CompletionSource | None) -> None:
        self._source = source
        self.hide()

    @property
    def enabled(self) -> bool:
        return self._source is not None

    @property
    def visible(self) -> bool:
        return self.popup.isVisible()

    def focus_lost(self) -> None:
        """The editor lost the keyboard focus: close the list unless it went to the list itself."""
        if self.visible:
            QTimer.singleShot(0, self._close_unless_in_popup)

    def _close_unless_in_popup(self) -> None:
        if not self._editor.hasFocus() and not self.popup.isActiveWindow():
            self.hide()

    def hide(self) -> None:
        self._timer.stop()
        self._request += 1  # whatever is being computed is not wanted any more
        self._answer = None
        if self.popup.isVisible():
            self.popup.hide()

    # ------------------------------------------------------------------ asking

    def schedule(self, *, forced: bool = False) -> None:
        """Ask for suggestions once typing pauses for :attr:`debounce_ms`."""
        if self._source is None:
            return
        self._wanted_forced = self._wanted_forced or forced
        self._timer.start(self.debounce_ms)

    def trigger(self, *, forced: bool = True) -> None:
        """Ask for suggestions right now (Ctrl+Space, or after accepting ``alias.``)."""
        self._timer.stop()
        self._wanted_forced = self._wanted_forced or forced
        self._fire()

    def _fire(self) -> None:
        source = self._source
        forced, self._wanted_forced = self._wanted_forced, False
        editor = self._editor
        if source is None or editor.textCursor().hasSelection():
            self.hide()
            return
        text = editor.toPlainText()
        position = editor.textCursor().position()
        dialect = editor.dialect
        self._request += 1
        request = self._request
        schema = source.schema()
        mode = source.keyword_case()
        counts = source.counts()

        def work() -> None:
            if request != self._request:  # a newer keystroke already replaced this question
                return
            result = source.completer.complete(
                text,
                position,
                dialect,
                schema,
                forced=forced,
                keyword_case=mode,
                frequency=lambda key: counts.get(key, 0),
            )
            with contextlib.suppress(RuntimeError):  # the editor was closed meanwhile
                self._ready.emit(request, _Answer(result, text, position, forced))

        self._submit(source, work)

    def _submit(self, source: CompletionSource, work: Callable[..., object], *args: object) -> None:
        try:
            source.executor.submit(work, *args)
        except RuntimeError:  # the worker was shut down with its workspace
            self.hide()

    def _on_ready(self, request: int, answer: _Answer) -> None:
        if request != self._request:
            return
        editor = self._editor
        if editor.textCursor().position() != answer.position or editor.toPlainText() != answer.text:
            return  # typing went on; its own question is on the way
        items = answer.result.items
        if not items or (
            not answer.forced
            and len(items) == 1
            and items[0].kind not in _EXPANDING
            and items[0].label.casefold() == answer.result.prefix.casefold()
        ):  # the only suggestion is what has been typed already: nothing to add
            self._answer = None
            self.popup.hide()
            return
        self._answer = answer
        self.popup.show_items(items, answer.result.prefix)
        cursor = QTextCursor(editor.document())
        cursor.setPosition(answer.result.replace_start)
        rect = editor.cursorRect(cursor)
        anchor = editor.viewport().mapToGlobal(QPoint(rect.left(), rect.top()))
        self.popup.place(anchor, rect.height())

    # ------------------------------------------------------------------ keys

    def handle_key(self, event: QKeyEvent) -> bool:
        """Give the list keys to the popup; ``True`` when the key was used up."""
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(
            modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if ctrl and key == Qt.Key.Key_Space and self.enabled:
            self.trigger(forced=True)
            return True
        if not self.visible:
            if key == Qt.Key.Key_Escape and self._timer.isActive():
                self.hide()  # a list that has not opened yet is cancelled too
            return False
        plain = not modifiers & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
            | Qt.KeyboardModifier.ShiftModifier
        )
        if key == Qt.Key.Key_Escape:
            self.hide()
            return True
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and not ctrl:
            self.popup.step(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.popup.step(_PAGE if key == Qt.Key.Key_PageDown else -_PAGE, wrap=False)
            return True
        if plain and key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.popup.current()
            if item is not None and self.accept(item):
                return True
            self.hide()  # nothing to insert: Tab indents and Enter breaks the line as usual
            return False
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            self.hide()
        return False

    def wants_key(self, key: int) -> bool:
        """Keys the editor must not hand to a menu shortcut (``Esc`` stops a query otherwise)."""
        return self.visible and key in (
            Qt.Key.Key_Escape,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        )

    def after_key(self, event: QKeyEvent) -> None:
        """React to a key the editor has just handled: decide whether to (re)open the list."""
        typed = event.text()
        key = event.key()
        if not self.enabled:
            return
        word_char = bool(typed) and (typed.isalnum() or typed in "_$")
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self.visible:
                self.schedule()
            return
        if word_char or typed == ".":
            self.schedule()
        elif typed == " ":
            if self._after_trigger_word():
                self.schedule()
            else:
                self.hide()
        elif typed and typed.isprintable():
            self.hide()  # punctuation, operators, brackets end the word

    def _after_trigger_word(self) -> bool:
        editor = self._editor
        cursor = editor.textCursor()
        before = editor.toPlainText()[: cursor.position()]
        match = _LAST_WORD.search(before)
        return match is not None and match.group(1).upper() in AUTO_AFTER_SPACE

    # ------------------------------------------------------------------ accepting

    def accept(self, item: Completion) -> bool:
        """Insert ``item`` over the word being typed; ``False`` when the text no longer fits."""
        answer = self._answer
        editor = self._editor
        source = self._source
        if answer is None or source is None:
            self.hide()
            return False
        start, end = item.replace or (answer.result.replace_start, answer.result.replace_end)
        text = editor.toPlainText()
        position = editor.textCursor().position()
        if text != answer.text:
            # typed on since the list was made: only plain typing inside the word is understood
            delta = len(text) - len(answer.text)
            end += delta
            fits = (
                item.replace is None
                and text[:start] == answer.text[:start]
                and start <= position <= end <= len(text)
            )
            if not fits:
                self.hide()
                return False
        cursor = editor.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(item.insert)
        if item.cursor_back:
            cursor.movePosition(
                QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, item.cursor_back
            )
        cursor.endEditBlock()
        self.hide()
        editor.setTextCursor(cursor)
        if item.usage_key:
            self._submit(source, source.record, item.usage_key)
        if item.retrigger:
            self.trigger(forced=False)
        return True

    def _on_cursor_moved(self) -> None:
        answer = self._answer
        if answer is None or not self.visible:
            return
        editor = self._editor
        delta = len(editor.toPlainText()) - len(answer.text)
        start, end = answer.result.replace_start, answer.result.replace_end + delta
        if not start <= editor.textCursor().position() <= end:
            self.hide()
