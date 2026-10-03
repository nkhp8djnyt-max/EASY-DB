from __future__ import annotations

import pytest

from sql_erd_studio.core.dialects import (
    LOCK_COMPARABLE,
    MYSQL,
    POSTGRESQL,
    SQLITE,
    Dialect,
    TypeKind,
)

K = TypeKind

POSTGRES_TYPES = [
    ("integer", K.INTEGER),
    ("bigint", K.INTEGER),
    ("smallint", K.INTEGER),
    ("serial", K.INTEGER),
    ("numeric(10,2)", K.DECIMAL),
    ("numeric", K.DECIMAL),
    ("double precision", K.FLOAT),
    ("real", K.FLOAT),
    ("boolean", K.BOOLEAN),
    ("character varying(20)", K.TEXT),
    ("character(3)", K.TEXT),
    ("text", K.TEXT),
    ("bytea", K.BINARY),
    ("date", K.DATE),
    ("time without time zone", K.TIME),
    ("time(3) with time zone", K.TIME),
    ("timestamp without time zone", K.DATETIME),
    ("timestamp(3) with time zone", K.DATETIME),
    ("timestamptz", K.DATETIME),
    ("uuid", K.UUID),
    ("json", K.JSON),
    ("jsonb", K.JSON),
    ("integer[]", K.ARRAY),
    ("text[]", K.ARRAY),
    ("_int4", K.ARRAY),
    ("ARRAY", K.ARRAY),
    ("interval", K.OTHER),
    ("money", K.OTHER),
    ("inet", K.OTHER),
    ("USER-DEFINED", K.OTHER),
]

MYSQL_TYPES = [
    ("tinyint(1)", K.BOOLEAN),
    ("TINYINT(1)", K.BOOLEAN),
    ("bool", K.BOOLEAN),
    ("bit(1)", K.BOOLEAN),
    ("tinyint(4)", K.INTEGER),
    ("int(11)", K.INTEGER),
    ("int unsigned", K.INTEGER),
    ("bigint(20) unsigned zerofill", K.INTEGER),
    ("year(4)", K.INTEGER),
    ("decimal(10,2)", K.DECIMAL),
    ("decimal(10,2) unsigned", K.DECIMAL),
    ("float", K.FLOAT),
    ("double", K.FLOAT),
    ("varchar(255)", K.TEXT),
    ("char(3)", K.TEXT),
    ("longtext", K.TEXT),
    ("text", K.TEXT),
    ("blob", K.BINARY),
    ("varbinary(16)", K.BINARY),
    ("date", K.DATE),
    ("time(6)", K.TIME),
    ("datetime(6)", K.DATETIME),
    ("timestamp", K.DATETIME),
    ("json", K.JSON),
    ("enum('a','b)c')", K.ENUM),
    ("set('x','y')", K.OTHER),
    ("bit(8)", K.OTHER),
    ("geometry", K.OTHER),
]

SQLITE_TYPES = [
    ("INTEGER", K.INTEGER),
    ("INT", K.INTEGER),
    ("BIGINT UNSIGNED", K.INTEGER),
    ("MEDIUMINT", K.INTEGER),
    ("TEXT", K.TEXT),
    ("VARCHAR(255)", K.TEXT),
    ("NCHAR(55)", K.TEXT),
    ("CLOB", K.TEXT),
    ("BLOB", K.BINARY),
    ("REAL", K.FLOAT),
    ("DOUBLE PRECISION", K.FLOAT),
    ("FLOAT", K.FLOAT),
    ("BOOLEAN", K.BOOLEAN),
    ("DATE", K.DATE),
    ("DATETIME", K.DATETIME),
    ("TIMESTAMP", K.DATETIME),
    ("TIME", K.TIME),
    ("JSON", K.JSON),
    ("DECIMAL(10,5)", K.DECIMAL),
    ("NUMERIC", K.DECIMAL),
    ("", K.OTHER),
    ("VARIANT", K.OTHER),
    ("POINT", K.INTEGER),  # contains "INT": SQLite's own affinity rules say INTEGER
]


@pytest.mark.parametrize(
    ("dialect", "declared", "expected"),
    [(POSTGRESQL, d, k) for d, k in POSTGRES_TYPES]
    + [(MYSQL, d, k) for d, k in MYSQL_TYPES]
    + [(SQLITE, d, k) for d, k in SQLITE_TYPES],
)
def test_classify_type(dialect: Dialect, declared: str, expected: TypeKind) -> None:
    assert dialect.classify_type(declared) is expected


def test_optimistic_locking_only_compares_reliable_types() -> None:
    assert K.INTEGER in LOCK_COMPARABLE
    assert K.TEXT in LOCK_COMPARABLE
    assert K.DATETIME in LOCK_COMPARABLE
    for unreliable in (K.FLOAT, K.JSON, K.BINARY, K.ARRAY, K.OTHER):
        assert unreliable not in LOCK_COMPARABLE
