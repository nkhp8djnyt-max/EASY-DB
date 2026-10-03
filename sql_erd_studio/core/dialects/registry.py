"""Lookup of the supported dialects by name, alias or connection URL."""

from __future__ import annotations

from urllib.parse import urlsplit

from .base import Dialect, DialectId
from .mysql import MYSQL
from .postgresql import POSTGRESQL
from .sqlite import SQLITE

#: The complete, ordered set of supported dialects. SQL Server / Oracle are deliberately absent.
_DIALECTS: tuple[Dialect, ...] = (POSTGRESQL, MYSQL, SQLITE)


class UnknownDialectError(ValueError):
    """Raised for a name that maps to none of the supported dialects."""

    def __init__(self, name: str) -> None:
        supported = ", ".join(d.id.value for d in _DIALECTS)
        super().__init__(f"unsupported SQL dialect {name!r}; supported dialects: {supported}")
        self.name = name


def _build_index() -> dict[str, Dialect]:
    index: dict[str, Dialect] = {}
    for dialect in _DIALECTS:
        names = (
            dialect.id.value,
            dialect.display_name.lower(),
            *dialect.aliases,
            *dialect.url_schemes,
        )
        for name in names:
            if index.setdefault(name, dialect) is not dialect:
                raise RuntimeError(f"dialect name {name!r} is claimed by two dialects")
    return index


_INDEX = _build_index()


def all_dialects() -> tuple[Dialect, ...]:
    return _DIALECTS


def get_dialect(name: str | DialectId | Dialect) -> Dialect:
    """Resolve ``"postgres"``, ``"MariaDB"``, ``"sqlite3"``, ``"postgresql+psycopg"``, ...

    Raises :class:`UnknownDialectError` (listing the supported dialects) otherwise.
    """
    if isinstance(name, Dialect):
        return name
    key = str(name).strip().lower().split("+", 1)[0]
    try:
        return _INDEX[key]
    except KeyError:
        raise UnknownDialectError(str(name)) from None


def dialect_from_url(url: str) -> Dialect:
    """Pick the dialect from a connection URL's scheme (``postgresql+psycopg://...``)."""
    scheme = urlsplit(url.strip()).scheme
    if not scheme:
        raise ValueError(f"connection URL has no scheme: {url!r}")
    return get_dialect(scheme)
