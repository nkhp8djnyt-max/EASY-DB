"""Optional native (SIMD) acceleration for SQL text scanning.

The C extension ``_native`` is built when a compiler is available (``setup.py``; failure to build
is not an error). It provides a statement splitter that skips string, comment and ``$$`` bodies
with AVX2 / SSE2 byte scans selected at run time. Everything falls back to the pure Python
implementation, which stays the reference: ``tests/core/simd`` fuzzes the two against each other.

Set ``SQL_ERD_STUDIO_NO_SIMD=1`` to ignore the extension altogether.
"""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

    from ..dialects.base import LexerSpec

_native: ModuleType | None
if os.environ.get("SQL_ERD_STUDIO_NO_SIMD"):
    _native = None
else:
    try:
        from . import _native
    except ImportError:  # not built on this platform
        _native = None

F_DASH_SPACE = 1 << 0
F_HASH = 1 << 1
F_NESTED = 1 << 2
F_DQ_STRING = 1 << 3
F_BACKSLASH = 1 << 4
F_E_PREFIX = 1 << 5
F_DOLLAR_QUOTE = 1 << 6
F_DOLLAR_NUM = 1 << 7
F_DOLLAR_NAMED = 1 << 8
F_QUESTION = 1 << 9
F_COLON = 1 << 10
F_AT_PARAM = 1 << 11
F_AT_VAR = 1 << 12
F_ID_DQ = 1 << 13
F_ID_BT = 1 << 14
F_ID_BR = 1 << 15

_IDENT_FLAGS = {('"', '"', True): F_ID_DQ, ("`", "`", True): F_ID_BT, ("[", "]", False): F_ID_BR}
_enabled = _native is not None


def available() -> bool:
    """Is the extension loaded (regardless of :func:`set_enabled`)?"""
    return _native is not None


def enabled() -> bool:
    return _enabled and _native is not None


def set_enabled(value: bool) -> None:
    """Switch the native paths on or off at run time (benchmarks and tests)."""
    global _enabled
    _enabled = value


def set_vector_enabled(value: bool) -> None:
    """Switch only the AVX2/SSE2 scanners (the native tokenizer keeps running, scalar)."""
    if _native is not None:
        _native.set_simd(value)


def implementation() -> str:
    """``avx2``, ``sse2``, ``scalar`` (native build without vector scanners) or ``python``."""
    return _native.implementation() if enabled() and _native is not None else "python"


def flags_for(spec: LexerSpec) -> int | None:
    """Lexer rules as the native bit set; ``None`` if ``spec`` uses a feature it does not model."""
    flags = 0
    if set(spec.line_comments) - {"--", "#"} or "--" not in spec.line_comments:
        return None
    for quote in spec.identifier_quotes:
        flag = _IDENT_FLAGS.get((quote.open, quote.close, quote.doubled))
        if flag is None:
            return None
        flags |= flag
    for enabled_, flag in (
        (spec.dash_comment_needs_space, F_DASH_SPACE),
        ("#" in spec.line_comments, F_HASH),
        (spec.nested_block_comments, F_NESTED),
        (spec.double_quote_is_string, F_DQ_STRING),
        (spec.backslash_escapes, F_BACKSLASH),
        (spec.escape_string_prefix, F_E_PREFIX),
        (spec.dollar_quoting, F_DOLLAR_QUOTE),
        (spec.dollar_numeric_params, F_DOLLAR_NUM),
        (spec.dollar_named_params, F_DOLLAR_NAMED),
        (spec.question_params, F_QUESTION),
        (spec.colon_params, F_COLON),
        (spec.at_params, F_AT_PARAM),
        (spec.at_variables, F_AT_VAR),
    ):
        if enabled_:
            flags |= flag
    return flags


def split_raw(text: str, flags: int) -> list[tuple[int, int, int, int, int, int]] | None:
    """Statement boundaries ``(begin, end, first_start, first_end, terminated, first_kind)``.

    ``None`` means "use the Python splitter" (extension missing or disabled, or the text holds
    something the native scanner does not model exactly).
    """
    if not enabled() or _native is None:
        return None
    result: list[tuple[int, int, int, int, int, int]] | None = _native.split(text, flags)
    return result


def find_first_of(text: str, start: int, needles: str) -> int:
    """Index of the first character of ``text[start:]`` that is one of the ASCII ``needles``."""
    if enabled() and _native is not None:
        return int(_native.find_first_of(text, start, needles))
    best = -1
    for needle in needles:
        index = text.find(needle, start)
        if index != -1 and (best == -1 or index < best):
            best = index
    return best


def status() -> dict[str, Any]:
    """What accelerates this process: the native scanner and NumPy's own SIMD dispatch."""
    return {"native": available(), "implementation": implementation(), "numpy": _numpy_features()}


def _numpy_features() -> list[str]:
    try:
        features: dict[str, bool] = import_module("numpy._core._multiarray_umath").__cpu_features__
    except (ImportError, AttributeError):  # pragma: no cover - layout differs between versions
        return []
    return sorted(name for name, on in features.items() if on)
