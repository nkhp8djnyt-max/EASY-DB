from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from easydbms.core.connections import ConnectionColor
from easydbms.core.storage import SettingsStore
from easydbms.ui.theme import (
    COLOR_HEX,
    THEMES,
    apply_theme,
    build_stylesheet,
    color_hex,
    current_tokens,
    resolve_theme,
    stylesheet,
)
from easydbms.ui.theme.assets import check_icon_path

from .conftest import Env
from .test_main_window import make_window


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


# ---------------------------------------------------------------------------- follow the system


@pytest.fixture
def system_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Qt.ColorScheme], None]:
    """Stand in for the operating system's setting (the offscreen platform has none)."""
    state = {"scheme": Qt.ColorScheme.Dark}
    monkeypatch.setattr(stylesheet, "system_color_scheme", lambda: state["scheme"])

    def switch(scheme: Qt.ColorScheme) -> None:
        state["scheme"] = scheme
        hints = QGuiApplication.styleHints()
        assert hints is not None
        hints.colorSchemeChanged.emit(scheme)  # what the platform does when the user switches

    return switch


def test_system_follows_the_operating_systems_colour_scheme(
    system_scheme: Callable[[Qt.ColorScheme], None],
) -> None:
    system_scheme(Qt.ColorScheme.Light)
    assert resolve_theme("system") == "light"
    system_scheme(Qt.ColorScheme.Dark)
    assert resolve_theme("system") == "dark"
    assert resolve_theme("light") == "light"
    assert resolve_theme("dark") == "dark"


def test_applying_the_system_theme_applies_the_resolved_one(
    qapp: QApplication, system_scheme: Callable[[Qt.ColorScheme], None]
) -> None:
    system_scheme(Qt.ColorScheme.Light)
    apply_theme(qapp, "system")
    assert current_tokens().name == "light"
    system_scheme(Qt.ColorScheme.Dark)
    apply_theme(qapp, "system")
    assert current_tokens().name == "dark"


def test_the_window_follows_the_system_while_the_system_theme_is_chosen(
    env: Env, qtbot: QtBot, system_scheme: Callable[[Qt.ColorScheme], None]
) -> None:
    system_scheme(Qt.ColorScheme.Dark)
    window = make_window(env, qtbot)
    window.set_theme("system")
    assert SettingsStore(env.services.paths.settings_file).load().theme == "system"
    assert current_tokens().name == "dark"
    system_scheme(Qt.ColorScheme.Light)
    qtbot.waitUntil(lambda: current_tokens().name == "light", timeout=3000)
    window.set_theme("dark")  # an explicit choice is not overridden by the system
    system_scheme(Qt.ColorScheme.Light)
    system_scheme(Qt.ColorScheme.Dark)
    assert current_tokens().name == "dark"
    window.set_theme("dark")


def test_the_view_menu_offers_the_system_theme(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    labels = [a.text() for m in window.menuBar().findChildren(QMenu) for a in m.actions()]
    assert "System theme" in labels
    assert labels.index("System theme") < labels.index("Dark theme") < labels.index("Light theme")
