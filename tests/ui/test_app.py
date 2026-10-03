from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from sql_erd_studio.app import build_window
from sql_erd_studio.core.paths import AppPaths
from sql_erd_studio.ui.i18n import current_language
from sql_erd_studio.ui.theme import current_tokens


def test_the_window_is_built_from_the_saved_settings(
    qapp: QApplication, qtbot: QtBot, tmp_path: Path
) -> None:
    paths = AppPaths.under(tmp_path)
    paths.ensure()
    paths.settings_file.write_text('theme = "light"\nlanguage = "ru"\n')
    window = build_window(qapp, paths)
    qtbot.addWidget(window)
    assert current_language() == "ru"
    assert current_tokens().name == "light"
    assert "Нет подключения" in window._status_label.text()
    window.close()


def test_defaults_are_a_dark_theme_and_a_created_data_directory(
    qapp: QApplication, qtbot: QtBot, tmp_path: Path
) -> None:
    paths = AppPaths.under(tmp_path / "fresh")
    window = build_window(qapp, paths)
    qtbot.addWidget(window)
    assert current_tokens().name == "dark"
    assert paths.data_dir.is_dir()
    assert paths.app_db.exists()
    window.close()
