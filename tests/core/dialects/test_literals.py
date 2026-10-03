from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

import pytest

from sql_erd_studio.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect, all_dialects


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_common_literals(dialect: Dialect) -> None:
    assert dialect.render_literal(None) == "NULL"
    assert dialect.render_literal(42) == "42"
    assert dialect.render_literal(-7) == "-7"
    assert dialect.render_literal(10**30) == "1000000000000000000000000000000"
    assert dialect.render_literal(1.5) == "1.5"
    assert dialect.render_literal(1e-9) == "1e-09"
    assert dialect.render_literal(Decimal("12.50")) == "12.50"
    assert dialect.render_literal(Decimal("1E+2")) == "100"
    assert dialect.render_literal("") == "''"
    assert dialect.render_literal("it's") == "'it''s'"
    assert dialect.render_literal("a;b -- c") == "'a;b -- c'"
    assert dialect.render_literal("héllo ✓") == "'héllo ✓'"
    assert dialect.render_literal(date(2024, 1, 2)) == "'2024-01-02'"
    assert dialect.render_literal(time(3, 4, 5)) == "'03:04:05'"
    assert dialect.render_literal(datetime(2024, 1, 2, 3, 4, 5)) == "'2024-01-02 03:04:05'"
    assert (
        dialect.render_literal(UUID("12345678-1234-5678-1234-567812345678"))
        == "'12345678-1234-5678-1234-567812345678'"
    )
    assert dialect.render_literal({"a": [1, "é"]}) == """'{"a": [1, "é"]}'"""


def test_booleans_are_not_rendered_as_integers() -> None:
    assert POSTGRESQL.render_literal(True) == "TRUE"
    assert POSTGRESQL.render_literal(False) == "FALSE"
    assert MYSQL.render_literal(True) == "TRUE"
    assert SQLITE.render_literal(True) == "1"
    assert SQLITE.render_literal(False) == "0"


def test_postgresql_specifics() -> None:
    assert POSTGRESQL.render_literal("back\\slash") == "'back\\slash'"
    assert POSTGRESQL.render_literal(b"\x00\x01\xff") == "'\\x0001ff'::bytea"
    assert POSTGRESQL.render_literal(float("nan")) == "'NaN'::float8"
    assert POSTGRESQL.render_literal(float("inf")) == "'Infinity'::float8"
    assert POSTGRESQL.render_literal(float("-inf")) == "'-Infinity'::float8"
    aware = datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    assert POSTGRESQL.render_literal(aware) == "'2024-01-02 03:04:05.123456+00:00'"
    with pytest.raises(ValueError, match="NUL"):
        POSTGRESQL.render_literal("a\x00b")


def test_mysql_specifics() -> None:
    assert MYSQL.render_literal("back\\slash") == "'back\\\\slash'"
    assert MYSQL.render_literal("a\x00b") == "'a\\0b'"
    assert MYSQL.render_literal(b"\x00\x01\xff") == "X'0001FF'"
    assert MYSQL.render_literal(b"") == "X''"
    # tzinfo is dropped, mirroring the driver
    aware = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert MYSQL.render_literal(aware) == "'2024-01-02 03:04:05'"


def test_sqlite_specifics() -> None:
    assert SQLITE.render_literal("back\\slash") == "'back\\slash'"
    assert SQLITE.render_literal(b"\x00\x01\xff") == "X'0001FF'"
    with pytest.raises(ValueError, match="NUL"):
        SQLITE.render_literal("a\x00b")


@pytest.mark.parametrize("dialect", [MYSQL, SQLITE], ids=lambda d: d.id.value)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")])
def test_values_a_dialect_cannot_represent_are_rejected(dialect: Dialect, value: object) -> None:
    with pytest.raises(ValueError, match="cannot"):
        dialect.render_literal(value)


@pytest.mark.parametrize("dialect", all_dialects(), ids=lambda d: d.id.value)
def test_unsupported_python_types_are_rejected(dialect: Dialect) -> None:
    with pytest.raises(TypeError, match="object"):
        dialect.render_literal(object())


def test_null_safe_equality_and_read_only_statements() -> None:
    assert POSTGRESQL.null_safe_eq("a", ":p") == "a IS NOT DISTINCT FROM :p"
    assert MYSQL.null_safe_eq("a", ":p") == "a <=> :p"
    assert SQLITE.null_safe_eq("a", ":p") == "a IS :p"
    assert POSTGRESQL.read_only_statements() == (
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
    )
    assert POSTGRESQL.read_only_statements(False)[0].endswith("READ WRITE")
    assert MYSQL.read_only_statements() == ("SET SESSION TRANSACTION READ ONLY",)
    assert SQLITE.read_only_statements() == ("PRAGMA query_only = ON",)
    assert SQLITE.read_only_statements(False) == ("PRAGMA query_only = OFF",)


@pytest.mark.parametrize(
    ("dialect", "version", "flavor", "returning"),
    [
        (POSTGRESQL, "16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)", "postgresql", True),
        (MYSQL, "8.0.36", "mysql", False),
        (MYSQL, "5.7.44-log", "mysql", False),
        (MYSQL, "10.11.14-MariaDB-0ubuntu0.24.04.1", "mariadb", True),
        (MYSQL, "5.5.5-10.11.14-MariaDB", "mariadb", True),
        (MYSQL, "10.4.32-MariaDB", "mariadb", False),
        (SQLITE, "3.45.1", "sqlite", True),
        (SQLITE, "3.34.0", "sqlite", False),
    ],
)
def test_server_info(dialect: Dialect, version: str, flavor: str, returning: bool) -> None:
    info = dialect.server_info(version)
    assert info.flavor == flavor
    assert info.supports_returning is returning
    assert info.raw == version
    assert info.version[0] >= 3
