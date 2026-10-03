"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication

from .core.paths import AppPaths
from .core.services import build_services
from .ui.i18n import resolve_language, set_language
from .ui.main_window import APP_TITLE, MainWindow
from .ui.runtime import BackgroundRunner, EventBridge
from .ui.theme import apply_theme


def build_window(app: QApplication, paths: AppPaths | None = None) -> MainWindow:
    """Create services, apply language and theme, and return the (not yet shown) main window."""
    bridge = EventBridge(app)
    services = build_services(paths or AppPaths.default(), listener=bridge.post)
    set_language(resolve_language(services.settings.language, QLocale.system().name()))
    apply_theme(app, services.settings.theme)
    return MainWindow(services, bridge, BackgroundRunner(app))


def main(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(APP_TITLE)
    window = build_window(app)
    window.show()
    window.show_startup_notices()
    return app.exec()
