"""Translate SQL between the supported dialects."""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .base import Dialect
from .parsing import make_generator, parse

_CALL_RE = re.compile(r"\s*([A-Za-z_][\w$]*)\s*\(")


@dataclass(frozen=True, slots=True)
class Translation:
    #: The translated script.
    sql: str
    #: Constructs the target dialect cannot express, and functions it does not know. The output
    #: is a best effort whenever this is non-empty.
    warnings: tuple[str, ...]


def translate(sql: str, *, source: Dialect, target: Dialect, pretty: bool = False) -> Translation:
    """Rewrite ``sql`` written for ``source`` so it runs on ``target``.

    Raises :class:`~.parsing.SqlSyntaxError` if ``sql`` does not parse as ``source``.
    ``sqlglot`` reports some constructs it cannot translate; on top of that, every function call in
    the result is checked against the target's function list, because ``sqlglot`` sometimes emits
    a function the target does not have (e.g. ``TIMESTAMP_TRUNC`` for SQLite).
    """
    generator = make_generator(target, pretty=pretty)
    rendered: list[str] = []
    warnings: list[str] = []
    for statement in parse(sql, source):
        text = generator.generate(statement)
        rendered.append(text)
        warnings.extend(w for w in generator.unsupported_messages if w not in warnings)
        warnings.extend(w for w in _unknown_functions(text, target) if w not in warnings)
    separator = "\n\n" if pretty else "\n"
    return Translation(
        sql=separator.join(f"{text};" for text in rendered),
        warnings=tuple(warnings),
    )


def _unknown_functions(sql: str, target: Dialect) -> list[str]:
    """Warnings for calls in ``sql`` whose function is not in ``target``'s vocabulary."""
    known = {function.name for function in target.functions} | target.keywords
    try:
        tree = sqlglot.parse_one(sql, read=target.sqlglot_name)
    except SqlglotError:
        return []
    generator = make_generator(target)
    found: list[str] = []
    for node in tree.find_all(exp.Func):
        match = _CALL_RE.match(generator.generate(node))
        if match is None:  # rendered without call syntax: CURRENT_DATE, ARRAY[...], ...
            continue
        name = match.group(1).upper()
        if name not in known and name not in found:
            found.append(name)
    return [f"{name}() is not a known {target.display_name} function" for name in found]
