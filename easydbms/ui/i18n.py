"""Two-language UI strings (Ukrainian, English).

Source strings in the code are English; ``tr`` looks them up in :data:`UK` when Ukrainian is active.
A missing entry falls back to the English text, and a test fails if any ``tr("...")`` literal in
the UI has no Ukrainian entry, so the two languages cannot drift apart.
"""

from __future__ import annotations

from ..core.autocomplete.messages import use_translator
from .catalog_uk import UK

_language = "en"


def resolve_language(setting: str, system_locale: str) -> str:
    """``auto`` picks Ukrainian for ``uk*`` locales and English otherwise."""
    if setting in ("uk", "en"):
        return setting
    return "uk" if system_locale.lower().startswith("uk") else "en"


def set_language(code: str) -> None:
    global _language
    if code not in ("uk", "en"):
        raise ValueError(f"unsupported language: {code}")
    _language = code


def current_language() -> str:
    return _language


def tr(text: str, **values: object) -> str:
    """Translate ``text``; ``{name}`` placeholders are filled from ``values``."""
    translated = UK.get(text, text) if _language == "uk" else text
    return translated.format(**values) if values else translated


def _template(text: str) -> str:
    return UK.get(text, text) if _language == "uk" else text


use_translator(_template)  # the sentences autocomplete writes itself follow the UI language
