"""MySQL / MariaDB dialect."""

from __future__ import annotations

import re
from datetime import datetime
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
from .vocab import mysql as vocab

_TYPE_KINDS: dict[str, TypeKind] = {
    **dict.fromkeys(
        ("tinyint", "smallint", "mediumint", "int", "integer", "bigint", "year", "serial"),
        TypeKind.INTEGER,
    ),
    **dict.fromkeys(("decimal", "numeric", "dec", "fixed"), TypeKind.DECIMAL),
    **dict.fromkeys(("float", "double", "real", "double precision"), TypeKind.FLOAT),
    **dict.fromkeys(("bool", "boolean"), TypeKind.BOOLEAN),
    **dict.fromkeys(
        (
            "char",
            "varchar",
            "tinytext",
            "text",
            "mediumtext",
            "longtext",
            "nchar",
            "nvarchar",
            "national char",
            "national varchar",
        ),
        TypeKind.TEXT,
    ),
    **dict.fromkeys(
        ("binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"), TypeKind.BINARY
    ),
    "date": TypeKind.DATE,
    "time": TypeKind.TIME,
    **dict.fromkeys(("datetime", "timestamp"), TypeKind.DATETIME),
    "json": TypeKind.JSON,
    "uuid": TypeKind.UUID,
}

_TINYINT_BOOL_RE = re.compile(r"tinyint\(\s*1\s*\)")
_BIT_BOOL_RE = re.compile(r"bit\(\s*1\s*\)")
_NUMERIC_MODIFIERS = frozenset({"unsigned", "signed", "zerofill"})
_MARIADB_COMPAT_PREFIX = "5.5.5-"  # MariaDB >= 10 reports this for old replication clients


class MySqlDialect(Dialect):
    """MySQL and MariaDB share this dialect; :meth:`server_info` tells them apart."""

    id: ClassVar[DialectId] = DialectId.MYSQL
    display_name: ClassVar[str] = "MySQL"
    aliases: ClassVar[tuple[str, ...]] = ("mariadb",)
    url_schemes: ClassVar[tuple[str, ...]] = ("mysql", "mariadb")
    default_port: ClassVar[int | None] = 3306
    file_based: ClassVar[bool] = False
    sqlglot_name: ClassVar[str] = "mysql"
    sqlalchemy_driver: ClassVar[str] = "mysql+pymysql"
    lexer: ClassVar[LexerSpec] = LexerSpec(
        line_comments=("--", "#"),
        identifier_quotes=(IdentQuote("`", "`"),),
        dash_comment_needs_space=True,
        double_quote_is_string=True,
        backslash_escapes=True,
        question_params=True,
        at_variables=True,
    )
    capabilities: ClassVar[Capabilities] = Capabilities(
        schemas=False,  # a MySQL "schema" is a database; there is no second namespace level
        returning=False,  # MariaDB >= 10.5 only, see server_info()
        ilike=False,
        transactional_ddl=False,  # DDL implicitly commits
        read_only_sessions=True,
        boolean_type=False,  # BOOLEAN is an alias of TINYINT(1)
    )
    identifier_quote: ClassVar[IdentQuote] = IdentQuote("`", "`")
    safe_identifier: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
    reserved_words = vocab.RESERVED_WORDS
    keywords = vocab.KEYWORDS
    functions = vocab.FUNCTIONS
    data_types = vocab.DATA_TYPES

    @override
    def null_safe_eq(self, lhs: str, rhs: str) -> str:
        # Comparison follows the column collation, so 'a' <=> 'A' can hold for *_ci columns.
        return f"{lhs} <=> {rhs}"

    @override
    def read_only_statements(self, enabled: bool = True) -> tuple[str, ...]:
        mode = "READ ONLY" if enabled else "READ WRITE"
        return (f"SET SESSION TRANSACTION {mode}",)

    @override
    def classify_type(self, declared_type: str) -> TypeKind:
        raw = declared_type.strip().lower()
        if _TINYINT_BOOL_RE.match(raw) or _BIT_BOOL_RE.match(raw):
            return TypeKind.BOOLEAN
        if raw.startswith("enum"):
            return TypeKind.ENUM
        words = [w for w in base_type_name(raw).split(" ") if w not in _NUMERIC_MODIFIERS]
        return _TYPE_KINDS.get(" ".join(words), TypeKind.OTHER)

    @override
    def _string_literal(self, value: str) -> str:
        # Assumes the default sql_mode: with NO_BACKSLASH_ESCAPES the backslashes would be literal.
        escaped = value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "\\0")
        return f"'{escaped}'"

    @override
    def _datetime_literal(self, value: datetime) -> str:
        # MySQL DATETIME has no time zone and PyMySQL discards tzinfo when binding; mirror that.
        return self._string_literal(value.replace(tzinfo=None).isoformat(sep=" "))

    @override
    def server_info(self, version_string: str) -> ServerInfo:
        raw = version_string.removeprefix(_MARIADB_COMPAT_PREFIX)
        mariadb = "mariadb" in raw.lower()
        version = parse_version(raw)
        return ServerInfo(
            flavor="mariadb" if mariadb else "mysql",
            version=version,
            raw=version_string,
            # INSERT ... RETURNING exists in MariaDB 10.5+; MySQL has no RETURNING at all.
            supports_returning=mariadb and version >= (10, 5),
        )


MYSQL = MySqlDialect()
