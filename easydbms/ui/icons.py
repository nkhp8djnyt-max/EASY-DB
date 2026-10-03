"""Small painted icons, and the application icon loaded from packaging/icon.png."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

_CACHE: dict[tuple[str, int, bool], QIcon] = {}


def dot_icon(color: str, size: int = 12, ring: bool = False) -> QIcon:
    """A filled circle (or an outlined ring when ``ring``) in ``color``."""
    key = (color, size, ring)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = 1.5
    radius = (size - 2 * margin) / 2
    center = QPointF(size / 2, size / 2)
    if ring:
        painter.setPen(QPen(QColor(color), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(color)))
    painter.drawEllipse(center, radius, radius)
    painter.end()
    icon = QIcon(pixmap)
    _CACHE[key] = icon
    return icon


def icon_file() -> Path:
    """packaging/icon.png: in the source tree, or where PyInstaller unpacks the bundled copy."""
    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if bundle else Path(__file__).resolve().parents[2]
    return root / "packaging" / "icon.png"


def app_icon() -> QIcon:
    """The application icon (the logo in packaging/icon.png)."""
    return QIcon(str(icon_file()))
