"""Graphics items of the diagram: table cards and relation lines with crow's-foot ends."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneContextMenuEvent,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from ...core.erd import Cardinality, Rect, Relation, route, route_through
from ...core.schema import Column, Table, TableKey, TableKind
from ..i18n import tr
from .colors import ErdColors

if TYPE_CHECKING:
    from PySide6.QtCore import QPoint

HEADER_H = 30.0
ROW_H = 22.0
PAD = 10.0
ICON_W = 22.0
MAX_ROWS = 14  # collapsed cards show at most this many columns
MIN_WIDTH = 170.0
MAX_NAME = 230.0
MAX_TYPE = 150.0
RADIUS = 7.0

#: Below these zoom levels details are skipped so very large diagrams stay fast.
LOD_ROWS = 0.35
LOD_TITLE = 0.14
LOD_EDGES = 0.05


class CardOwner(Protocol):
    """What the cards report to (the scene)."""

    def card_geometry_changed(self, card: TableCard) -> None: ...
    def card_moved(self, card: TableCard) -> None: ...
    def card_clicked(self, card: TableCard, column: Column | None) -> None: ...
    def card_opened(self, card: TableCard) -> None: ...
    def card_menu(self, card: TableCard, position: QPoint) -> None: ...


def visible_columns(table: Table, expanded: bool) -> tuple[list[Column], int]:
    """Columns to draw and how many are hidden; keys are never hidden."""
    columns = table.columns
    if expanded or len(columns) <= MAX_ROWS:
        return list(columns), 0
    keys = table.foreign_key_columns | set(table.primary_key)
    chosen = {c.name for c in columns if c.name in keys}
    for column in columns:
        if len(chosen) >= MAX_ROWS:
            break
        chosen.add(column.name)
    shown = [c for c in columns if c.name in chosen]
    return shown, len(columns) - len(shown)


class TableCard(QGraphicsItem):
    """One table: a title bar and a row per column (key icon, name, type)."""

    def __init__(
        self,
        table: Table,
        colors: ErdColors,
        owner: CardOwner,
        *,
        show_schema: bool = False,
        junction: bool = False,
    ) -> None:
        super().__init__()
        self.table = table
        self.junction = junction
        self._colors = colors
        self._owner = owner
        self._show_schema = show_schema
        self._expanded = False
        self._hover_row = -1
        self._press = QPointF()
        self._moved = False
        self.dimmed = False
        #: Where the automatic layout put the card; edge channels are valid only while it is there.
        self.layout_pos = QPointF()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self._rows: list[Column] = []
        self._hidden = 0
        self._width = MIN_WIDTH
        self._names: list[str] = []
        self._types: list[str] = []
        self._has_comment = any(c.comment for c in table.columns)
        self._font = QFont()
        self._font.setPixelSize(12)
        self._bold = QFont(self._font)
        self._bold.setBold(True)
        self._rebuild()

    # ------------------------------------------------------------------ geometry

    @property
    def key(self) -> TableKey:
        return self.table.key

    @property
    def expandable(self) -> bool:
        return len(self.table.columns) > MAX_ROWS

    def _row_count(self) -> int:
        return len(self._rows) + (1 if self.expandable else 0)

    def _rebuild(self) -> None:
        self.prepareGeometryChange()
        self._rows, self._hidden = visible_columns(self.table, self._expanded)
        fm = QFontMetricsF(self._font)
        bold = QFontMetricsF(self._bold)
        title = self._title()
        name_w = max((fm.horizontalAdvance(c.name) for c in self._rows), default=0.0)
        type_w = max((fm.horizontalAdvance(c.type) for c in self._rows), default=0.0)
        name_w = min(name_w, MAX_NAME)
        type_w = min(type_w, MAX_TYPE)
        comment_w = 18.0 if self._has_comment else 0.0
        body = PAD + ICON_W + name_w + comment_w + 16 + type_w + PAD
        head = PAD + bold.horizontalAdvance(title) + PAD + (46 if self._badge() else 0)
        self._width = max(MIN_WIDTH, body, head)
        room_name = self._width - PAD - ICON_W - comment_w - 16 - type_w - PAD
        self._names = [
            fm.elidedText(c.name, Qt.TextElideMode.ElideRight, room_name) for c in self._rows
        ]
        self._types = [
            fm.elidedText(c.type, Qt.TextElideMode.ElideRight, MAX_TYPE) for c in self._rows
        ]
        self.update()

    def _badge(self) -> str:
        """Small label on the right of the title bar: view kind, or ``N:M`` for a junction table."""
        if self.table.is_view:
            return tr("matview") if self.table.kind is TableKind.MATERIALIZED_VIEW else tr("view")
        return "N:M" if self.junction else ""

    def _title(self) -> str:
        table = self.table
        return f"{table.schema}.{table.name}" if self._show_schema else table.name

    def size(self) -> tuple[float, float]:
        return self._width, HEADER_H + self._row_count() * ROW_H + (6 if self._row_count() else 0)

    def boundingRect(self) -> QRectF:
        width, height = self.size()
        return QRectF(-1.0, -1.0, width + 2.0, height + 2.0)

    def shape(self) -> QPainterPath:
        width, height = self.size()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, width, height), RADIUS, RADIUS)
        return path

    def scene_rect(self) -> Rect:
        width, height = self.size()
        return Rect(self.x(), self.y(), width, height)

    def anchor_y(self, columns: tuple[str, ...]) -> float:
        """Local height where an edge for ``columns`` attaches (that column's row)."""
        for index, column in enumerate(self._rows):
            if columns and column.name == columns[0]:
                return HEADER_H + index * ROW_H + ROW_H / 2
        if self.expandable and self._rows:
            return HEADER_H + len(self._rows) * ROW_H + ROW_H / 2  # the "more" row
        return HEADER_H / 2

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self._rebuild()
        self._owner.card_geometry_changed(self)

    def set_colors(self, colors: ErdColors) -> None:
        self._colors = colors
        self.update()

    def row_at(self, y: float) -> int:
        """Row index under local height ``y`` (``-1`` on the title bar or outside)."""
        if y < HEADER_H:
            return -1
        index = int((y - HEADER_H) // ROW_H)
        return index if 0 <= index < self._row_count() else -1

    # ------------------------------------------------------------------ painting

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        colors = self._colors
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        width, height = self.size()
        painter.setOpacity(0.28 if self.dimmed else 1.0)
        header = colors.header_view if self.table.is_view else colors.header
        selected = bool(self.isSelected())

        if lod < LOD_TITLE:
            painter.fillRect(QRectF(0, 0, width, height), colors.card)
            painter.fillRect(QRectF(0, 0, width, HEADER_H), header)
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPen(colors.edge_active if selected else colors.border, 2.0 if selected else 1.0)
        card = QPainterPath()
        card.addRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), RADIUS, RADIUS)
        painter.fillPath(card, colors.card)
        painter.save()
        painter.setClipPath(card)
        painter.fillRect(QRectF(0, 0, width, HEADER_H), header)
        painter.restore()
        painter.setPen(outline)
        painter.drawPath(card)

        painter.setPen(colors.text)
        painter.setFont(self._bold)
        badge = self._badge()
        title_box = QRectF(PAD, 0, width - 2 * PAD - (46 if badge else 0), HEADER_H)
        painter.drawText(
            title_box,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            QFontMetricsF(self._bold).elidedText(
                self._title(), Qt.TextElideMode.ElideRight, title_box.width()
            ),
        )
        if badge:
            painter.setFont(self._font)
            painter.setPen(colors.muted)
            label = badge
            painter.drawText(
                QRectF(width - 56 - PAD + 10, 0, 46, HEADER_H),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                label,
            )
        if lod < LOD_ROWS:
            return

        painter.setFont(self._font)
        fks = self.table.foreign_key_columns
        for index, column in enumerate(self._rows):
            top = HEADER_H + index * ROW_H
            if index == self._hover_row:
                painter.fillRect(QRectF(1, top, width - 2, ROW_H), colors.row_hover)
            centre = top + ROW_H / 2
            if column.primary_key:
                _key(painter, PAD + 3, centre, colors.pk)
            elif column.name in fks:
                _key(painter, PAD + 3, centre, colors.fk)
            painter.setPen(colors.text)
            name_x = PAD + ICON_W
            painter.drawText(
                QRectF(name_x, top, width - name_x - PAD, ROW_H),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self._names[index],
            )
            painter.setPen(colors.muted)
            painter.drawText(
                QRectF(PAD, top, width - 2 * PAD, ROW_H),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                self._types[index],
            )
            if column.comment:
                _help(
                    painter,
                    width - PAD - 4 - _type_width(self._font, self._types[index]),
                    centre,
                    colors,
                )
        if self.expandable:
            top = HEADER_H + len(self._rows) * ROW_H
            painter.setPen(colors.edge_active)
            text = (
                tr("show fewer columns")
                if self._expanded
                else tr("+ {n} more columns", n=self._hidden)
            )
            painter.drawText(
                QRectF(PAD + ICON_W, top, width - 2 * PAD - ICON_W, ROW_H),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                text,
            )

    # ------------------------------------------------------------------ interaction

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._owner.card_geometry_changed(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._press = event.scenePos()
        self._moved = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseMoveEvent(event)
        if (event.scenePos() - self._press).manhattanLength() > 3:
            self._moved = True

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._moved:
            self._owner.card_moved(self)
            return
        row = self.row_at(event.pos().y())
        if row < 0:
            self._owner.card_clicked(self, None)
        elif row < len(self._rows):
            self._owner.card_clicked(self, self._rows[row])
        else:
            self.toggle_expanded()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._owner.card_opened(self)

    def contextMenuEvent(self, event: QGraphicsSceneContextMenuEvent) -> None:
        self.setSelected(True)
        self._owner.card_menu(self, event.screenPos())

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        row = self.row_at(event.pos().y())
        if row != self._hover_row:
            self._hover_row = row
            self.setToolTip(self._tooltip(row))
            self.update()

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self._hover_row = -1
        self.update()

    def _tooltip(self, row: int) -> str:
        table = self.table
        if row < 0:
            lines = [f"{table.schema}.{table.name}", f"{len(table.columns)} " + tr("columns")]
            if table.row_estimate is not None:
                lines.append(tr("about {n} rows", n=f"{table.row_estimate:,}"))
            if self.junction:
                lines.append(tr("junction table: links the tables it references (many-to-many)"))
            if table.comment:
                lines.append(table.comment)
            return "\n".join(lines)
        if row >= len(self._rows):
            return ""
        column = self._rows[row]
        lines = [f"{column.name}  {column.type}"]
        flags = []
        if column.primary_key:
            flags.append(tr("primary key"))
        if not column.nullable:
            flags.append(tr("NOT NULL"))
        if flags:
            lines.append(" · ".join(flags))
        if column.default is not None:
            lines.append(tr("default {value}", value=column.default))
        for fk in table.foreign_keys:
            if column.name in fk.columns:
                position = fk.columns.index(column.name)
                target = fk.ref_columns[position] if position < len(fk.ref_columns) else "?"
                lines.append(f"→ {fk.ref_table}.{target}")
        if column.comment:
            lines.append("")
            lines.append(column.comment)
        return "\n".join(lines)


def _type_width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text) + 14


def _key(painter: QPainter, x: float, y: float, color: QColor) -> None:
    """A small key: a ring and a toothed shaft."""
    painter.save()
    painter.setPen(QPen(color, 1.5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(x, y), 3.2, 3.2)
    painter.drawLine(QPointF(x + 3.2, y), QPointF(x + 10.5, y))
    painter.drawLine(QPointF(x + 7.5, y), QPointF(x + 7.5, y + 3.2))
    painter.drawLine(QPointF(x + 10.5, y), QPointF(x + 10.5, y + 3.2))
    painter.restore()


def _help(painter: QPainter, x: float, y: float, colors: ErdColors) -> None:
    """The "?" badge of a column that has a comment (the comment is in its tooltip)."""
    painter.save()
    painter.setPen(QPen(colors.muted, 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(x - 5, y), 6.0, 6.0)
    font = QFont()
    font.setPixelSize(9)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(x - 11, y - 6, 12, 12), int(Qt.AlignmentFlag.AlignCenter), "?")
    painter.restore()


class RelationLine(QGraphicsItem):
    """A foreign key: an orthogonal line with a crow's foot at the child and a bar at the parent."""

    def __init__(
        self,
        relation: Relation,
        child: TableCard,
        parent: TableCard,
        colors: ErdColors,
        lane: int,
        channel: tuple[tuple[float, float], ...] = (),
    ) -> None:
        super().__init__()
        self.relation = relation
        self._channel = channel
        self.child = child
        self.parent_card = parent
        self._colors = colors
        self._lane = lane
        self._points: list[QPointF] = []
        self._start_side = "right"
        self._end_side = "left"
        self._bounds = QRectF()
        self.active = False
        self.dimmed = False
        self.setZValue(-1)
        self.setAcceptHoverEvents(True)
        self.setToolTip(self._describe())
        self.refresh()

    def _describe(self) -> str:
        r = self.relation
        child_cols = ", ".join(r.columns)
        parent_cols = ", ".join(r.ref_columns)
        kind = "1:1" if r.cardinality is Cardinality.ONE_TO_ONE else "1:N"
        optional = tr("optional") if r.optional else tr("required")
        return f"{r.child.name}({child_cols}) → {r.parent.name}({parent_cols})\n{kind} · {optional}"

    def set_colors(self, colors: ErdColors) -> None:
        self._colors = colors
        self.update()

    def refresh(self) -> None:
        """Recompute the route after either card moved or changed height."""
        child_rect = self.child.scene_rect()
        parent_rect = self.parent_card.scene_rect()
        child_y = child_rect.y + self.child.anchor_y(self.relation.columns)
        parent_y = parent_rect.y + self.parent_card.anchor_y(self.relation.ref_columns)
        found = None
        if self._channel and (
            self.child.pos() == self.child.layout_pos
            and self.parent_card.pos() == self.parent_card.layout_pos
        ):
            found = route_through(
                child_rect, parent_rect, child_y, parent_y, self._channel, self._lane
            )
        if found is None:
            found = route(child_rect, parent_rect, child_y, parent_y, self._lane)
        self.prepareGeometryChange()
        self._points = [QPointF(x, y) for x, y in found.points]
        self._start_side, self._end_side = found.start_side, found.end_side
        xs = [p.x() for p in self._points]
        ys = [p.y() for p in self._points]
        self._bounds = QRectF(
            min(xs) - 30, min(ys) - 14, max(xs) - min(xs) + 60, max(ys) - min(ys) + 28
        )

    def boundingRect(self) -> QRectF:
        return self._bounds

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if self._points:
            path.moveTo(self._points[0])
            for point in self._points[1:]:
                path.lineTo(point)
        stroker = QPainterPathStroker()
        stroker.setWidth(9.0)
        return stroker.createStroke(path)

    def hoverEnterEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.active = True
        self.update()

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        self.active = False
        self.update()

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        if len(self._points) < 2:
            return
        colors = self._colors
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        highlighted = self.active
        if lod < LOD_EDGES and not highlighted:
            return  # sub-pixel lines: skipping them roughly halves a repaint of a huge diagram
        painter.setOpacity(0.15 if self.dimmed and not highlighted else 1.0)
        pen = QPen(colors.edge_active if highlighted else colors.edge, 2.0 if highlighted else 1.3)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if lod >= LOD_TITLE:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath(self._points[0])
        for point in self._points[1:]:
            path.lineTo(point)
        painter.drawPath(path)
        if lod < 0.25:
            return
        relation = self.relation
        many = relation.cardinality is Cardinality.ONE_TO_MANY
        outward_child = 1.0 if self._start_side == "right" else -1.0
        outward_parent = 1.0 if self._end_side == "right" else -1.0
        if many:
            _crows_foot(painter, self._points[0], outward_child)
        else:
            _one(painter, self._points[0], outward_child, optional=False)
        _one(painter, self._points[-1], outward_parent, optional=relation.optional)


def _crows_foot(painter: QPainter, tip: QPointF, direction: float) -> None:
    """Three prongs meeting at the line and spreading out towards the card."""
    base = QPointF(tip.x() + direction * 12, tip.y())
    painter.drawLine(base, QPointF(tip.x(), tip.y() - 6))
    painter.drawLine(base, tip)
    painter.drawLine(base, QPointF(tip.x(), tip.y() + 6))


def _one(painter: QPainter, tip: QPointF, direction: float, *, optional: bool) -> None:
    """``||`` (exactly one) or ``o|`` (zero or one) at the card edge."""
    x = tip.x() + direction * 6
    painter.drawLine(QPointF(x, tip.y() - 6), QPointF(x, tip.y() + 6))
    if optional:
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(tip.x() + direction * 13, tip.y()), 3.5, 3.5)
        painter.restore()
    else:
        x2 = tip.x() + direction * 10
        painter.drawLine(QPointF(x2, tip.y() - 6), QPointF(x2, tip.y() + 6))
