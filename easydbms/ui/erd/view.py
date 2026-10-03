"""The diagram view: wheel zoom, background panning, fit-all and the zoom buttons."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .scene import ErdScene

MIN_ZOOM = 0.01
MAX_ZOOM = 2.5
_STEP = 1.18


class HelpPopup(QFrame):
    """The "?" button's list of mouse and keyboard controls."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty("card", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        lines = [
            tr("Mouse wheel — zoom"),
            tr("Drag the background — move around"),
            tr("Drag a card — rearrange (remembered)"),
            tr("Click a column — insert its name in the editor"),
            tr("Double-click a card — open the table's data"),
            tr("Right-click a card — more actions"),
            tr("Select a card — highlight related tables"),
            "Ctrl+0 — " + tr("fit everything"),
            "Ctrl++ / Ctrl+- — " + tr("zoom"),
        ]
        label = QLabel("\n".join(lines))
        layout.addWidget(label)


class ErdView(QGraphicsView):
    zoomChanged = Signal(float)

    def __init__(self, scene: ErdScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._controls = self._build_controls()
        for sequence, handler in (
            ("Ctrl+0", self.fit_all),
            ("Ctrl++", self.zoom_in),
            ("Ctrl+=", self.zoom_in),
            ("Ctrl+-", self.zoom_out),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)

    # ------------------------------------------------------------------ zoom

    @property
    def zoom(self) -> float:
        return float(self.transform().m11())

    def set_zoom(self, factor: float) -> None:
        factor = max(MIN_ZOOM, min(MAX_ZOOM, factor))
        self.resetTransform()
        self.scale(factor, factor)
        self.zoomChanged.emit(factor)

    def _zoom_by(self, ratio: float) -> None:
        target = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * ratio))
        ratio = target / self.zoom
        if ratio != 1.0:
            self.scale(ratio, ratio)
            self.zoomChanged.emit(self.zoom)

    def zoom_in(self) -> None:
        self._zoom_by(_STEP)

    def zoom_out(self) -> None:
        self._zoom_by(1 / _STEP)

    def fit_all(self, rect: QRectF | None = None) -> None:
        """Fit ``rect`` (default: every visible card) into the view, never enlarging past 100 %."""
        scene = self.scene()
        if not isinstance(scene, ErdScene):
            return
        target = rect if rect is not None and not rect.isNull() else scene.visible_bounds()
        if target.isNull():
            return
        target = target.adjusted(-40, -40, 40, 40)
        self.fitInView(target, Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoom > 1.0:
            self.set_zoom(1.0)
            self.centerOn(target.center())
        elif self.zoom < MIN_ZOOM:
            self.set_zoom(MIN_ZOOM)
            self.centerOn(target.center())
        self.zoomChanged.emit(self.zoom)

    def center_on_rect(self, rect: QRectF) -> None:
        self.centerOn(rect.center())

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self._zoom_by(_STEP ** (delta / 120.0))
            event.accept()
        else:
            super().wheelEvent(event)

    # ------------------------------------------------------------------ controls overlay

    def _build_controls(self) -> QWidget:
        box = QWidget(self)
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.zoom_in_button = self._button("+", tr("Zoom in (Ctrl++)"), self.zoom_in)
        self.zoom_out_button = self._button("-", tr("Zoom out (Ctrl+-)"), self.zoom_out)
        self.fit_button = self._button("⛶", tr("Fit everything (Ctrl+0)"), self.fit_all)
        self.help_button = self._button("?", tr("Controls"), self._show_help)
        for button in (
            self.zoom_in_button,
            self.zoom_out_button,
            self.fit_button,
            self.help_button,
        ):
            layout.addWidget(button)
        box.adjustSize()
        return box

    def _button(self, text: str, tip: str, handler: Callable[[], object]) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFixedSize(30, 30)
        button.setProperty("compact", True)
        button.clicked.connect(handler)
        return button

    def _show_help(self) -> None:
        popup = HelpPopup(self)
        popup.adjustSize()
        origin = self.help_button.mapToGlobal(QPoint(0, 0))
        popup.move(origin.x(), origin.y() - popup.sizeHint().height() - 6)
        popup.show()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._controls.move(12, self.height() - self._controls.height() - 12)
        self._controls.raise_()
