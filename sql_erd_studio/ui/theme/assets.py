"""Small image assets the stylesheet needs (a check mark), generated on demand."""

from __future__ import annotations

import getpass
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

_SIZE = 32


def check_icon_path(color: str) -> str:
    """Path (forward slashes, as QSS wants) of a check-mark PNG in ``color``; created if missing."""
    directory = Path(tempfile.gettempdir()) / f"sql-erd-studio-{getpass.getuser()}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"check-{color.lstrip('#')}.png"
    if not path.exists():
        pixmap = QPixmap(_SIZE, _SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 4.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([QPointF(8, 17), QPointF(14, 23), QPointF(24, 10)])
        painter.end()
        pixmap.save(str(path), "PNG")
    return path.as_posix()
