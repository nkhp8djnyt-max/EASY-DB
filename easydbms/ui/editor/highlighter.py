"""Syntax highlighting driven by the dialect's lexer.

Each block is tokenised on its own. What a block leaves open (block comment, string, quoted
identifier, ``$$`` body) is stored as the block state and re-opened for the next block by
prepending its opener, so multi-line constructs highlight correctly without re-lexing the whole
document on every keystroke.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from ...core.dialects import Dialect, Token, TokenKind, tokenize
from ..theme import SyntaxColors, current_syntax

_NORMAL, _COMMENT, _STRING, _QUOTED, _DOLLAR = 0, 1, 2, 3, 4


def _format(color: str, *, italic: bool = False, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(color))
    if italic:
        fmt.setFontItalic(True)
    if bold:
        fmt.setFontWeight(QFont.Weight.DemiBold)
    return fmt


class SqlHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, dialect: Dialect) -> None:
        super().__init__(document)
        self._opener_by_block: dict[int, str] = {}
        self._set_dialect(dialect)
        self.set_colors(current_syntax())

    # ------------------------------------------------------------------ configuration

    def _set_dialect(self, dialect: Dialect) -> None:
        self._dialect = dialect
        self._functions = frozenset(f.name for f in dialect.functions)

    def set_dialect(self, dialect: Dialect) -> None:
        self._set_dialect(dialect)
        self._opener_by_block.clear()
        self.rehighlight()

    def set_colors(self, colors: SyntaxColors) -> None:
        self._formats = {
            "keyword": _format(colors.keyword, bold=True),
            "function": _format(colors.function),
            "string": _format(colors.string),
            "number": _format(colors.number),
            "comment": _format(colors.comment, italic=True),
            "ident": _format(colors.identifier),
            "param": _format(colors.parameter),
        }
        self._error = _format(colors.error)
        self._error.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
        self._error.setUnderlineColor(QColor(colors.error))

    # ------------------------------------------------------------------ Qt hook

    def highlightBlock(self, text: str) -> None:
        block = self.currentBlock().blockNumber()
        previous = self.previousBlockState()
        opener = self._opener_by_block.get(block - 1, "") if previous > _NORMAL else ""
        tokens = tokenize(opener + text, self._dialect)
        shift = len(opener)
        significant = [t for t in tokens if not t.is_trivia]
        next_is_paren = {
            id(t): significant[i + 1].text == "(" for i, t in enumerate(significant[:-1])
        }
        for token in tokens:
            fmt = self._format_for(token, next_is_paren.get(id(token), False))
            if fmt is None:
                continue
            start = max(token.start - shift, 0)
            length = token.end - shift - start
            if length > 0:
                self.setFormat(start, length, fmt)
        state, new_opener = self._end_state(tokens)
        if new_opener:
            self._opener_by_block[block] = new_opener
        else:
            self._opener_by_block.pop(block, None)
        self.setCurrentBlockState(state)

    # ------------------------------------------------------------------ helpers

    def _format_for(self, token: Token, before_paren: bool) -> QTextCharFormat | None:
        kind = token.kind
        if kind is TokenKind.WORD:
            upper = token.upper
            if before_paren and upper in self._functions:
                return self._formats["function"]
            if upper in self._dialect.keywords:
                return self._formats["keyword"]
            return None
        if kind is TokenKind.STRING:
            return self._formats["string"]
        if kind is TokenKind.NUMBER:
            return self._formats["number"]
        if kind in (TokenKind.LINE_COMMENT, TokenKind.BLOCK_COMMENT):
            return self._formats["comment"]
        if kind is TokenKind.QUOTED_IDENT:
            return self._formats["ident"]
        if kind in (TokenKind.PARAM, TokenKind.VARIABLE):
            return self._formats["param"]
        return None

    @staticmethod
    def _end_state(tokens: list[Token]) -> tuple[int, str]:
        """The block state after ``tokens`` and the text that re-opens the open construct."""
        if not tokens or tokens[-1].terminated:
            return _NORMAL, ""
        last = tokens[-1]
        text = last.text
        if last.kind is TokenKind.BLOCK_COMMENT:
            return _COMMENT, "/*"
        if last.kind is TokenKind.QUOTED_IDENT:
            return _QUOTED, text[0]
        if last.kind is TokenKind.STRING:
            if text.startswith("$"):
                tag = text[: text.index("$", 1) + 1]
                return _DOLLAR, tag
            return _STRING, text[: 2 if text[:1] in "Ee" and text[1:2] == "'" else 1]
        return _NORMAL, ""
