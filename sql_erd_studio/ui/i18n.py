"""Two-language UI strings (Russian, English).

Source strings in the code are English; ``tr`` looks them up in :data:`RU` when Russian is active.
A missing entry falls back to the English text, and a test fails if any ``tr("...")`` literal in
the UI has no Russian entry, so the two languages cannot drift apart.
"""

from __future__ import annotations

from .catalog_ru import RU

_language = "en"


def resolve_language(setting: str, system_locale: str) -> str:
    """``auto`` picks Russian for ``ru*`` locales and English otherwise."""
    if setting in ("ru", "en"):
        return setting
    return "ru" if system_locale.lower().startswith("ru") else "en"


def set_language(code: str) -> None:
    global _language
    if code not in ("ru", "en"):
        raise ValueError(f"unsupported language: {code}")
    _language = code


def current_language() -> str:
    return _language


def tr(text: str, **values: object) -> str:
    """Translate ``text``; ``{name}`` placeholders are filled from ``values``."""
    translated = RU.get(text, text) if _language == "ru" else text
    return translated.format(**values) if values else translated
