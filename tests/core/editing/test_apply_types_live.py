"""Typed values survive the round trip: typed as text, bound as parameters, read back, locked on."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from easydbms.core.db import DatabaseClient
from easydbms.core.editing import (
    ChangeSet,
    ReadOnly,
    build_statements,
    parse_value,
    same_value,
    target_for_table,
)
from easydbms.core.schema import introspect
from tests.core.conftest import Target

DDL = {
    "postgresql": (
        "CREATE TABLE {t} (id integer PRIMARY KEY, flag boolean, price numeric(10,2), "
        "ts timestamptz, d date, tm time, u uuid, j jsonb, ip inet, e text, f double precision)"
    ),
    "mysql": (
        "CREATE TABLE {t} (id integer PRIMARY KEY, flag tinyint(1), price decimal(10,2), "
        "ts datetime(6), d date, tm time, u char(36), j json, e varchar(20), f double)"
    ),
    "sqlite": (
        "CREATE TABLE {t} (id integer PRIMARY KEY, flag boolean, price numeric(10,2), "
        "ts datetime, d date, tm time, u text, j json, e text, f real)"
    ),
}
FIRST = {
    "postgresql": {
        "flag": "yes", "price": "1234.50", "ts": "2024-02-29 13:45:10+02:00", "d": "2024-02-29",
        "tm": "13:45:10", "u": "12345678-1234-5678-1234-567812345678", "j": '{"a": [1, 2]}',
        "ip": "10.1.2.3", "e": "héllo", "f": "2.5",
    },
    "mysql": {
        "flag": "yes", "price": "1234.50", "ts": "2024-02-29 13:45:10.250000", "d": "2024-02-29",
        "tm": "13:45:10", "u": "12345678-1234-5678-1234-567812345678", "j": '{"a": [1, 2]}',
        "e": "héllo", "f": "2.5",
    },
    "sqlite": {
        "flag": "yes", "price": "1234.50", "ts": "2024-02-29 13:45:10", "d": "2024-02-29",
        "tm": "13:45:10", "u": "12345678-1234-5678-1234-567812345678", "j": '{"a": [1, 2]}',
        "e": "héllo", "f": "2.5",
    },
}  # fmt: skip
SECOND = {
    "flag": "no", "price": "0.01", "d": "1999-12-31", "tm": "00:00:01", "e": "änderung",
    "f": "-1.5",
}  # fmt: skip


class Typed:
    def __init__(self, target: Target, client: DatabaseClient, name: str) -> None:
        self.target, self.client, self.name = target, client, name
        self.quoted = target.quote(name)

    def edit_target(self) -> Any:
        table = introspect(self.client).find(self.name)
        assert table is not None
        result = target_for_table(
            table, self.quoted, self.client.dialect, [c.name for c in table.columns]
        )
        assert not isinstance(result, ReadOnly)
        return result

    def row(self, target: Any) -> dict[str, Any]:
        rows = self.client.execute(f"SELECT * FROM {self.quoted}").rows
        values: dict[str, Any] = target.values_of(rows[0])
        return values


@pytest.fixture
def typed(target: Target) -> Iterator[Typed]:
    name = target.table()
    with target.client() as client:
        client.execute(DDL[target.name].format(t=target.quote(name)))
        client.execute(f"INSERT INTO {target.quote(name)} (id) VALUES (1)")
        yield Typed(target, client, name)


def edit_all(typed: Typed, texts: dict[str, str]) -> tuple[Any, ChangeSet]:
    target = typed.edit_target()
    changes = ChangeSet(target)
    original = typed.row(target)
    for column, text in texts.items():
        spec = target.by_name(column)
        assert spec is not None
        changes.set_cell((1,), original, column, parse_value(spec.kind, text))
    return target, changes


def test_every_typed_column_can_be_written_and_read_back(typed: Typed) -> None:
    texts = FIRST[typed.target.name]
    target, changes = edit_all(typed, texts)
    planned = build_statements(changes, typed.client.dialect)
    result = typed.client.apply([p.statement for p in planned])
    assert result.ok, result.error
    row = typed.row(target)
    for column, text in texts.items():
        spec = target.by_name(column)
        assert spec is not None
        assert same_value(spec.kind, row[column], parse_value(spec.kind, text)), (
            column,
            row[column],
            text,
        )


def test_the_second_edit_locks_on_the_values_read_back(typed: Typed) -> None:
    """Old values come from the driver (UUID, aware datetimes, timedelta) and are bound again."""
    _, first = edit_all(typed, FIRST[typed.target.name])
    assert typed.client.apply(
        [p.statement for p in build_statements(first, typed.client.dialect)]
    ).ok
    texts = {c: t for c, t in SECOND.items() if c in FIRST[typed.target.name]}
    texts["u"] = "87654321-4321-8765-4321-876543218765"
    texts["ts"] = "2030-01-01 00:00:00" + ("+00:00" if typed.target.name == "postgresql" else "")
    second_target, second = edit_all(typed, texts)
    planned = build_statements(second, typed.client.dialect)
    result = typed.client.apply([p.statement for p in planned])
    assert result.ok, (result.error, result.matched, [p.preview for p in planned])
    row = typed.row(second_target)
    assert same_value(
        second_target.by_name("d").kind,
        row["d"],
        parse_value(second_target.by_name("d").kind, "1999-12-31"),
    )


def test_unchanged_values_are_not_resent(typed: Typed) -> None:
    target, first = edit_all(typed, FIRST[typed.target.name])
    assert typed.client.apply(
        [p.statement for p in build_statements(first, typed.client.dialect)]
    ).ok
    # "editing" every cell to the text it already shows changes nothing, however it is represented
    from easydbms.core.editing import edit_text

    row = typed.row(target)
    again = ChangeSet(target)
    for spec in target.columns:
        if spec.editable and not spec.key:
            again.set_cell(
                (1,), row, spec.name, parse_value(spec.kind, edit_text(spec.kind, row[spec.name]))
            )
    assert again.is_empty, again.updates
