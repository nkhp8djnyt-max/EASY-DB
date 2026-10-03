"""The few sentences autocomplete writes itself ("4 cols", "foreign key …").

The core knows no UI toolkit and no language: sentences are English templates passed through
:func:`t`, and the application installs a translator with :func:`use_translator` (the UI points it
at its Ukrainian catalog). Without one, the English template is used as is.
"""

from __future__ import annotations

from collections.abc import Callable

_translate: Callable[[str], str] = lambda template: template  # noqa: E731


def use_translator(translate: Callable[[str], str] | None) -> None:
    """Install ``translate`` (template -> template in the user's language); ``None`` resets."""
    global _translate
    _translate = translate or (lambda template: template)


def t(template: str, **values: object) -> str:
    """``template`` in the user's language, its ``{name}`` placeholders filled from ``values``."""
    text = _translate(template)
    return text.format(**values) if values else text
