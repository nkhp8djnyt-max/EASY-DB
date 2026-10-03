from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from easydbms.core.connections import ConnectionColor
from easydbms.ui.theme import (
    COLOR_HEX,
    THEMES,
    apply_theme,
    build_stylesheet,
    color_hex,
    current_tokens,
)
from easydbms.ui.theme.assets import check_icon_path


@pytest.mark.parametrize("name", ["dark", "light"])
def test_stylesheet_is_complete_and_uses_the_tokens(name: str) -> None:
    tokens = THEMES[name]
    sheet = build_stylesheet(tokens)
    assert tokens.accent in sheet
    assert tokens.bg in sheet
    assert "{t." not in sheet  # no unformatted placeholders
    assert sheet.count("{") == sheet.count("}")


def test_dark_theme_matches_the_sql_academy_palette() -> None:
    dark = THEMES["dark"]
    assert (dark.bg, dark.panel, dark.accent) == ("#1a1a1a", "#222222", "#2f7bf5")


def test_all_tokens_are_valid_hex_colours() -> None:
    for tokens in THEMES.values():
        for field in tokens.__slots__:
            value = getattr(tokens, field)
            if field != "name":
                assert re.fullmatch(r"#[0-9a-f]{6}", value), (tokens.name, field, value)


def test_connection_colours_are_defined_for_every_label() -> None:
    assert set(COLOR_HEX) == set(ConnectionColor)
    assert color_hex(ConnectionColor.RED) == COLOR_HEX[ConnectionColor.RED]
    assert color_hex(None) == "#6e7681"
    assert color_hex(None, "#000000") == "#000000"


def test_apply_theme_sets_the_stylesheet_and_remembers_the_name(qapp: QApplication) -> None:
    apply_theme(qapp, "light")
    assert current_tokens().name == "light"
    assert THEMES["light"].bg in qapp.styleSheet()
    apply_theme(qapp, "dark")
    assert current_tokens().name == "dark"


def test_check_icon_is_generated_once(qapp: QApplication) -> None:
    path = Path(check_icon_path("#ffffff"))
    assert path.exists()
    assert path.suffix == ".png"
    assert path.stat().st_size > 0
    first_mtime = path.stat().st_mtime_ns
    assert check_icon_path("#ffffff") == path.as_posix()
    assert path.stat().st_mtime_ns == first_mtime  # cached, not rewritten
