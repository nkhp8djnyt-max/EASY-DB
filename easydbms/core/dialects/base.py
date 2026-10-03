"""The :class:`Dialect` abstraction: everything that differs between SQL dialects.

A dialect is a stateless singleton. Class attributes describe it (lexical rules, vocabulary,
capabilities); methods generate dialect-correct SQL fragments. Anything that needs to *parse*
text (formatting, splitting, translation) lives in sibling modules and takes a ``Dialect``.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from .types import TypeKind
from .vocab.spec import FunctionSpec


class DialectId(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"


@dataclass(frozen=True, slots=True)
class IdentQuote:
    """A way of quoting identifiers. ``doubled`` means the closing char is escaped by doubling."""

    open: str
    close: str
    doubled: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class LexerSpec:
    """Lexical rules of a dialect, consumed by :mod:`.lexer`."""

    line_comments: tuple[str, ...]
    identifier_quotes: tuple[IdentQuote, ...]
    #: MySQL: ``--`` only starts a comment when followed by whitespace or end of input.
    dash_comment_needs_space: bool = False
    #: PostgreSQL: ``/* /* */ */`` nests.
    nested_block_comments: bool = False
    #: MySQL (default sql_mode): ``"text"`` is a string, not an identifier.
    double_quote_is_string: bool = False
    #: MySQL: ``\'`` escapes inside ordinary string literals.
    backslash_escapes: bool = False
    #: PostgreSQL: ``E'...'`` strings honour backslash escapes.
    escape_string_prefix: bool = False
    #: PostgreSQL: ``$tag$ ... $tag$``.
    dollar_quoting: bool = False
    #: PostgreSQL: ``$1``.
    dollar_numeric_params: bool = False
    #: SQLite: ``$name``.
    dollar_named_params: bool = False
    #: ``?`` (and SQLite ``?NNN``).
    question_params: bool = False
    #: ``:name`` (SQLAlchemy ``text()`` style); never matches the ``::`` cast operator.
    colon_params: bool = True
    #: SQLite: ``@name``.
    at_params: bool = False
    #: MySQL: ``@user_var`` and ``@@system_var``.
    at_variables: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Capabilities:
    """Feature flags the UI consults instead of testing for a dialect name."""

    #: A real schema namespace inside one database (PostgreSQL). MySQL "schemas" are databases.
    schemas: bool
    #: ``INSERT/UPDATE/DELETE ... RETURNING`` (version dependent, see :meth:`Dialect.server_info`).
    returning: bool
    ilike: bool
    #: DDL participates in transactions and can be rolled back.
    transactional_ddl: bool
    #: Sessions can be switched to read-only with :meth:`Dialect.read_only_statements`.
    read_only_sessions: bool
    #: A native BOOLEAN type exists (SQLite stores 0/1).
    boolean_type: bool


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """What a concrete server reports about itself; refines the static dialect capabilities."""

    flavor: str
    version: tuple[int, ...]
    raw: str
    supports_returning: bool


_VERSION_RE = re.compile(r"\d+(?:\.\d+)*")
_TRAILING_SEMICOLONS_RE = re.compile(r"[;\s]+\Z")


def parse_version(raw: str) -> tuple[int, ...]:
    """Extract the first dotted version number: ``"16.14 (Ubuntu)"`` -> ``(16, 14)``."""
    match = _VERSION_RE.search(raw)
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def strip_trailing_semicolons(sql: str) -> str:
    return _TRAILING_SEMICOLONS_RE.sub("", sql)


class Dialect(ABC):
    id: ClassVar[DialectId]
    display_name: ClassVar[str]
    #: Extra names accepted by :func:`.registry.get_dialect` (lower-case).
    aliases: ClassVar[tuple[str, ...]]
    #: URL schemes (without ``+driver``) that select this dialect.
    url_schemes: ClassVar[tuple[str, ...]]
    default_port: ClassVar[int | None]
    #: The database is a local file (SQLite) rather than a network server.
    file_based: ClassVar[bool]
    #: Name understood by ``sqlglot``.
    sqlglot_name: ClassVar[str]
    #: SQLAlchemy URL drivername, e.g. ``postgresql+psycopg``.
    sqlalchemy_driver: ClassVar[str]
    lexer: ClassVar[LexerSpec]
    capabilities: ClassVar[Capabilities]
    #: Primary identifier quote used when generating SQL.
    identifier_quote: ClassVar[IdentQuote]
    #: Identifiers matching this pattern (and not reserved) can be written unquoted.
    safe_identifier: ClassVar[re.Pattern[str]]
    #: Upper-case words that must be quoted when used as identifiers.
    reserved_words: ClassVar[frozenset[str]]
    #: Upper-case keywords for highlighting and autocomplete (superset of ``reserved_words``).
    keywords: ClassVar[frozenset[str]]
    functions: ClassVar[tuple[FunctionSpec, ...]]
    data_types: ClassVar[tuple[str, ...]]

    # ------------------------------------------------------------------ identity

    def __repr__(self) -> str:
        return f"<Dialect {self.id.value}>"

    def __str__(self) -> str:
        return self.display_name

    # ------------------------------------------------------------------ identifiers

    def quote_ident(self, name: str) -> str:
        """Always quote ``name``: ``a"b`` -> ``"a""b"`` (PostgreSQL/SQLite), ```a``b``` (MySQL)."""
        if not name:
            raise ValueError("identifier must not be empty")
        if "\x00" in name:
            raise ValueError("identifier must not contain NUL")
        quote = self.identifier_quote
        return quote.open + name.replace(quote.close, quote.close * 2) + quote.close

    def quote_qualified(self, *parts: str | None) -> str:
        """Quote and join ``schema.table.column`` style names, skipping ``None`` parts."""
        names = [part for part in parts if part is not None]
        if not names:
            raise ValueError("at least one name part is required")
        return ".".join(self.quote_ident(part) for part in names)

    def needs_quoting(self, name: str) -> bool:
        return not self.safe_identifier.fullmatch(name) or name.upper() in self.reserved_words

    def quote_ident_if_needed(self, name: str) -> str:
        """Quote only when required, e.g. for autocomplete insertions."""
        return self.quote_ident(name) if self.needs_quoting(name) else name

    # ------------------------------------------------------------------ literals

    def render_literal(self, value: object) -> str:
        """Render ``value`` as an SQL literal.

        For *display* (change previews, copy-as-SQL) only: statements that are executed must
        always use bound parameters. Raises ``TypeError`` for unsupported Python types and
        ``ValueError`` for values the dialect cannot represent (e.g. ``NaN`` in MySQL).
        """
        if value is None:
            return "NULL"
        if isinstance(value, bool):  # before int: bool is an int subclass
            return self._bool_literal(value)
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return self._float_literal(value)
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(f"cannot render {value} as a {self.display_name} literal")
            return format(value, "f")
        if isinstance(value, str):
            return self._string_literal(value)
        if isinstance(value, bytes | bytearray | memoryview):
            return self._bytes_literal(bytes(value))
        if isinstance(value, datetime):  # before date: datetime is a date subclass
            return self._datetime_literal(value)
        if isinstance(value, date | time):
            return self._string_literal(value.isoformat())
        if isinstance(value, UUID):
            return self._string_literal(str(value))
        if isinstance(value, dict | list):
            return self._string_literal(json.dumps(value, ensure_ascii=False))
        raise TypeError(f"cannot render {type(value).__name__} as an SQL literal")

    def _bool_literal(self, value: bool) -> str:
        return "TRUE" if value else "FALSE"

    def _float_literal(self, value: float) -> str:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{self.display_name} cannot represent {value!r}")
        return repr(value)

    def _datetime_literal(self, value: datetime) -> str:
        return self._string_literal(value.isoformat(sep=" "))

    def _string_literal(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _bytes_literal(self, value: bytes) -> str:
        return f"X'{value.hex().upper()}'"

    # ------------------------------------------------------------------ comparison

    @abstractmethod
    def null_safe_eq(self, lhs: str, rhs: str) -> str:
        """SQL for ``lhs = rhs`` that also holds when both sides are NULL.

        ``lhs`` / ``rhs`` are SQL fragments (a quoted column, a bind placeholder). Used for
        optimistic locking: ``WHERE id = :id AND name <null-safe-eq> :old_name``.
        """

    # ------------------------------------------------------------------ sessions

    @abstractmethod
    def read_only_statements(self, enabled: bool = True) -> tuple[str, ...]:
        """Statements that switch the current session to read-only (or back to read-write)."""

    # ------------------------------------------------------------------ types

    @abstractmethod
    def classify_type(self, declared_type: str) -> TypeKind:
        """Map a declared column type (``"varchar(255)"``, ``"tinyint(1)"``) to a ``TypeKind``."""

    # ------------------------------------------------------------------ server

    def server_info(self, version_string: str) -> ServerInfo:
        """Interpret ``SELECT version()`` output; subclasses refine flavor and feature flags."""
        return ServerInfo(
            flavor=self.id.value,
            version=parse_version(version_string),
            raw=version_string,
            supports_returning=self.capabilities.returning,
        )
