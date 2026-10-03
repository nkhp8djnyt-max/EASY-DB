"""Split an editor buffer into statements and find the one under the cursor.

Splitting is lexical (see :mod:`.lexer`), so semicolons inside strings, comments, quoted
identifiers and PostgreSQL ``$$`` bodies are not terminators. Stored-program bodies
(``CREATE TRIGGER/PROCEDURE/FUNCTION/EVENT ... BEGIN ... END``) are kept whole by tracking
``BEGIN`` / ``CASE`` ... ``END`` nesting, which covers SQLite triggers, MySQL routines and
PostgreSQL ``BEGIN ATOMIC`` bodies without needing a ``DELIMITER`` command.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import simd
from .base import Dialect
from .lexer import Token, TokenKind, tokenize

_COMPOUND_OBJECTS = frozenset({"TRIGGER", "PROCEDURE", "FUNCTION", "EVENT"})
#: ``END IF`` / ``END LOOP`` / ... close constructs that were never counted as an opener.
_UNCOUNTED_END_SUFFIXES = frozenset({"IF", "LOOP", "WHILE", "REPEAT"})
#: The object kind of ``CREATE [DEFINER=... | OR REPLACE | TEMP ...] TRIGGER`` shows up early.
_COMPOUND_LOOKAHEAD = 24


@dataclass(frozen=True, slots=True)
class Statement:
    """One executable statement of a script."""

    #: Offsets into the original text; ``end`` is exclusive and includes the ``;``. ``start`` is
    #: the first non-whitespace character, which may be a comment attached to the statement.
    start: int
    end: int
    #: Statement text stripped of surrounding whitespace, terminator included.
    text: str
    #: Same without the trailing ``;`` (what a client should send to the server).
    body: str
    #: First significant token upper-cased (``SELECT``, ``CREATE``, ``(``), ``""`` otherwise.
    keyword: str


def split_statements(sql: str, dialect: Dialect) -> list[Statement]:
    """Return the non-empty statements of ``sql`` (comment-only fragments are dropped)."""
    native = _split_native(sql, dialect)
    if native is not None:
        return native
    return _split_python(sql, dialect)


def _split_native(sql: str, dialect: Dialect) -> list[Statement] | None:
    """The C splitter's answer, or ``None`` when it is unavailable or declines this input."""
    if not simd.enabled():
        return None
    flags = _native_flags(dialect)
    if flags is None:
        return None
    rows = simd.split_raw(sql, flags)
    if rows is None:
        return None
    return [
        _statement(
            sql,
            begin,
            end,
            _keyword(sql, first_start, first_end, first_kind),
            terminated=bool(terminated),
        )
        for begin, end, first_start, first_end, terminated, first_kind in rows
    ]


def _keyword(sql: str, start: int, end: int, kind: int) -> str:
    if kind == 1:
        return sql[start:end].upper()
    return "(" if kind == 2 else ""


_FLAGS_BY_DIALECT: dict[str, int | None] = {}


def _native_flags(dialect: Dialect) -> int | None:
    key = dialect.id.value
    if key not in _FLAGS_BY_DIALECT:
        _FLAGS_BY_DIALECT[key] = simd.flags_for(dialect.lexer)
    return _FLAGS_BY_DIALECT[key]


def _split_python(sql: str, dialect: Dialect) -> list[Statement]:
    """Reference implementation; also what runs when the native splitter is not available."""
    tokens = tokenize(sql, dialect)
    statements: list[Statement] = []
    begin = 0  # where the statement being collected starts
    first_sig: int | None = None  # index of its first significant token
    sig_count = 0
    depth = 0
    compound = False
    skip_next_case = False

    for index, token in enumerate(tokens):
        if token.is_trivia:
            continue
        if first_sig is None:
            first_sig = index
            sig_count = depth = 0
            compound = skip_next_case = False
        sig_count += 1

        if token.kind is TokenKind.PUNCT and token.text == ";":
            if depth > 0:
                continue
            if sig_count > 1:
                statements.append(_build(sql, tokens[first_sig], begin, token.end, terminated=True))
            begin = token.end  # a lone ";" is an empty statement: drop it
            first_sig = None
            continue

        if token.kind is not TokenKind.WORD:
            continue
        if not compound and sig_count <= _COMPOUND_LOOKAHEAD:
            compound = _starts_compound(tokens, first_sig, index)
        if not compound:
            continue

        if skip_next_case:
            skip_next_case = False
            if token.upper == "CASE":  # the CASE of ``END CASE`` closes the one counted earlier
                continue
        word = token.upper
        if word in ("BEGIN", "CASE"):
            depth += 1
        elif word == "END" and _next_significant_word(tokens, index) not in _UNCOUNTED_END_SUFFIXES:
            depth -= 1
            skip_next_case = True

    if first_sig is not None:
        statements.append(_build(sql, tokens[first_sig], begin, len(sql), terminated=False))
    return statements


def leading_keyword(sql: str, dialect: Dialect) -> str:
    """Upper-case first significant token of ``sql`` (a word or ``(``), ``""`` if there is none."""
    for token in tokenize(sql[:512], dialect):
        if token.is_trivia:
            continue
        return token.upper if token.kind is TokenKind.WORD or token.text == "(" else ""
    return ""


def statement_at(sql: str, offset: int, dialect: Dialect) -> Statement | None:
    """The statement a cursor at ``offset`` refers to (what Ctrl+Enter should run).

    * Inside a statement, in a comment directly above it, or right after its ``;``: that statement.
    * On blank space between two statements: the preceding one.
    * Before the first statement: the first statement.
    """
    statements = split_statements(sql, dialect)
    if not statements:
        return None
    offset = max(0, min(offset, len(sql)))
    previous: Statement | None = None
    for statement in statements:
        if offset < statement.start:
            return previous or statement
        if offset <= statement.end:
            return statement
        previous = statement
    return previous


def _build(sql: str, first: Token, begin: int, end: int, *, terminated: bool) -> Statement:
    keyword = first.upper if first.kind is TokenKind.WORD or first.text == "(" else ""
    return _statement(sql, begin, end, keyword, terminated=terminated)


def _statement(sql: str, begin: int, end: int, keyword: str, *, terminated: bool) -> Statement:
    raw = sql[begin:end]
    text = raw.strip()
    return Statement(
        start=begin + (len(raw) - len(raw.lstrip())),
        end=end,
        text=text,
        # Only a real terminator is dropped: a trailing "-- note;" is a comment, not a ";".
        body=text[:-1].rstrip() if terminated else text,
        keyword=keyword,
    )


def _starts_compound(tokens: list[Token], first_sig: int, current: int) -> bool:
    """Is the statement beginning at ``first_sig`` a ``CREATE ... TRIGGER|PROCEDURE|...``?

    Evaluated at each leading word until it becomes true; the object kind is always one of the
    first few words, before any ``(``.
    """
    if tokens[first_sig].upper != "CREATE" or tokens[current].upper not in _COMPOUND_OBJECTS:
        return False
    return not any(
        token.kind is TokenKind.PUNCT and token.text == "(" for token in tokens[first_sig:current]
    )


def _next_significant_word(tokens: list[Token], index: int) -> str:
    for token in tokens[index + 1 :]:
        if token.is_trivia:
            continue
        return token.upper if token.kind is TokenKind.WORD else ""
    return ""
