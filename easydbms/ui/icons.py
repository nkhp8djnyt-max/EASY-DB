"""Small painted icons (no image files to ship)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
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


def app_icon(size: int = 256) -> QIcon:
    """The application icon: three linked table cards on a rounded blue tile (painted, so there
    is no image file to ship)."""
    pixmap = draw_app_icon(size)
    return QIcon(pixmap)


def draw_app_icon(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    unit = size / 100.0
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#2f6feb")))
    painter.drawRoundedRect(QRectF(4 * unit, 4 * unit, 92 * unit, 92 * unit), 20 * unit, 20 * unit)
    link = QPen(QColor("#cfe0ff"), 3.4 * unit)
    link.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(link)
    painter.drawLine(QPointF(34 * unit, 36 * unit), QPointF(66 * unit, 36 * unit))
    painter.drawLine(QPointF(30 * unit, 44 * unit), QPointF(42 * unit, 62 * unit))
    painter.drawLine(QPointF(70 * unit, 44 * unit), QPointF(58 * unit, 62 * unit))
    painter.setPen(Qt.PenStyle.NoPen)
    for x, y in ((14, 22), (60, 22), (37, 58)):  # card, then its header strip
        painter.setBrush(QBrush(QColor("#ffffff")))
        painter.drawRoundedRect(
            QRectF(x * unit, y * unit, 26 * unit, 20 * unit), 4 * unit, 4 * unit
        )
        painter.setBrush(QBrush(QColor("#9db8f0")))
        painter.drawRoundedRect(QRectF(x * unit, y * unit, 26 * unit, 6 * unit), 3 * unit, 3 * unit)
    painter.end()
    return pixmap
