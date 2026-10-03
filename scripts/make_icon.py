"""Rebuild packaging/icon.png and packaging/icon.ico from the logo image.

Run: ``python scripts/make_icon.py [SOURCE]`` (needs PySide6). SOURCE is any image Qt can read,
found by its content rather than its extension; it defaults to packaging/icon.png. The result is
a real PNG and a multi-size Windows ICO, so PyInstaller and Explorer accept them. ``icon.icns``
is not kept in the repository: PyInstaller makes it from the PNG when Pillow is installed.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QImageWriter

OUT = Path(__file__).resolve().parent.parent / "packaging"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def square(image: QImage) -> QImage:
    """The largest centred square of ``image``."""
    side = min(image.width(), image.height())
    return image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side)


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    QImageWriter(buffer, QByteArray(b"png")).write(image)
    buffer.close()
    return bytes(data.data())


def ico_bytes(image: QImage) -> bytes:
    """A Windows ICO holding ``image`` at every size in ICO_SIZES, each entry PNG-compressed."""
    frames = [
        png_bytes(
            image.scaled(
                size,
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).convertToFormat(QImage.Format.Format_ARGB32)
        )
        for size in ICO_SIZES
    ]
    offset = 6 + 16 * len(frames)
    directory = b""
    for size, frame in zip(ICO_SIZES, frames, strict=True):
        edge = size % 256  # width and height are one byte each, 0 standing for 256
        directory += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(frame), offset)
        offset += len(frame)
    return struct.pack("<HHH", 0, 1, len(frames)) + directory + b"".join(frames)


def main(argv: list[str]) -> int:
    app = QGuiApplication([])
    source = Path(argv[1]) if len(argv) > 1 else OUT / "icon.png"
    image = QImage(str(source))
    if image.isNull():
        print(f"cannot read an image from {source}", file=sys.stderr)
        return 1
    image = square(image)
    OUT.mkdir(exist_ok=True)
    (OUT / "icon.png").write_bytes(png_bytes(image))
    (OUT / "icon.ico").write_bytes(ico_bytes(image))
    print(f"wrote {OUT / 'icon.png'} and {OUT / 'icon.ico'}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
