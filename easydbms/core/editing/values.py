"""Turning what a user types into a value the driver can bind, and a value back into editable text.

The grid shows driver values (``Decimal``, ``datetime``, ``bool``, ...); an editor works with text.
:func:`parse_value` checks the text against the column's :class:`~easydbms.core.dialects.TypeKind`
and returns the Python value to bind; :func:`edit_text` is its inverse. :func:`same_value` decides
whether an "edit" actually changed anything, which has to ignore representation differences
(``Decimal('5.0')`` is ``Decimal('5.00')``; MySQL hands out ``TIME`` as ``timedelta``).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from ..dialects import TypeKind

#: Kinds whose text may be empty: an empty input there means NULL (an empty string is a value
#: only for text).
_EMPTY_IS_NULL = frozenset(
    {
        TypeKind.INTEGER,
        TypeKind.DECIMAL,
        TypeKind.FLOAT,
        TypeKind.BOOLEAN,
        TypeKind.DATE,
        TypeKind.TIME,
        TypeKind.DATETIME,
        TypeKind.JSON,
        TypeKind.UUID,
        TypeKind.ENUM,
        TypeKind.OTHER,
    }
)
#: Kinds the grid cannot edit as text.
READ_ONLY_KINDS = frozenset({TypeKind.BINARY, TypeKind.ARRAY})

_TRUE = frozenset({"true", "t", "yes", "y", "on", "1"})
_FALSE = frozenset({"false", "f", "no", "n", "off", "0"})


class ValueParseError(ValueError):
    """The text is not a valid value of the column's type; ``kind`` says what was expected."""

    def __init__(self, kind: TypeKind, text: str) -> None:
        super().__init__(f"{text!r} is not a valid {kind.value} value")
        self.kind = kind
        self.text = text


def is_editable(kind: TypeKind) -> bool:
    return kind not in READ_ONLY_KINDS


def parse_value(kind: TypeKind, text: str) -> Any:
    """The value to bind for ``text`` typed into a column of ``kind`` (``None`` means NULL)."""
    if kind in _EMPTY_IS_NULL and not text.strip():
        return None
    stripped = text.strip()
    try:
        if kind is TypeKind.INTEGER:
            return int(stripped)
        if kind is TypeKind.DECIMAL:
            number = Decimal(stripped)
            if not number.is_finite():
                raise ValueParseError(kind, text)
            return number
        if kind is TypeKind.FLOAT:
            value = float(stripped)
            if not math.isfinite(value):
                raise ValueParseError(kind, text)
            return value
        if kind is TypeKind.BOOLEAN:
            lowered = stripped.lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
            raise ValueParseError(kind, text)
        if kind is TypeKind.DATE:
            return date.fromisoformat(stripped)
        if kind is TypeKind.TIME:
            return time.fromisoformat(stripped)
        if kind is TypeKind.DATETIME:
            return datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        if kind is TypeKind.JSON:
            json.loads(stripped)
            return stripped
        if kind is TypeKind.UUID:
            return str(UUID(stripped))
    except (ValueError, InvalidOperation) as error:
        if isinstance(error, ValueParseError):
            raise
        raise ValueParseError(kind, text) from error
    return text  # TEXT, ENUM, OTHER (and anything unknown): as typed


def edit_text(kind: TypeKind, value: object) -> str:
    """The text an editor starts with for ``value`` (``""`` for NULL)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return _timedelta_text(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, dict | list):  # PostgreSQL hands out json / jsonb already parsed
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _timedelta_text(value: timedelta) -> str:
    total = value.total_seconds()
    sign = "-" if total < 0 else ""
    seconds = abs(total)
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    micro = round((seconds - int(seconds)) * 1_000_000)
    text = f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"
    return text + (f".{micro:06d}" if micro else "")


def same_value(kind: TypeKind, left: object, right: object) -> bool:
    """Are two values the same for the purpose of "did this cell change"?"""
    if left is None or right is None:
        return left is None and right is None
    if kind is TypeKind.JSON:
        return _canonical_json(left) == _canonical_json(right)
    if kind is TypeKind.OTHER and type(left) is not type(right):
        return edit_text(kind, left) == edit_text(kind, right)  # an address vs the text typed
    left, right = _normalise(kind, left), _normalise(kind, right)
    try:
        return bool(left == right)
    except TypeError:  # e.g. naive vs aware datetimes
        return False


def _canonical_json(value: object) -> object:
    """JSON as one comparable form: a parsed document or a text, ignoring spacing and key order."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _normalise(kind: TypeKind, value: object) -> object:
    if kind is TypeKind.BOOLEAN and isinstance(value, int | bool):
        return bool(value)
    if (
        kind is TypeKind.TIME
        and isinstance(value, timedelta)
        and 0 <= value.total_seconds() < 86400
    ):
        # MySQL returns TIME as timedelta
        seconds = value.total_seconds()
        return time(
            int(seconds // 3600),
            int(seconds % 3600 // 60),
            int(seconds % 60),
            round((seconds % 1) * 1_000_000),
        )
    if kind in _TEMPORAL and isinstance(value, str):
        return _parse_temporal(kind, value)  # SQLite keeps dates and times as text
    if kind in _NUMERIC and not isinstance(value, bool):
        if isinstance(value, float) and math.isfinite(value):
            return Decimal(repr(value))  # 0.1 is the number 0.1, not its binary approximation
        if isinstance(value, int | Decimal):
            return Decimal(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytearray | memoryview):
        return bytes(value)
    return value


_NUMERIC = frozenset({TypeKind.INTEGER, TypeKind.DECIMAL, TypeKind.FLOAT})
_TEMPORAL = frozenset({TypeKind.DATE, TypeKind.TIME, TypeKind.DATETIME})


def _parse_temporal(kind: TypeKind, text: str) -> object:
    try:
        if kind is TypeKind.DATE:
            return date.fromisoformat(text.strip())
        if kind is TypeKind.TIME:
            return time.fromisoformat(text.strip())
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return text
