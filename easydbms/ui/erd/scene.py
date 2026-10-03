"""The diagram scene: builds cards and relation lines from an :class:`ErdModel`."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ...core.erd import ErdModel, assign_lanes, layout
from ...core.schema import Column, Table, TableKey
from ..theme import Tokens
from .colors import ErdColors, erd_colors
from .items import LOD_TITLE, RelationLine, TableCard

#: Above this many tables, a diagram that spans several schemas is grouped by schema.
GROUP_FROM = 60


class GroupFrame(QGraphicsItem):
    """A labelled frame around the tables of one schema (large multi-schema diagrams)."""

    def __init__(self, name: str, rect: QRectF, colors: ErdColors) -> None:
        super().__init__()
        self._name = name
        self._rect = rect
        self._colors = colors
        self.setZValue(-2)

    def set_colors(self, colors: ErdColors) -> None:
        self._colors = colors
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-1, -1, 1, 1)

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        colors = self._colors
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(colors.group_border, 1.0, Qt.PenStyle.DashLine))
        painter.setBrush(colors.group_fill)
        painter.drawRoundedRect(self._rect, 10, 10)
        painter.setPen(colors.muted)
        font = QFont()
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        if option.levelOfDetailFromTransform(painter.worldTransform()) >= LOD_TITLE:
            painter.drawText(
                QRectF(self._rect.x() + 14, self._rect.y(), self._rect.width() - 28, 34),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self._name,
            )


class ErdScene(QGraphicsScene):
    #: A card was double-clicked (or "Open data" chosen): the :class:`Table`.
    tableOpenRequested = Signal(object)
    #: A column row (or the title bar, ``None``) was clicked: ``(Table, Column | None)``.
    columnClicked = Signal(object, object)
    #: Right click on a card: ``(Table, QPoint)`` in screen coordinates.
    menuRequested = Signal(object, object)
    #: Cards were dragged: ``{TableKey: (x, y)}`` of every card that moved.
    cardsMoved = Signal(object)
    #: The single selected card changed (``None`` when none or several are selected).
    focusChanged = Signal(object)

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._colors = erd_colors(tokens)
        self._cards: dict[TableKey, TableCard] = {}
        self._edges: list[RelationLine] = []
        self._edges_of: dict[TableKey, list[RelationLine]] = {}
        self._frames: list[GroupFrame] = []
        self._model: ErdModel | None = None
        self._building = False
        self._focus: TableKey | None = None
        self._filter = ""
        self.selectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------------ building

    @property
    def model(self) -> ErdModel | None:
        return self._model

    def card(self, key: TableKey) -> TableCard | None:
        return self._cards.get(key)

    def cards(self) -> list[TableCard]:
        return list(self._cards.values())

    def edges(self) -> list[RelationLine]:
        return list(self._edges)

    def build(
        self, model: ErdModel, saved: Mapping[TableKey, tuple[float, float]] | None = None
    ) -> None:
        """Replace the contents with ``model``; ``saved`` positions win over the layout."""
        self._building = True
        self.blockSignals(True)
        try:
            self.clear()
        finally:
            self.blockSignals(False)
        self._cards, self._edges, self._edges_of, self._frames = {}, [], {}, []
        self._model = model
        self._focus = None
        schemas = {t.schema for t in model.tables}
        show_schema = len(schemas) > 1
        for table in model.tables:
            self._cards[table.key] = TableCard(
                table,
                self._colors,
                self,
                show_schema=show_schema,
                junction=table.key in model.junctions,
            )
        sizes = {key: card.size() for key, card in self._cards.items()}
        groups = (
            {key: key.schema for key in self._cards}
            if show_schema and len(self._cards) > GROUP_FROM
            else None
        )
        placed = layout(sizes, [(r.child, r.parent) for r in model.relations], groups=groups)
        for key, card in self._cards.items():
            auto = placed.positions.get(key, (0.0, 0.0))
            card.layout_pos = QPointF(*auto)
            x, y = (saved or {}).get(key) or auto
            card.setPos(x, y)
            self.addItem(card)
        for name, (x, y, w, h) in placed.groups.items():
            frame = GroupFrame(name, QRectF(x, y, w, h), self._colors)
            self._frames.append(frame)
            self.addItem(frame)

        spans = []
        pairs = []
        for relation in model.relations:
            child, parent = self._cards.get(relation.child), self._cards.get(relation.parent)
            if child is None or parent is None:
                continue
            pairs.append((relation, child, parent))
            spans.append((child.scene_rect().right, parent.scene_rect().x))
        for (relation, child, parent), lane in zip(pairs, assign_lanes(spans), strict=True):
            channel = placed.channels.get((relation.child, relation.parent), ())
            line = RelationLine(relation, child, parent, self._colors, lane, channel)
            self._edges.append(line)
            self._edges_of.setdefault(relation.child, []).append(line)
            if relation.parent != relation.child:
                self._edges_of.setdefault(relation.parent, []).append(line)
            self.addItem(line)
        self._building = False
        bounds = self.itemsBoundingRect()
        margin = max(bounds.width(), bounds.height(), 400.0) * 0.5
        self.setSceneRect(bounds.adjusted(-margin, -margin, margin, margin))
        if self._filter:
            self.set_filter(self._filter)

    def set_tokens(self, tokens: Tokens) -> None:
        self._tokens = tokens
        self._colors = erd_colors(tokens)
        for card in self._cards.values():
            card.set_colors(self._colors)
        for line in self._edges:
            line.set_colors(self._colors)
        for frame in self._frames:
            frame.set_colors(self._colors)

    # ------------------------------------------------------------------ owner callbacks (cards)

    def card_geometry_changed(self, card: TableCard) -> None:
        if self._building:
            return
        for line in self._edges_of.get(card.key, ()):
            line.refresh()
            line.update()

    def card_moved(self, card: TableCard) -> None:
        moved = {c.key: (c.x(), c.y()) for c in self.selectedItems() if isinstance(c, TableCard)}
        moved[card.key] = (card.x(), card.y())
        self.cardsMoved.emit(moved)

    def card_clicked(self, card: TableCard, column: Column | None) -> None:
        self.columnClicked.emit(card.table, column)

    def card_opened(self, card: TableCard) -> None:
        self.tableOpenRequested.emit(card.table)

    def card_menu(self, card: TableCard, position: QPoint) -> None:
        self.menuRequested.emit(card.table, position)

    # ------------------------------------------------------------------ highlight and filter

    @property
    def focus_key(self) -> TableKey | None:
        return self._focus

    def _on_selection(self) -> None:
        selected = [i for i in self.selectedItems() if isinstance(i, TableCard)]
        key = selected[0].key if len(selected) == 1 else None
        self.highlight(key)
        self.focusChanged.emit(key)

    def highlight(self, key: TableKey | None) -> None:
        """Dim everything except ``key``, its neighbours and the lines between them."""
        self._focus = key
        model = self._model
        if key is None or model is None:
            for card in self._cards.values():
                card.dimmed = False
                card.update()
            for line in self._edges:
                line.dimmed = False
                line.update()
            return
        near = model.neighbors(key) | {key}
        for card_key, card in self._cards.items():
            card.dimmed = card_key not in near
            card.update()
        for line in self._edges:
            line.dimmed = key not in (line.relation.child, line.relation.parent)
            line.update()

    def set_filter(self, text: str) -> list[TableKey]:
        """Show only tables whose name or a column name contains ``text``; return those keys."""
        self._filter = text.strip()
        needle = self._filter.casefold()
        matches: list[TableKey] = []
        visible: set[TableKey] = set()
        for key, card in self._cards.items():
            table: Table = card.table
            hit = (
                not needle
                or needle in table.name.casefold()
                or any(needle in c.name.casefold() for c in table.columns)
            )
            card.setVisible(hit)
            if hit:
                visible.add(key)
                matches.append(key)
        for line in self._edges:
            line.setVisible(line.relation.child in visible and line.relation.parent in visible)
        return matches

    def visible_bounds(self) -> QRectF:
        """Bounding box of the visible cards (the whole diagram when none is hidden)."""
        rect = QRectF()
        for card in self._cards.values():
            if card.isVisible():
                rect = rect.united(card.sceneBoundingRect())
        return rect
