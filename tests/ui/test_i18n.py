from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import easydbms.ui as ui_package
from easydbms.core import autocomplete
from easydbms.core.autocomplete.snippets import SNIPPETS
from easydbms.ui.catalog_uk import UK
from easydbms.ui.i18n import current_language, resolve_language, set_language, tr

UI_DIR = Path(ui_package.__file__).parent
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def source_strings() -> dict[str, str]:
    """Every literal passed to ``tr()`` in the UI package (and ``t()`` in autocomplete's core)."""
    found: dict[str, str] = {}
    files = [(p, "tr") for p in sorted(UI_DIR.rglob("*.py")) if p.name != "catalog_uk.py"]
    files += [(p, "t") for p in sorted(Path(autocomplete.__file__).parent.rglob("*.py"))]
    for path, function in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == function
                and node.args
                and not (
                    isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "description"
                )
            ):
                first = node.args[0]
                assert isinstance(first, ast.Constant), (
                    f"{path.name}:{node.lineno}: tr() needs a string literal so it can be "
                    "checked against the Ukrainian catalog"
                )
                found[str(first.value)] = path.name
    for snippet in SNIPPETS:  # their descriptions reach the user through t() in the provider
        assert snippet.description, f"snippet {snippet.trigger!r} has no description"
        found[snippet.description] = "snippets.py"
    return found


def test_every_ui_string_has_a_ukrainian_translation() -> None:
    missing = sorted(s for s in source_strings() if s not in UK)
    assert missing == []


def test_the_catalog_has_no_stale_entries() -> None:
    stale = sorted(set(UK) - set(source_strings()))
    assert stale == []


def test_translations_keep_the_same_placeholders() -> None:
    for english, ukrainian in UK.items():
        assert set(PLACEHOLDER.findall(english)) == set(PLACEHOLDER.findall(ukrainian)), english


def test_translations_are_not_empty_or_identical_by_accident() -> None:
    technical = {
        "URL",
        "PRODUCTION",
        "SSL / TLS",
        "ssh-agent",
        "Supabase",
        "Neon",
        "PlanetScale",
        "CockroachDB Cloud",
        "AWS RDS / Aurora (IAM)",
        "Google Cloud SQL (IAM)",
        "Azure Database (Microsoft Entra ID)",
    }  # same in both languages on purpose
    for english, ukrainian in UK.items():
        assert ukrainian.strip(), english
        if english not in technical:
            assert ukrainian != english, english


def test_keyboard_mnemonics_survive_translation() -> None:
    for english, ukrainian in UK.items():
        assert ("&" in english) == ("&" in ukrainian), english


@pytest.mark.parametrize(
    ("setting", "locale", "expected"),
    [
        ("uk", "en_US", "uk"),
        ("en", "uk_UA", "en"),
        ("auto", "uk_UA", "uk"),
        ("auto", "UK", "uk"),
        ("auto", "ru_RU", "en"),  # Russian is not offered any more
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
    set_language("uk")
    assert current_language() == "uk"
    assert tr("Connections") == UK["Connections"]
    assert tr("Connected to {name}", name="db") == UK["Connected to {name}"].format(name="db")
    assert tr("Connections") != "Connections"
    assert tr("a string nobody translated") == "a string nobody translated"
    with pytest.raises(ValueError, match="unsupported"):
        set_language("de")
    with pytest.raises(ValueError, match="unsupported"):
        set_language("ru")


def test_the_catalog_is_ukrainian_and_has_no_russian_letters() -> None:
    russian_only = set("ыэъЫЭЪёЁ")  # letters that do not exist in the Ukrainian alphabet
    for english, ukrainian in UK.items():
        assert not (set(ukrainian) & russian_only), (english, ukrainian)
