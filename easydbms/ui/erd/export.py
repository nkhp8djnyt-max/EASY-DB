"""Save the diagram as an image (PNG), a vector drawing (SVG), a PDF, or as Mermaid / DBML text."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QByteArray, QMarginsF, QRectF, QSize, QSizeF
from PySide6.QtGui import QColor, QImage, QImageWriter, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgGenerator

from ...core.erd import TextFormat, export_text
from ..i18n import tr
from .scene import ErdScene

_MARGIN = 28.0
#: PNG pixels per scene unit: sharp enough to print, small enough to keep the file reasonable.
_PNG_SCALE = 2.0
#: A PNG larger than this (in pixels per side) is scaled down to fit.
_MAX_PIXELS = 16000
#: The PDF format caps a page at 14400 points.
_MAX_POINTS = 14000.0


class ExportFormat(StrEnum):
    PNG = "png"
    SVG = "svg"
    PDF = "pdf"
    MERMAID = "mermaid"
    DBML = "dbml"

    @property
    def suffix(self) -> str:
        return {"mermaid": ".mmd"}.get(self.value, "." + self.value)


def file_filters() -> list[tuple[ExportFormat, str]]:
    """``(format, "label (*.ext)")`` for a save dialog, in the order they are offered."""
    return [
        (ExportFormat.PNG, tr("PNG image") + " (*.png)"),
        (ExportFormat.SVG, tr("SVG drawing") + " (*.svg)"),
        (ExportFormat.PDF, tr("PDF document") + " (*.pdf)"),
        (ExportFormat.MERMAID, tr("Mermaid diagram (text)") + " (*.mmd *.md)"),
        (ExportFormat.DBML, tr("DBML schema (text)") + " (*.dbml)"),
    ]


def format_for_path(path: str | Path) -> ExportFormat | None:
    suffix = Path(path).suffix.lower()
    return {
        ".png": ExportFormat.PNG,
        ".svg": ExportFormat.SVG,
        ".pdf": ExportFormat.PDF,
        ".mmd": ExportFormat.MERMAID,
        ".mermaid": ExportFormat.MERMAID,
        ".md": ExportFormat.MERMAID,
        ".dbml": ExportFormat.DBML,
    }.get(suffix)


def export_diagram(
    scene: ErdScene, path: str | Path, fmt: ExportFormat | None = None
) -> ExportFormat:
    """Write the diagram to ``path`` (the format follows its suffix unless ``fmt`` is given).

    Raises ``ValueError`` when there is nothing to draw or the suffix is not one we write, and
    ``FileNotFoundError`` when its folder is missing and ``OSError`` when it cannot be written.
    """
    chosen = fmt or format_for_path(path)
    if chosen is None:
        raise ValueError(tr("Unknown file type: use .png, .svg, .pdf, .mmd or .dbml."))
    target = Path(path)
    if not target.parent.is_dir():
        raise FileNotFoundError(tr("The folder {path} does not exist.", path=str(target.parent)))
    if chosen in (ExportFormat.MERMAID, ExportFormat.DBML):
        model = scene.model
        if model is None or not model.tables:
            raise ValueError(tr("There is nothing to export."))
        text_format = TextFormat.MERMAID if chosen is ExportFormat.MERMAID else TextFormat.DBML
        target.write_text(export_text(model, text_format), encoding="utf-8")
        return chosen
    source = scene.visible_bounds()
    if source.isEmpty():
        raise ValueError(tr("There is nothing to export."))
    source = source.adjusted(-_MARGIN, -_MARGIN, _MARGIN, _MARGIN)
    background = scene.backgroundBrush().color()
    if not background.isValid() or background.alpha() == 0:
        background = QColor("#ffffff")
    selected = scene.selectedItems()
    scene.clearSelection()  # the selection outline and the dimming are not part of the picture
    try:
        if chosen is ExportFormat.PNG:
            _png(scene, source, target, background)
        elif chosen is ExportFormat.SVG:
            _svg(scene, source, target, background)
        else:
            _pdf(scene, source, target, background)
    finally:
        for item in selected:
            item.setSelected(True)
    return chosen


def _paint(
    scene: ErdScene, painter: QPainter, source: QRectF, target: QRectF, fill: QColor
) -> None:
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.TextAntialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
    )
    painter.fillRect(target, fill)
    scene.render(painter, target, source)


def _png(scene: ErdScene, source: QRectF, target: Path, background: QColor) -> None:
    scale = min(_PNG_SCALE, _MAX_PIXELS / max(source.width(), source.height()))
    width, height = max(1, round(source.width() * scale)), max(1, round(source.height() * scale))
    image = QImage(QSize(width, height), QImage.Format.Format_ARGB32)
    image.fill(background)
    painter = QPainter(image)
    _paint(scene, painter, source, QRectF(0, 0, width, height), background)
    painter.end()
    writer = QImageWriter(str(target), QByteArray(b"png"))
    if not writer.write(image):
        raise OSError(f"{tr('Cannot write {path}', path=str(target))}: {writer.errorString()}")


def _svg(scene: ErdScene, source: QRectF, target: Path, background: QColor) -> None:
    generator = QSvgGenerator()
    generator.setFileName(str(target))
    size = QSize(max(1, round(source.width())), max(1, round(source.height())))
    generator.setSize(size)
    generator.setViewBox(QRectF(0, 0, size.width(), size.height()))
    generator.setTitle("EasyDBMS diagram")
    painter = QPainter(generator)
    _paint(scene, painter, source, QRectF(0, 0, size.width(), size.height()), background)
    painter.end()
    if not target.exists():
        raise OSError(tr("Cannot write {path}", path=str(target)))


def _pdf(scene: ErdScene, source: QRectF, target: Path, background: QColor) -> None:
    scale = min(1.0, _MAX_POINTS / max(source.width(), source.height()))
    width, height = source.width() * scale, source.height() * scale
    writer = QPdfWriter(str(target))
    writer.setResolution(72)  # one painter unit is one point
    writer.setPageSize(QPageSize(QSizeF(width, height), QPageSize.Unit.Point))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    writer.setTitle("EasyDBMS diagram")
    painter = QPainter(writer)
    _paint(scene, painter, source, QRectF(0, 0, width, height), background)
    painter.end()
    if not target.exists():
        raise OSError(tr("Cannot write {path}", path=str(target)))
