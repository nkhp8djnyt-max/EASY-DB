"""Turn a change set into ``UPDATE`` / ``INSERT`` / ``DELETE`` statements with bound parameters.

Every statement is built twice: the *executable* form (placeholders, run by the driver — values
are never pasted into SQL) and a *preview* form with the values written as literals, which is what
the user reads before confirming.

Optimistic locking: an ``UPDATE`` finds its row by the key it was loaded with **and** by the old
value of every cell it changes (``col = old`` / ``col IS NULL``), so if somebody else changed the
row in the meantime it matches nothing and the apply reports a conflict instead of silently
overwriting. Types whose equality is unreliable (floats, JSON, binary, arrays) are left out of that
check and rely on the key alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from ..db import BoundStatement
from ..dialects import LOCK_COMPARABLE, Dialect, DialectId
from .changeset import ChangeSet, Delete, Insert, Update
from .target import EditTarget, RowKey


@dataclass(frozen=True, slots=True)
class PlannedStatement:
    """One statement to run, with what the UI needs to show it and to blame the right cell."""

    statement: BoundStatement
    #: The statement with the values written as SQL literals (display only).
    preview: str
    kind: str  # "update" | "insert" | "delete"
    #: The row key (update, delete) or the id of the new row (insert).
    subject: RowKey | int
    #: The columns the statement writes.
    columns: tuple[str, ...]


def build_statements(changes: ChangeSet, dialect: Dialect) -> list[PlannedStatement]:
    """Deletes first (so a key can be reused), then updates, then inserts."""
    builder = _Builder(changes.target, dialect)
    planned = [builder.delete(d) for d in changes.deletes.values()]
    planned += [builder.update(u) for u in changes.updates.values()]
    planned += [builder.insert(i) for i in changes.inserts.values()]
    return planned


def preview_script(planned: Sequence[PlannedStatement]) -> str:
    """All statements as one readable script."""
    return "\n".join(p.preview + ";" for p in planned)


def bind_value(dialect: Dialect, value: Any) -> Any:
    """``value`` in a form the dialect's driver accepts as a parameter."""
    if dialect.id is DialectId.SQLITE:
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        if isinstance(value, date | time):
            return value.isoformat()
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        if isinstance(value, UUID):
            return str(value)
        return value
    if isinstance(value, UUID) and dialect.id is DialectId.MYSQL:
        return str(value)
    return value


class _Builder:
    def __init__(self, target: EditTarget, dialect: Dialect) -> None:
        self.target = target
        self.dialect = dialect
        self.marker = "?" if dialect.id is DialectId.SQLITE else "%s"
        self.table = dialect.quote_qualified(target.table.schema, target.table.name)

    # ------------------------------------------------------------------ pieces

    def _name(self, column: str) -> str:
        return self.dialect.quote_ident(column)

    def _executable(self, sql: str) -> str:
        """``%`` in identifiers must be doubled for the drivers that use ``%s`` placeholders."""
        return sql if self.marker == "?" else sql.replace("%", "%%")

    def _finish(
        self,
        kind: str,
        subject: RowKey | int,
        columns: tuple[str, ...],
        template: str,
        params: Sequence[Any],
        expect: int,
    ) -> PlannedStatement:
        """``template`` has ``\x00`` where a parameter goes."""
        executable = self._executable(template.replace("\x00", "\x01")).replace("\x01", self.marker)
        bound = tuple(bind_value(self.dialect, p) for p in params)
        parts = template.split("\x00")
        preview = parts[0]
        for value, part in zip(params, parts[1:], strict=True):
            preview += self._literal(value) + part
        return PlannedStatement(
            BoundStatement(executable, bound, expect), preview, kind, subject, columns
        )

    def _literal(self, value: Any) -> str:
        try:
            return self.dialect.render_literal(value)
        except (TypeError, ValueError):
            return repr(value)

    def _where_key(self, key: RowKey) -> tuple[list[str], list[Any]]:
        terms = [f"{self._name(column)} = \x00" for column in self.target.key]
        return terms, list(key)

    # ------------------------------------------------------------------ statements

    def update(self, update: Update) -> PlannedStatement:
        columns = tuple(update.changes)
        sets = [f"{self._name(column)} = \x00" for column in columns]
        params: list[Any] = [update.changes[column][1] for column in columns]
        terms, key_params = self._where_key(update.key)
        params += key_params
        for column in columns:
            spec = self.target.by_name(column)
            if spec is None or spec.key or spec.kind not in LOCK_COMPARABLE:
                continue  # the key is checked anyway; unreliable types rely on the key alone
            old = update.changes[column][0]
            if old is None:
                terms.append(f"{self._name(column)} IS NULL")
            else:
                terms.append(f"{self._name(column)} = \x00")
                params.append(old)
        template = f"UPDATE {self.table} SET {', '.join(sets)} WHERE {' AND '.join(terms)}"
        return self._finish("update", update.key, columns, template, params, 1)

    def delete(self, delete: Delete) -> PlannedStatement:
        terms, params = self._where_key(delete.key)
        template = f"DELETE FROM {self.table} WHERE {' AND '.join(terms)}"
        return self._finish("delete", delete.key, (), template, params, 1)

    def insert(self, insert: Insert) -> PlannedStatement:
        columns = tuple(name for name in self._column_order() if name in insert.values)
        if not columns:
            tail = "() VALUES ()" if self.dialect.id is DialectId.MYSQL else "DEFAULT VALUES"
            return self._finish("insert", insert.id, (), f"INSERT INTO {self.table} {tail}", [], 1)
        names = ", ".join(self._name(column) for column in columns)
        markers = ", ".join("\x00" for _ in columns)
        template = f"INSERT INTO {self.table} ({names}) VALUES ({markers})"
        params = [insert.values[column] for column in columns]
        return self._finish("insert", insert.id, columns, template, params, 1)

    def _column_order(self) -> list[str]:
        return [column.name for column in self.target.table.columns]


def row_label(target: EditTarget, key: RowKey) -> str:
    """A row's identity as text for messages: ``id = 3`` or ``order_id = 1, line_no = 2``."""
    return ", ".join(f"{name} = {value}" for name, value in zip(target.key, key, strict=True))
