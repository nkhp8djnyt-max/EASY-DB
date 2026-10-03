"""SQLite dialect."""

from __future__ import annotations

import re
from typing import ClassVar, override

from .base import (
    Capabilities,
    Dialect,
    DialectId,
    IdentQuote,
    LexerSpec,
    ServerInfo,
    parse_version,
)
from .types import TypeKind, base_type_name
from .vocab import sqlite as vocab

# Names people actually declare; they all have NUMERIC/TEXT affinity in SQLite but need a
# specific editor. Checked before the affinity rules below.
_NAMED_KINDS: dict[str, TypeKind] = {
    **dict.fromkeys(("boolean", "bool"), TypeKind.BOOLEAN),
    "date": TypeKind.DATE,
    "time": TypeKind.TIME,
    **dict.fromkeys(("datetime", "timestamp"), TypeKind.DATETIME),
    **dict.fromkeys(("json", "jsonb"), TypeKind.JSON),
    "uuid": TypeKind.UUID,
    **dict.fromkeys(("decimal", "numeric", "number"), TypeKind.DECIMAL),
}

_RETURNING_SINCE = (3, 35)


class SqliteDialect(Dialect):
    id: ClassVar[DialectId] = DialectId.SQLITE
    display_name: ClassVar[str] = "SQLite"
    aliases: ClassVar[tuple[str, ...]] = ("sqlite3",)
    url_schemes: ClassVar[tuple[str, ...]] = ("sqlite",)
    default_port: ClassVar[int | None] = None
    file_based: ClassVar[bool] = True
    sqlglot_name: ClassVar[str] = "sqlite"
    sqlalchemy_driver: ClassVar[str] = "sqlite+pysqlite"
    lexer: ClassVar[LexerSpec] = LexerSpec(
        line_comments=("--",),
        # "name" is standard; [name] and `name` are accepted for MS / MySQL compatibility.
        identifier_quotes=(
            IdentQuote('"', '"'),
            IdentQuote("[", "]", doubled=False),
            IdentQuote("`", "`"),
        ),
        question_params=True,
        dollar_named_params=True,
        at_params=True,
    )
    capabilities: ClassVar[Capabilities] = Capabilities(
        schemas=False,  # only ATTACHed databases ("main", "temp", ...)
        returning=True,  # since 3.35, see server_info()
        ilike=False,
        transactional_ddl=True,
        read_only_sessions=True,
        boolean_type=False,  # stored as 0 / 1
    )
    identifier_quote: ClassVar[IdentQuote] = IdentQuote('"', '"')
    safe_identifier: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
    reserved_words = vocab.RESERVED_WORDS
    keywords = vocab.KEYWORDS
    functions = vocab.FUNCTIONS
    data_types = vocab.DATA_TYPES

    @override
    def null_safe_eq(self, lhs: str, rhs: str) -> str:
        return f"{lhs} IS {rhs}"

    @override
    def read_only_statements(self, enabled: bool = True) -> tuple[str, ...]:
        return (f"PRAGMA query_only = {'ON' if enabled else 'OFF'}",)

    @override
    def classify_type(self, declared_type: str) -> TypeKind:
        """Named types first, then SQLite's affinity rules (datatype docs, section 3.1)."""
        name = base_type_name(declared_type)
        if name in _NAMED_KINDS:
            return _NAMED_KINDS[name]
        if name.startswith(("datetime", "timestamp")):
            return TypeKind.DATETIME
        if "int" in name:
            return TypeKind.INTEGER
        if "char" in name or "clob" in name or "text" in name:
            return TypeKind.TEXT
        if "blob" in name:
            return TypeKind.BINARY
        if "real" in name or "floa" in name or "doub" in name:
            return TypeKind.FLOAT
        return TypeKind.OTHER  # no declared type, or NUMERIC affinity of an unknown name

    @override
    def _bool_literal(self, value: bool) -> str:
        return "1" if value else "0"

    @override
    def _string_literal(self, value: str) -> str:
        if "\x00" in value:
            raise ValueError("SQLite string literals cannot contain NUL")
        return "'" + value.replace("'", "''") + "'"

    @override
    def server_info(self, version_string: str) -> ServerInfo:
        version = parse_version(version_string)
        return ServerInfo(
            flavor="sqlite",
            version=version,
            raw=version_string,
            supports_returning=version >= _RETURNING_SINCE,
        )


SQLITE = SqliteDialect()
