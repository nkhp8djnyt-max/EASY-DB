from __future__ import annotations

import pytest

from easydbms.core.dialects import (
    MYSQL,
    POSTGRESQL,
    SQLITE,
    Dialect,
    DialectId,
    UnknownDialectError,
    all_dialects,
    dialect_from_url,
    get_dialect,
)


def test_exactly_the_three_open_source_dialects() -> None:
    assert [d.id for d in all_dialects()] == [
        DialectId.POSTGRESQL,
        DialectId.MYSQL,
        DialectId.SQLITE,
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("postgresql", POSTGRESQL),
        ("PostgreSQL", POSTGRESQL),
        ("postgres", POSTGRESQL),
        ("pg", POSTGRESQL),
        ("psql", POSTGRESQL),
        ("  postgresql+psycopg ", POSTGRESQL),
        ("mysql", MYSQL),
        ("MariaDB", MYSQL),
        ("mysql+pymysql", MYSQL),
        ("sqlite", SQLITE),
        ("SQLite3", SQLITE),
        ("sqlite+pysqlite", SQLITE),
        (DialectId.SQLITE, SQLITE),
        (MYSQL, MYSQL),
    ],
)
def test_get_dialect(name: str | DialectId | Dialect, expected: Dialect) -> None:
    assert get_dialect(name) is expected


@pytest.mark.parametrize(
    "name", ["mssql", "sqlserver", "oracle", "oracledb", "clickhouse", "", "nonsense"]
)
def test_unsupported_dialects_are_rejected_with_the_supported_list(name: str) -> None:
    with pytest.raises(UnknownDialectError) as info:
        get_dialect(name)
    assert "postgresql, mysql, sqlite" in str(info.value)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@host:5432/db?sslmode=require", POSTGRESQL),
        ("postgres://u@host/db", POSTGRESQL),
        ("postgresql+psycopg://u:p@host/db", POSTGRESQL),
        ("mysql://u:p@host:3306/db", MYSQL),
        ("mariadb://u:p@host/db", MYSQL),
        ("mysql+pymysql://u:p@host/db", MYSQL),
        ("sqlite:///tmp/file.db", SQLITE),
        ("sqlite://", SQLITE),
    ],
)
def test_dialect_from_url(url: str, expected: Dialect) -> None:
    assert dialect_from_url(url) is expected


def test_dialect_from_url_rejects_corporate_and_schemeless_urls() -> None:
    with pytest.raises(UnknownDialectError):
        dialect_from_url("mssql://sa:pw@host/db")
    with pytest.raises(UnknownDialectError):
        dialect_from_url("oracle://u:p@host/orcl")
    with pytest.raises(ValueError, match="no scheme"):
        dialect_from_url("just-a-path")


def test_static_metadata() -> None:
    assert [d.default_port for d in all_dialects()] == [5432, 3306, None]
    assert [d.file_based for d in all_dialects()] == [False, False, True]
    assert [d.sqlglot_name for d in all_dialects()] == ["postgres", "mysql", "sqlite"]
    assert {d.capabilities.ilike for d in all_dialects() if d is not POSTGRESQL} == {False}
    assert POSTGRESQL.capabilities.schemas
    assert not MYSQL.capabilities.transactional_ddl
