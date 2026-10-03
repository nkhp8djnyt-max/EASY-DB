"""Write packaging/icon.png (+ icon.ico) from the painted application icon.

Run: ``python scripts/make_icon.py`` (needs PySide6). PyInstaller turns the PNG into the platform
format when Pillow is installed, so ``icon.icns`` is not kept in the repository.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication

from easydbms.ui.icons import draw_app_icon

OUT = Path(__file__).resolve().parent.parent / "packaging"


def main() -> None:
    app = QGuiApplication([])
    OUT.mkdir(exist_ok=True)
    draw_app_icon(512).save(str(OUT / "icon.png"), "PNG")
    draw_app_icon(256).save(str(OUT / "icon.ico"), "ICO")
    print(f"wrote {OUT / 'icon.png'} and {OUT / 'icon.ico'}")
    del app


if __name__ == "__main__":
    main()
