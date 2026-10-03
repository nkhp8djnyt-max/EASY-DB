"""Dialect-independent classification of column types."""

from __future__ import annotations

import re
from enum import StrEnum


class TypeKind(StrEnum):
    """What kind of editor / comparison a column needs, regardless of the dialect's type name."""

    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TEXT = "text"
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    JSON = "json"
    BINARY = "binary"
    UUID = "uuid"
    ENUM = "enum"
    ARRAY = "array"
    OTHER = "other"


#: Kinds whose equality is reliable enough for ``WHERE col = <old value>`` optimistic locking.
#: Floats (rounding), JSON (key order / normalisation), binary blobs, arrays and unknown types
#: are compared by primary key only.
LOCK_COMPARABLE: frozenset[TypeKind] = frozenset(
    {
        TypeKind.INTEGER,
        TypeKind.DECIMAL,
        TypeKind.BOOLEAN,
        TypeKind.TEXT,
        TypeKind.DATE,
        TypeKind.TIME,
        TypeKind.DATETIME,
        TypeKind.UUID,
        TypeKind.ENUM,
    }
)

_PARAMS_RE = re.compile(r"\(.*?\)")
_WS_RE = re.compile(r"\s+")


def base_type_name(raw: str) -> str:
    """Lower-case a declared type and drop ``(...)`` parameters and surplus whitespace.

    ``"VARCHAR(255)"`` -> ``"varchar"``, ``"Double  Precision"`` -> ``"double precision"``.
    """
    return _WS_RE.sub(" ", _PARAMS_RE.sub("", raw.lower())).strip()
