"""SQL dialect support: PostgreSQL, MySQL (including MariaDB) and SQLite."""

from .base import (
    Capabilities,
    Dialect,
    DialectId,
    IdentQuote,
    LexerSpec,
    ServerInfo,
)
from .lexer import Token, TokenKind, tokenize
from .mysql import MYSQL, MySqlDialect
from .pagination import paginate
from .parsing import SqlSyntaxError, SyntaxIssue, check_syntax, format_sql, parse
from .postgresql import POSTGRESQL, PostgresDialect
from .registry import UnknownDialectError, all_dialects, dialect_from_url, get_dialect
from .splitter import Statement, split_statements, statement_at
from .sqlite import SQLITE, SqliteDialect
from .translate import Translation, translate
from .types import LOCK_COMPARABLE, TypeKind
from .vocab import FunctionCategory, FunctionSpec

__all__ = [
    "LOCK_COMPARABLE",
    "MYSQL",
    "POSTGRESQL",
    "SQLITE",
    "Capabilities",
    "Dialect",
    "DialectId",
    "FunctionCategory",
    "FunctionSpec",
    "IdentQuote",
    "LexerSpec",
    "MySqlDialect",
    "PostgresDialect",
    "ServerInfo",
    "SqlSyntaxError",
    "SqliteDialect",
    "Statement",
    "SyntaxIssue",
    "Token",
    "TokenKind",
    "Translation",
    "TypeKind",
    "UnknownDialectError",
    "all_dialects",
    "check_syntax",
    "dialect_from_url",
    "format_sql",
    "get_dialect",
    "paginate",
    "parse",
    "split_statements",
    "statement_at",
    "tokenize",
    "translate",
]
