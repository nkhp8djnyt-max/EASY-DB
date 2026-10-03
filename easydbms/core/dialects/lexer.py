"""A forgiving, dialect-aware SQL lexer.

Unlike a parser it never fails: unterminated strings, quoted identifiers and comments simply run to
the end of input and are flagged ``terminated=False``. That makes it safe for half-typed editor
content, which is what syntax highlighting, statement splitting and the autocomplete token
fallback all deal with. The tokens are contiguous and cover the whole input, so
``"".join(t.text for t in tokenize(sql, d)) == sql`` always holds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .base import Dialect, IdentQuote, LexerSpec


class TokenKind(StrEnum):
    WHITESPACE = "whitespace"
    LINE_COMMENT = "line_comment"
    BLOCK_COMMENT = "block_comment"
    STRING = "string"
    QUOTED_IDENT = "quoted_ident"
    WORD = "word"
    NUMBER = "number"
    PARAM = "param"
    VARIABLE = "variable"
    OPERATOR = "operator"
    PUNCT = "punct"


_TRIVIA = frozenset({TokenKind.WHITESPACE, TokenKind.LINE_COMMENT, TokenKind.BLOCK_COMMENT})


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    text: str
    start: int
    #: Exclusive end offset.
    end: int
    #: ``False`` for a string / quoted identifier / block comment that hits end of input.
    terminated: bool = True

    @property
    def is_trivia(self) -> bool:
        return self.kind in _TRIVIA

    @property
    def upper(self) -> str:
        return self.text.upper()


_WORD_RE = re.compile(r"[^\W\d][\w$]*")
_NUMBER_RE = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d[\d_]*(?:\.\d*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?")
_DOLLAR_NUMERIC_RE = re.compile(r"\$\d+")
_DOLLAR_NAMED_RE = re.compile(r"\$\w+")
_DOLLAR_TAG_RE = re.compile(r"\$(?:[^\W\d]\w*)?\$")
_COLON_PARAM_RE = re.compile(r":[^\W\d]\w*")
_QUESTION_PARAM_RE = re.compile(r"\?\d*")
_AT_VARIABLE_RE = re.compile(r"@@?[\w.$]+")
_AT_PARAM_RE = re.compile(r"@\w+")

_PUNCT = frozenset("(),;.[]")
_BASE_OPERATOR_CHARS = "+-*/<>=!~|&^%"


def tokenize(sql: str, dialect: Dialect) -> list[Token]:
    """Split ``sql`` into contiguous tokens following ``dialect``'s lexical rules."""
    spec = dialect.lexer
    operator_chars = _operator_chars(spec)
    tokens: list[Token] = []
    n = len(sql)
    i = 0
    while i < n:
        kind, end, terminated = _scan(sql, i, n, spec, operator_chars)
        tokens.append(Token(kind, sql[i:end], i, end, terminated))
        i = end
    return tokens


def _operator_chars(spec: LexerSpec) -> frozenset[str]:
    chars = set(_BASE_OPERATOR_CHARS)
    # ``#``, ``@`` and ``?`` are operator characters only where they cannot start something else.
    if "#" not in spec.line_comments:
        chars.add("#")
    if not (spec.at_params or spec.at_variables):
        chars.add("@")
    if not spec.question_params:
        chars.add("?")
    return frozenset(chars)


def _scan(
    sql: str, i: int, n: int, spec: LexerSpec, operator_chars: frozenset[str]
) -> tuple[TokenKind, int, bool]:
    c = sql[i]

    if c.isspace():
        j = i + 1
        while j < n and sql[j].isspace():
            j += 1
        return TokenKind.WHITESPACE, j, True

    end = _line_comment_end(sql, i, n, spec)
    if end is not None:
        return TokenKind.LINE_COMMENT, end, True

    if sql.startswith("/*", i):
        end, terminated = _block_comment_end(sql, i, n, spec.nested_block_comments)
        return TokenKind.BLOCK_COMMENT, end, terminated

    if c == "'" or (c == '"' and spec.double_quote_is_string):
        end, terminated = _quoted_end(
            sql, i, n, c, c, doubled=True, backslash=spec.backslash_escapes
        )
        return TokenKind.STRING, end, terminated

    for quote in spec.identifier_quotes:
        if sql.startswith(quote.open, i):
            end, terminated = _ident_end(sql, i, n, quote)
            return TokenKind.QUOTED_IDENT, end, terminated

    if c.isdigit() or (c == "." and i + 1 < n and sql[i + 1].isdigit()):
        match = _NUMBER_RE.match(sql, i)
        if match:
            return TokenKind.NUMBER, match.end(), True

    if c.isalpha() or c == "_" or ord(c) > 127:
        match = _WORD_RE.match(sql, i)
        if match is None:  # non-letter code point above ASCII; treat as an operator
            return TokenKind.OPERATOR, i + 1, True
        end = match.end()
        if spec.escape_string_prefix and sql[i:end] in ("E", "e") and sql.startswith("'", end):
            end, terminated = _quoted_end(sql, end, n, "'", "'", doubled=True, backslash=True)
            return TokenKind.STRING, end, terminated
        return TokenKind.WORD, end, True

    if c == "$":
        if spec.dollar_numeric_params and (match := _DOLLAR_NUMERIC_RE.match(sql, i)):
            return TokenKind.PARAM, match.end(), True
        if spec.dollar_quoting and (match := _DOLLAR_TAG_RE.match(sql, i)):
            tag = match.group(0)
            close = sql.find(tag, match.end())
            if close == -1:
                return TokenKind.STRING, n, False
            return TokenKind.STRING, close + len(tag), True
        if spec.dollar_named_params and (match := _DOLLAR_NAMED_RE.match(sql, i)):
            return TokenKind.PARAM, match.end(), True

    if c == "?" and spec.question_params:
        match = _QUESTION_PARAM_RE.match(sql, i)
        assert match is not None
        return TokenKind.PARAM, match.end(), True

    if c == ":":
        if sql.startswith("::", i):
            return TokenKind.OPERATOR, i + 2, True
        if spec.colon_params and (match := _COLON_PARAM_RE.match(sql, i)):
            return TokenKind.PARAM, match.end(), True
        return TokenKind.OPERATOR, i + 1, True

    if c == "@":
        if spec.at_variables and (match := _AT_VARIABLE_RE.match(sql, i)):
            return TokenKind.VARIABLE, match.end(), True
        if spec.at_params and (match := _AT_PARAM_RE.match(sql, i)):
            return TokenKind.PARAM, match.end(), True

    if c in _PUNCT:
        return TokenKind.PUNCT, i + 1, True

    j = i + 1
    while j < n and sql[j] in operator_chars and not _starts_comment(sql, j, spec):
        j += 1
    return TokenKind.OPERATOR, j, True


def _starts_comment(sql: str, i: int, spec: LexerSpec) -> bool:
    return sql.startswith("/*", i) or _line_comment_end(sql, i, len(sql), spec) is not None


def _line_comment_end(sql: str, i: int, n: int, spec: LexerSpec) -> int | None:
    """End offset of a line comment starting at ``i``, or ``None`` if none starts there."""
    for prefix in spec.line_comments:
        if not sql.startswith(prefix, i):
            continue
        if prefix == "--" and spec.dash_comment_needs_space:
            following = sql[i + 2 : i + 3]
            if following and not following.isspace() and ord(following) >= 32:
                continue
        newline = sql.find("\n", i)
        return n if newline == -1 else newline
    return None


def _block_comment_end(sql: str, i: int, n: int, nested: bool) -> tuple[int, bool]:
    depth = 1
    j = i + 2
    while j < n:
        if sql.startswith("*/", j):
            depth -= 1
            j += 2
            if depth == 0:
                return j, True
        elif nested and sql.startswith("/*", j):
            depth += 1
            j += 2
        else:
            j += 1
    return n, False


def _quoted_end(
    sql: str, i: int, n: int, open_: str, close: str, *, doubled: bool, backslash: bool
) -> tuple[int, bool]:
    """Scan a quoted run starting at ``i`` (which holds ``open_``)."""
    j = i + len(open_)
    while j < n:
        if backslash and sql[j] == "\\":
            j += 2
            continue
        if sql.startswith(close, j):
            if doubled and sql.startswith(close, j + len(close)):
                j += 2 * len(close)
                continue
            return j + len(close), True
        j += 1
    return n, False


def _ident_end(sql: str, i: int, n: int, quote: IdentQuote) -> tuple[int, bool]:
    return _quoted_end(sql, i, n, quote.open, quote.close, doubled=quote.doubled, backslash=False)
