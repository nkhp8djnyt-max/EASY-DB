"""PostgreSQL dialect."""

from __future__ import annotations

import re
from typing import ClassVar, override

from .base import Capabilities, Dialect, DialectId, IdentQuote, LexerSpec
from .types import TypeKind, base_type_name
from .vocab import postgresql as vocab

_TYPE_KINDS: dict[str, TypeKind] = {
    **dict.fromkeys(
        (
            "smallint",
            "int2",
            "integer",
            "int",
            "int4",
            "bigint",
            "int8",
            "smallserial",
            "serial",
            "serial2",
            "serial4",
            "serial8",
            "bigserial",
            "oid",
        ),
        TypeKind.INTEGER,
    ),
    **dict.fromkeys(("numeric", "decimal"), TypeKind.DECIMAL),
    **dict.fromkeys(("real", "float4", "double precision", "float8", "float"), TypeKind.FLOAT),
    **dict.fromkeys(("boolean", "bool"), TypeKind.BOOLEAN),
    **dict.fromkeys(
        ("char", "character", "bpchar", "varchar", "character varying", "text", "name", "citext"),
        TypeKind.TEXT,
    ),
    "bytea": TypeKind.BINARY,
    "date": TypeKind.DATE,
    **dict.fromkeys(
        ("time", "timetz", "time without time zone", "time with time zone"), TypeKind.TIME
    ),
    **dict.fromkeys(
        (
            "timestamp",
            "timestamptz",
            "timestamp without time zone",
            "timestamp with time zone",
        ),
        TypeKind.DATETIME,
    ),
    "uuid": TypeKind.UUID,
    **dict.fromkeys(("json", "jsonb"), TypeKind.JSON),
    "xml": TypeKind.TEXT,
}


class PostgresDialect(Dialect):
    id: ClassVar[DialectId] = DialectId.POSTGRESQL
    display_name: ClassVar[str] = "PostgreSQL"
    aliases: ClassVar[tuple[str, ...]] = ("postgres", "pg", "pgsql", "psql")
    url_schemes: ClassVar[tuple[str, ...]] = ("postgresql", "postgres", "pgsql")
    default_port: ClassVar[int | None] = 5432
    file_based: ClassVar[bool] = False
    sqlglot_name: ClassVar[str] = "postgres"
    sqlalchemy_driver: ClassVar[str] = "postgresql+psycopg"
    lexer: ClassVar[LexerSpec] = LexerSpec(
        line_comments=("--",),
        identifier_quotes=(IdentQuote('"', '"'),),
        nested_block_comments=True,
        escape_string_prefix=True,
        dollar_quoting=True,
        dollar_numeric_params=True,
    )
    capabilities: ClassVar[Capabilities] = Capabilities(
        schemas=True,
        returning=True,
        ilike=True,
        transactional_ddl=True,
        read_only_sessions=True,
        boolean_type=True,
    )
    identifier_quote: ClassVar[IdentQuote] = IdentQuote('"', '"')
    # Unquoted identifiers fold to lower case, so anything with capitals needs quotes.
    safe_identifier: ClassVar[re.Pattern[str]] = re.compile(r"[a-z_][a-z0-9_$]*")
    reserved_words = vocab.RESERVED_WORDS
    keywords = vocab.KEYWORDS
    functions = vocab.FUNCTIONS
    data_types = vocab.DATA_TYPES

    @override
    def null_safe_eq(self, lhs: str, rhs: str) -> str:
        return f"{lhs} IS NOT DISTINCT FROM {rhs}"

    @override
    def read_only_statements(self, enabled: bool = True) -> tuple[str, ...]:
        mode = "READ ONLY" if enabled else "READ WRITE"
        return (f"SET SESSION CHARACTERISTICS AS TRANSACTION {mode}",)

    @override
    def classify_type(self, declared_type: str) -> TypeKind:
        raw = declared_type.strip().lower()
        if raw.endswith("[]") or raw.startswith("_") or raw == "array":
            return TypeKind.ARRAY
        return _TYPE_KINDS.get(base_type_name(raw), TypeKind.OTHER)

    @override
    def _float_literal(self, value: float) -> str:
        if value != value:
            return "'NaN'::float8"
        if value in (float("inf"), float("-inf")):
            return f"'{'-' if value < 0 else ''}Infinity'::float8"
        return repr(value)

    @override
    def _string_literal(self, value: str) -> str:
        if "\x00" in value:
            raise ValueError("PostgreSQL strings cannot contain NUL")
        # Assumes standard_conforming_strings = on (the default since 9.1): backslash is literal.
        return "'" + value.replace("'", "''") + "'"

    @override
    def _bytes_literal(self, value: bytes) -> str:
        return f"'\\x{value.hex()}'::bytea"


POSTGRESQL = PostgresDialect()
