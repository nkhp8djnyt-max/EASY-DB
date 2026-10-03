"""Shapes of the per-dialect vocabulary used by autocomplete and highlighting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FunctionCategory(StrEnum):
    AGGREGATE = "aggregate"
    WINDOW = "window"
    STRING = "string"
    NUMERIC = "numeric"
    DATETIME = "datetime"
    CONDITIONAL = "conditional"
    JSON = "json"
    ARRAY = "array"
    SYSTEM = "system"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class FunctionSpec:
    """A function offered by autocomplete: ``name`` is upper-case, ``signature`` is display text."""

    name: str
    signature: str
    category: FunctionCategory


_C = FunctionCategory

#: Short codes keep the vocabulary tables compact and readable.
CATEGORY_CODES: dict[str, FunctionCategory] = {
    "agg": _C.AGGREGATE,
    "win": _C.WINDOW,
    "str": _C.STRING,
    "num": _C.NUMERIC,
    "dt": _C.DATETIME,
    "cond": _C.CONDITIONAL,
    "json": _C.JSON,
    "arr": _C.ARRAY,
    "sys": _C.SYSTEM,
    "other": _C.OTHER,
}


def build_functions(rows: tuple[tuple[str, str, str], ...]) -> tuple[FunctionSpec, ...]:
    """Turn ``(category_code, NAME, signature)`` rows into specs, rejecting duplicates."""
    seen: set[str] = set()
    specs: list[FunctionSpec] = []
    for code, name, signature in rows:
        if name in seen:
            raise ValueError(f"duplicate function in vocabulary: {name}")
        seen.add(name)
        specs.append(FunctionSpec(name, signature, CATEGORY_CODES[code]))
    return tuple(specs)
