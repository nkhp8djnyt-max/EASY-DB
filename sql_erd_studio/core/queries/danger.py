"""Spotting statements that deserve a confirmation before they run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from ..dialects import Dialect

_WRITE_KEYWORDS = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "UPSERT", "CREATE", "ALTER", "DROP",
        "TRUNCATE", "GRANT", "REVOKE", "RENAME", "VACUUM", "REINDEX", "ATTACH", "DETACH", "COPY",
        "LOAD", "CALL", "DO", "EXECUTE",
    }
)  # fmt: skip


class DangerKind(StrEnum):
    DROP = "drop"
    TRUNCATE = "truncate"
    DELETE_ALL = "delete_all"
    UPDATE_ALL = "update_all"


@dataclass(frozen=True, slots=True)
class Danger:
    kind: DangerKind
    #: What the statement acts on, e.g. the table name, when it could be determined.
    target: str = ""


def is_write(keyword: str) -> bool:
    """Does a statement starting with ``keyword`` (upper case) change anything?

    ``WITH`` may precede a data-modifying statement, so it is judged by :func:`assess`'s caller
    inspecting the parsed statement; here only the leading keyword decides.
    """
    return keyword in _WRITE_KEYWORDS


def assess(sql: str, dialect: Dialect) -> Danger | None:
    """The danger of one statement: DROP, TRUNCATE, or DELETE / UPDATE without WHERE."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect.sqlglot_name)
    except SqlglotError:
        return _by_keyword(sql)
    if isinstance(tree, exp.Command):  # sqlglot did not understand it; judge by the first word
        return _by_keyword(sql)
    if isinstance(tree, exp.Drop):
        return Danger(DangerKind.DROP, _target(tree))
    if isinstance(tree, exp.TruncateTable):
        return Danger(DangerKind.TRUNCATE, _target(tree))
    if isinstance(tree, exp.Delete) and tree.args.get("where") is None:
        return Danger(DangerKind.DELETE_ALL, _target(tree))
    if isinstance(tree, exp.Update) and tree.args.get("where") is None:
        return Danger(DangerKind.UPDATE_ALL, _target(tree))
    return None


def _target(tree: exp.Expression) -> str:
    table = tree.find(exp.Table)
    return table.sql() if table is not None else ""


def _by_keyword(sql: str) -> Danger | None:
    words = sql.split(None, 1)
    first = words[0].upper() if words else ""
    if first == "DROP":
        return Danger(DangerKind.DROP)
    if first == "TRUNCATE":
        return Danger(DangerKind.TRUNCATE)
    return None
