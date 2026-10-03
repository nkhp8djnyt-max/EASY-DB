from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import easydbms.ui as ui_package
from easydbms.ui.catalog_ru import RU
from easydbms.ui.i18n import current_language, resolve_language, set_language, tr

UI_DIR = Path(ui_package.__file__).parent
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def source_strings() -> dict[str, str]:
    """Every literal passed to ``tr()`` in the UI package, with the file that uses it."""
    found: dict[str, str] = {}
    for path in sorted(UI_DIR.rglob("*.py")):
        if path.name == "catalog_ru.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
            ):
                first = node.args[0]
                assert isinstance(first, ast.Constant), (
                    f"{path.name}:{node.lineno}: tr() needs a string literal so it can be "
                    "checked against the Russian catalog"
                )
                found[str(first.value)] = path.name
    return found


def test_every_ui_string_has_a_russian_translation() -> None:
    missing = sorted(s for s in source_strings() if s not in RU)
    assert missing == []


def test_the_catalog_has_no_stale_entries() -> None:
    stale = sorted(set(RU) - set(source_strings()))
    assert stale == []


def test_translations_keep_the_same_placeholders() -> None:
    for english, russian in RU.items():
        assert set(PLACEHOLDER.findall(english)) == set(PLACEHOLDER.findall(russian)), english


def test_translations_are_not_empty_or_identical_by_accident() -> None:
    technical = {"URL", "PRODUCTION"}  # same in both languages on purpose
    for english, russian in RU.items():
        assert russian.strip(), english
        if english not in technical:
            assert russian != english, english


def test_keyboard_mnemonics_survive_translation() -> None:
    for english, russian in RU.items():
        assert ("&" in english) == ("&" in russian), english


@pytest.mark.parametrize(
    ("setting", "locale", "expected"),
    [
        ("ru", "en_US", "ru"),
        ("en", "ru_RU", "en"),
        ("auto", "ru_RU", "ru"),
        ("auto", "RU", "ru"),
        ("auto", "en_US", "en"),
        ("auto", "de_DE", "en"),
        ("auto", "", "en"),
    ],
)
def test_resolve_language(setting: str, locale: str, expected: str) -> None:
    assert resolve_language(setting, locale) == expected


def test_tr_switches_language_and_fills_placeholders() -> None:
    set_language("en")
    assert tr("Connections") == "Connections"
    assert tr("Connected to {name}", name="db") == "Connected to db"
    set_language("ru")
    assert current_language() == "ru"
    assert tr("Connections") == "Подключения"
    assert tr("Connected to {name}", name="db") == "Подключено: db"
    assert tr("a string nobody translated") == "a string nobody translated"
    with pytest.raises(ValueError, match="unsupported"):
        set_language("de")
