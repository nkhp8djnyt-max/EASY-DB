"""Syntax checking and formatting on top of the lexer and ``sqlglot``.

``sqlglot`` is lenient (unknown functions and many odd constructs parse fine), so
:func:`check_syntax` reports what is *certainly* wrong: unterminated literals/comments and
structurally invalid statements. It is meant for editor hints, not as a substitute for the server.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect as SqlglotDialect
from sqlglot.errors import ErrorLevel, ParseError, SqlglotError, TokenError
from sqlglot.generator import Generator

from .base import Dialect
from .lexer import TokenKind, tokenize

_UNTERMINATED = {
    TokenKind.STRING: "unterminated string literal",
    TokenKind.QUOTED_IDENT: "unterminated quoted identifier",
    TokenKind.BLOCK_COMMENT: "unterminated block comment",
}


@dataclass(frozen=True, slots=True)
class SyntaxIssue:
    message: str
    #: 1-based position of the offending token.
    line: int
    column: int


class SqlSyntaxError(ValueError):
    """The SQL cannot be formatted or translated because it does not parse."""

    def __init__(self, issues: list[SyntaxIssue]) -> None:
        self.issues = tuple(issues)
        first = self.issues[0]
        super().__init__(f"{first.message} (line {first.line}, column {first.column})")


def check_syntax(sql: str, dialect: Dialect) -> list[SyntaxIssue]:
    """Return syntax problems of ``sql`` in ``dialect``; an empty list means none were found."""
    lexical = _lexical_issues(sql, dialect)
    if lexical:
        return lexical
    try:
        sqlglot.parse(sql, read=dialect.sqlglot_name)
    except ParseError as error:
        return [
            SyntaxIssue(
                message=str(item.get("description", "invalid syntax")),
                line=int(item.get("line") or 1),
                column=int(item.get("col") or 1),
            )
            for item in error.errors
        ] or [SyntaxIssue(str(error), 1, 1)]
    except TokenError as error:
        return [SyntaxIssue(str(error), 1, 1)]
    return []


def parse(sql: str, dialect: Dialect) -> list[exp.Expression]:
    """Parse ``sql`` into one expression per statement; raises :class:`SqlSyntaxError`."""
    issues = check_syntax(sql, dialect)
    if issues:
        raise SqlSyntaxError(issues)
    try:
        parsed = sqlglot.parse(sql, read=dialect.sqlglot_name)
    except SqlglotError as error:  # pragma: no cover - check_syntax already rejected these
        raise SqlSyntaxError([SyntaxIssue(str(error), 1, 1)]) from error
    return [statement for statement in parsed if isinstance(statement, exp.Expression)]


def format_sql(sql: str, dialect: Dialect, *, indent: int = 2) -> str:
    """Pretty-print ``sql`` (one or more statements) in ``dialect``.

    Formatting regenerates SQL from the syntax tree, so it also normalises: keywords become upper
    case and PostgreSQL ``x::int`` becomes ``CAST(x AS INT)``. Comments are kept, but ``--``
    line comments are re-emitted as ``/* */`` block comments.
    """
    statements = parse(sql, dialect)
    generator = make_generator(dialect, pretty=True, indent=indent)
    rendered = [generator.generate(statement) for statement in statements]
    return "\n\n".join(f"{text};" for text in rendered)


def make_generator(dialect: Dialect, *, pretty: bool = False, indent: int = 2) -> Generator:
    """A ``sqlglot`` SQL generator for ``dialect`` that collects (rather than raises) problems.

    Constructs the dialect cannot express are listed in ``generator.unsupported_messages`` after
    each ``generate()`` call.
    """
    return SqlglotDialect.get_or_raise(dialect.sqlglot_name).generator(
        pretty=pretty, indent=indent, unsupported_level=ErrorLevel.IGNORE
    )


def _lexical_issues(sql: str, dialect: Dialect) -> list[SyntaxIssue]:
    issues: list[SyntaxIssue] = []
    for token in tokenize(sql, dialect):
        if token.terminated or token.kind not in _UNTERMINATED:
            continue
        line = sql.count("\n", 0, token.start) + 1
        column = token.start - (sql.rfind("\n", 0, token.start) + 1) + 1
        issues.append(SyntaxIssue(_UNTERMINATED[token.kind], line, column))
    return issues
