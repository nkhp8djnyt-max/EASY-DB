from __future__ import annotations

import pytest

from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect
from easydbms.core.queries import Danger, DangerKind, assess, is_write


@pytest.mark.parametrize("dialect", [POSTGRESQL, MYSQL, SQLITE], ids=lambda d: d.id.value)
@pytest.mark.parametrize(
    ("sql", "kind", "target"),
    [
        ("DROP TABLE users", DangerKind.DROP, "users"),
        ("drop table if exists a.b", DangerKind.DROP, "a.b"),
        ("DROP VIEW v", DangerKind.DROP, "v"),
        ("DELETE FROM users", DangerKind.DELETE_ALL, "users"),
        ("update users set name = 'x'", DangerKind.UPDATE_ALL, "users"),
    ],
)
def test_dangerous_statements(dialect: Dialect, sql: str, kind: DangerKind, target: str) -> None:
    assert assess(sql, dialect) == Danger(kind, target)


@pytest.mark.parametrize("dialect", [POSTGRESQL, MYSQL], ids=lambda d: d.id.value)
def test_truncate(dialect: Dialect) -> None:
    danger = assess("TRUNCATE TABLE logs", dialect)
    assert danger is not None
    assert danger.kind is DangerKind.TRUNCATE
    assert danger.target == "logs"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "DELETE FROM users WHERE id = 1",
        "UPDATE users SET name = 'x' WHERE id = 1",
        "INSERT INTO users VALUES (1)",
        "CREATE TABLE t (a int)",
        "SELECT 'DROP TABLE users'",
        "-- DROP TABLE users\nSELECT 1",
        "",
    ],
)
def test_harmless_statements(sql: str) -> None:
    assert assess(sql, POSTGRESQL) is None


def test_unparseable_drop_and_truncate_are_still_caught_by_keyword() -> None:
    assert assess("DROP SOMETHING WEIRD ((", POSTGRESQL) == Danger(DangerKind.DROP)
    assert assess("truncate ??? ((", POSTGRESQL) == Danger(DangerKind.TRUNCATE)
    assert assess("select ??? ((", POSTGRESQL) is None


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("INSERT", True),
        ("DROP", True),
        ("CREATE", True),
        ("SELECT", False),
        ("WITH", False),
        ("EXPLAIN", False),
        ("", False),
        ("SHOW", False),
    ],
)
def test_is_write(keyword: str, expected: bool) -> None:
    assert is_write(keyword) is expected
