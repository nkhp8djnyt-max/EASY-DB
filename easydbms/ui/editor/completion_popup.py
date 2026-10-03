"""The suggestion list shown under the cursor: kind icon, name, type on the right, details pane."""

from __future__ import annotations

import html
from collections.abc import Sequence

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ...core.autocomplete import Completion, Kind
from ..theme import current_tokens

ROW_HEIGHT = 24
MAX_ROWS = 10
LIST_WIDTH = 380
DOC_WIDTH = 250
_BADGE = 16

#: Badge letter and colour of every kind of suggestion (the same in both themes).
BADGES: dict[Kind, tuple[str, str]] = {
    Kind.TABLE: ("T", "#2f7bf5"),
    Kind.VIEW: ("V", "#5b86d6"),
    Kind.CTE: ("W", "#8a6fd8"),
    Kind.SCHEMA: ("D", "#7d8590"),
    Kind.COLUMN: ("c", "#2e9d4a"),
    Kind.ALIAS: ("a", "#2bb3a7"),
    Kind.KEYWORD: ("K", "#a371f7"),
    Kind.FUNCTION: ("f", "#e98a36"),
    Kind.TYPE: ("t", "#c99a1c"),
    Kind.SNIPPET: ("{}", "#c0792b"),
    Kind.JOIN: ("J", "#d0629f"),
    Kind.STAR: ("*", "#2f7bf5"),
}


class CompletionModel(QAbstractListModel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: Sequence[Completion] = ()
        self.prefix = ""

    def set_items(self, items: Sequence[Completion], prefix: str) -> None:
        self.beginResetModel()
        self._items = items
        self.prefix = prefix
        self.endResetModel()

    def item(self, row: int) -> Completion | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        item = self.item(index.row())
        if item is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return item.label
        if role == Qt.ItemDataRole.UserRole:
            return item
        return None


class _Delegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        model = index.model()
        item = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, Completion) or not isinstance(model, CompletionModel):
            return
        tokens = current_tokens()
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(tokens.selection))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(tokens.hover))

        glyph, color = BADGES.get(item.kind, ("?", "#7d8590"))
        badge = QRect(rect.left() + 6, rect.top() + (rect.height() - _BADGE) // 2, _BADGE, _BADGE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(badge, 4, 4)
        small = QFont(option.font)
        small.setBold(True)
        small.setPointSizeF(max(option.font.pointSizeF() - 1.5, 6.0))
        painter.setFont(small)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, glyph)

        painter.setFont(option.font)
        metrics = QFontMetrics(option.font)
        right = rect.right() - 8
        left = badge.right() + 8
        if item.detail:
            room = max((right - left) * 2 // 5, 60)
            detail = metrics.elidedText(item.detail, Qt.TextElideMode.ElideRight, room)
            width = metrics.horizontalAdvance(detail)
            painter.setPen(QColor(tokens.text_muted))
            painter.drawText(
                QRect(right - width, rect.top(), width, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                detail,
            )
            right -= width + 12
        self._paint_label(painter, option.font, item.label, model.prefix, left, right, rect)
        painter.restore()

    @staticmethod
    def _paint_label(
        painter: QPainter, font: QFont, label: str, prefix: str, left: int, right: int, rect: QRect
    ) -> None:
        tokens = current_tokens()
        metrics = QFontMetrics(font)
        text = metrics.elidedText(label, Qt.TextElideMode.ElideRight, max(right - left, 10))
        start = text.lower().find(prefix.lower()) if prefix else -1
        if start < 0 or "…" in text[:-1] or start + len(prefix) > len(text):
            painter.setPen(QColor(tokens.text))
            painter.drawText(
                QRect(left, rect.top(), right - left, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            return
        bold = QFont(font)
        bold.setBold(True)
        x = left
        for part, mark in (
            (text[:start], False),
            (text[start : start + len(prefix)], True),
            (text[start + len(prefix) :], False),
        ):
            if not part:
                continue
            painter.setFont(bold if mark else font)
            painter.setPen(QColor(tokens.accent if mark else tokens.text))
            width = QFontMetrics(bold if mark else font).horizontalAdvance(part)
            painter.drawText(
                QRect(x, rect.top(), width + 2, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                part,
            )
            x += width
        painter.setFont(font)

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(LIST_WIDTH, ROW_HEIGHT)


class CompletionPopup(QFrame):
    """Never takes the keyboard focus: the editor keeps typing and forwards the list keys."""

    #: A row was double-clicked.
    activated = Signal(object)

    def __init__(self, owner: QWidget) -> None:
        super().__init__(
            owner,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("completionPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.model = CompletionModel(self)
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(_Delegate(self.view))
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setUniformItemSizes(True)
        self.view.setMouseTracking(True)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.doubleClicked.connect(self._on_double_clicked)
        self.view.selectionModel().currentChanged.connect(self._show_doc)
        self.doc = QLabel()
        self.doc.setObjectName("completionDoc")
        self.doc.setWordWrap(True)
        self.doc.setTextFormat(Qt.TextFormat.RichText)
        self.doc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.doc.setFixedWidth(DOC_WIDTH)
        self.doc.setMargin(10)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.view)
        layout.addWidget(self.doc)

    # ------------------------------------------------------------------ content

    def show_items(self, items: Sequence[Completion], prefix: str) -> None:
        self.model.set_items(items, prefix)
        rows = min(len(items), MAX_ROWS)
        self.view.setFixedSize(LIST_WIDTH, rows * ROW_HEIGHT + 2)
        self.select(0)

    def select(self, row: int) -> None:
        count = self.model.rowCount()
        if count == 0:
            return
        row %= count
        index = self.model.index(row)
        self.view.setCurrentIndex(index)
        self.view.scrollTo(index)
        self._show_doc()

    def step(self, delta: int, *, wrap: bool = True) -> None:
        """Move the selection by ``delta`` rows."""
        count = self.model.rowCount()
        if count == 0:
            return
        row = self.view.currentIndex().row() + delta
        self.select(row if wrap else max(0, min(row, count - 1)))

    def current(self) -> Completion | None:
        return self.model.item(self.view.currentIndex().row())

    def row_count(self) -> int:
        return self.model.rowCount()

    def _show_doc(self, *_: object) -> None:
        item = self.current()
        text = ""
        if item is not None and (item.doc or item.detail):
            body = html.escape(item.doc or item.detail).replace("\n", "<br>")
            title = html.escape(item.label)
            text = f"<b>{title}</b><br><span>{body}</span>"
        self.doc.setText(text)
        self.doc.setVisible(bool(text))
        self.adjustSize()

    def _on_double_clicked(self, index: QModelIndex) -> None:
        item = self.model.item(index.row())
        if item is not None:
            self.activated.emit(item)

    # ------------------------------------------------------------------ placement

    def place(self, anchor: QPoint, line_height: int) -> None:
        """Show under ``anchor`` (global, top left of the text being replaced), or above it."""
        self.adjustSize()
        size = self.sizeHint()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 4000, 4000)
        x = min(anchor.x() - 28, area.right() - size.width())
        x = max(x, area.left())
        below = anchor.y() + line_height + 2
        y = below if below + size.height() <= area.bottom() else anchor.y() - size.height() - 2
        self.move_to(QPoint(x, max(y, area.top())))
        if not self.isVisible():
            self.show()

    def move_to(self, point: QPoint) -> None:
        QWidget.move(self, point)
