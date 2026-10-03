from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from easydbms.core.dialects import DialectId
from easydbms.core.erd import build_erd
from easydbms.core.schema import Column, DatabaseSchema, Table, TableKey
from easydbms.ui.erd import ErdScene, ErdView
from easydbms.ui.erd.items import (
    HEADER_H,
    MAX_ROWS,
    ROW_H,
    RelationLine,
    TableCard,
    visible_columns,
)
from easydbms.ui.theme import current_tokens
from tests.core.erd.test_model import col, fk, schema_of, shop, table


def build(
    qtbot: QtBot,
    schema: DatabaseSchema | None = None,
    saved: Mapping[TableKey, tuple[float, float]] | None = None,
) -> tuple[ErdScene, ErdView]:
    scene = ErdScene(current_tokens())
    view = ErdView(scene)
    qtbot.addWidget(view)
    view.resize(1000, 700)
    view.show()
    scene.build(build_erd(schema or shop()), saved)
    view.fit_all()
    return scene, view


def key(name: str, schema: str = "main") -> TableKey:
    return TableKey(schema, name)


def click_at(view: ErdView, scene_pos: QPointF, *, double: bool = False) -> None:
    pos = view.mapFromScene(scene_pos)
    if double:
        QTest.mouseDClick(
            view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos
        )
    else:
        QTest.mouseClick(
            view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos
        )


def row_point(card: TableCard, row: int) -> QPointF:
    return card.mapToScene(QPointF(card.size()[0] / 2, HEADER_H + ROW_H * row + ROW_H / 2))


# ---------------------------------------------------------------------------- building


def test_one_card_per_table_and_one_line_per_foreign_key(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    assert len(scene.cards()) == 7
    assert len(scene.edges()) == 6
    assert scene.card(key("book_tag")) is not None
    assert scene.card(key("nope")) is None


def test_cards_do_not_overlap_and_parents_are_left_of_children(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    cards = scene.cards()
    for i, a in enumerate(cards):
        for b in cards[i + 1 :]:
            assert (
                not a.sceneBoundingRect()
                .adjusted(2, 2, -2, -2)
                .intersects(b.sceneBoundingRect().adjusted(2, 2, -2, -2))
            ), (a.table.name, b.table.name)
    author, book = scene.card(key("author")), scene.card(key("book"))
    assert author is not None
    assert book is not None
    assert author.x() < book.x()


def test_saved_positions_win_over_the_layout(qtbot: QtBot) -> None:
    scene, _ = build(qtbot, saved={key("tag"): (1234.0, -321.0)})
    tag = scene.card(key("tag"))
    assert tag is not None
    assert (tag.x(), tag.y()) == (1234.0, -321.0)
    assert tag.layout_pos != tag.pos()  # the automatic position is remembered separately


def test_rebuild_replaces_everything(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    scene.build(build_erd(schema_of(table("only", [col("id", pk=True)], ("id",)))))
    assert [c.table.name for c in scene.cards()] == ["only"]
    assert scene.edges() == []


def test_views_look_different_and_are_listed(qtbot: QtBot) -> None:
    schema = schema_of(
        table("t", [col("id", pk=True)], ("id",)),
        table(
            "v",
            [col("id")],
            kind=__import__("easydbms.core.schema", fromlist=["TableKind"]).TableKind.VIEW,
        ),
    )
    scene, _ = build(qtbot, schema)
    card = scene.card(key("v"))
    assert card is not None
    assert card.table.is_view


def test_long_edges_use_channels_while_cards_stay_put(qtbot: QtBot) -> None:
    # a(0) <- b(1) <- c(2) <- d(3), and d also references a: that edge skips two layers
    tables = [
        table("a", [col("id", pk=True)], ("id",)),
        table("b", [col("id", pk=True), col("a_id")], ("id",), [fk(("a_id",), "a")]),
        table("c", [col("id", pk=True), col("b_id")], ("id",), [fk(("b_id",), "b")]),
        table(
            "d",
            [col("id", pk=True), col("c_id"), col("a_id")],
            ("id",),
            [fk(("c_id",), "c"), fk(("a_id",), "a")],
        ),
    ]
    scene, _ = build(qtbot, schema_of(*tables))
    long_edge = next(
        e for e in scene.edges() if e.relation.child.name == "d" and e.relation.parent.name == "a"
    )
    points = long_edge._points
    assert len(points) > 4  # start, three-ish bends through the middle layers, end
    for a, b in pairwise(points):
        assert a.x() == b.x() or a.y() == b.y()
    # the path avoids the cards of the layers it crosses
    for name in ("b", "c"):
        card = scene.card(key(name))
        assert card is not None
        rect = card.sceneBoundingRect().adjusted(1, 1, -1, -1)
        for a, b in pairwise(points):
            horizontal = a.y() == b.y()
            if horizontal:
                lo, hi = sorted((a.x(), b.x()))
                crosses = (
                    rect.top() < a.y() < rect.bottom() and lo < rect.right() and hi > rect.left()
                )
                assert not crosses, name
    # dragging a card away makes the edge fall back to a simple (still orthogonal) route
    d = scene.card(key("d"))
    assert d is not None
    d.setPos(d.x() + 40, d.y() + 400)
    for a, b in pairwise(long_edge._points):
        assert a.x() == b.x() or a.y() == b.y()


# ---------------------------------------------------------------------------- interaction


def test_dragging_a_card_updates_its_lines_and_reports_the_new_position(qtbot: QtBot) -> None:
    scene, view = build(qtbot)
    book = scene.card(key("book"))
    assert book is not None
    line = next(e for e in scene.edges() if e.relation.child.name == "book")
    before = list(line._points)
    start = book.mapToScene(QPointF(book.size()[0] / 2, HEADER_H / 2))
    with qtbot.waitSignal(scene.cardsMoved, timeout=2000) as moved:
        QTest.mousePress(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            view.mapFromScene(start),
        )
        QTest.mouseMove(view.viewport(), view.mapFromScene(start + QPointF(60, 90)))
        QTest.mouseRelease(
            view.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            view.mapFromScene(start + QPointF(60, 90)),
        )
    (positions,) = moved.args
    assert set(positions) == {key("book")}
    assert positions[key("book")] == (book.x(), book.y())
    assert line._points != before


def test_clicking_a_column_reports_it_and_the_title_reports_none(qtbot: QtBot) -> None:
    scene, view = build(qtbot)
    author = scene.card(key("author"))
    assert author is not None
    view.centerOn(author)
    with qtbot.waitSignal(scene.columnClicked, timeout=2000) as clicked:
        click_at(view, row_point(author, 1))
    table, column = clicked.args
    assert table is author.table
    assert isinstance(column, Column)
    assert column.name == "name"
    with qtbot.waitSignal(scene.columnClicked, timeout=2000) as header:
        click_at(view, author.mapToScene(QPointF(20, HEADER_H / 2)))
    assert header.args[1] is None


def test_double_click_opens_the_table(qtbot: QtBot) -> None:
    scene, view = build(qtbot)
    tag = scene.card(key("tag"))
    assert tag is not None
    view.centerOn(tag)
    with qtbot.waitSignal(scene.tableOpenRequested, timeout=2000) as opened:
        click_at(view, tag.mapToScene(QPointF(20, HEADER_H / 2)), double=True)
    assert opened.args[0] is tag.table


def test_selecting_a_card_dims_the_unrelated_ones(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    book = scene.card(key("book"))
    assert book is not None
    book.setSelected(True)
    assert scene.focus_key == key("book")
    dimmed = {c.table.name for c in scene.cards() if c.dimmed}
    assert dimmed == {"tag", "lonely", "profile", "node"}
    active = {
        (e.relation.child.name, e.relation.parent.name) for e in scene.edges() if not e.dimmed
    }
    assert active == {("book", "author"), ("book_tag", "book")}
    scene.clearSelection()
    assert scene.focus_key is None
    assert not any(c.dimmed for c in scene.cards())
    assert not any(e.dimmed for e in scene.edges())


def test_filter_hides_non_matching_tables_and_their_lines(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    matches = scene.set_filter("auth")  # tables author, and columns author_id in book/profile
    assert {k.name for k in matches} == {"author", "book", "profile"}
    assert scene.card(key("tag")).isVisible() is False  # type: ignore[union-attr]
    visible_edges = [e for e in scene.edges() if e.isVisible()]
    assert {(e.relation.child.name, e.relation.parent.name) for e in visible_edges} == {
        ("book", "author"),
        ("profile", "author"),
    }
    assert scene.visible_bounds().width() > 0
    assert len(scene.set_filter("")) == 7
    assert all(e.isVisible() for e in scene.edges())


def test_filter_is_kept_when_the_scene_is_rebuilt(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    scene.set_filter("tag")
    scene.build(build_erd(shop()))
    assert not scene.card(key("author")).isVisible()  # type: ignore[union-attr]


def test_long_tables_are_collapsed_and_expand_on_click(qtbot: QtBot) -> None:
    wide = Table(
        "main",
        "wide",
        columns=tuple(Column(f"c{i}", "int") for i in range(40)),
        primary_key=("c0",),
    )
    scene, view = build(qtbot, DatabaseSchema(DialectId.SQLITE, ("main",), "main", (wide,)))
    card = scene.card(key("wide"))
    assert card is not None
    collapsed_height = card.size()[1]
    shown, hidden = visible_columns(wide, expanded=False)
    assert len(shown) == MAX_ROWS
    assert hidden == 40 - MAX_ROWS
    view.centerOn(card)
    click_at(view, row_point(card, MAX_ROWS))  # the "+N more columns" row
    assert card.size()[1] > collapsed_height
    assert len(visible_columns(wide, expanded=True)[0]) == 40
    click_at(view, row_point(card, 40))
    assert card.size()[1] == collapsed_height


def test_keys_stay_visible_in_a_collapsed_card() -> None:
    columns = tuple(Column(f"c{i}", "int") for i in range(40))
    from easydbms.core.schema import ForeignKey

    table_ = Table(
        "main",
        "t",
        columns=columns,
        primary_key=("c0",),
        foreign_keys=(ForeignKey(None, ("c39",), "main", "o", ("id",)),),
    )
    shown, _ = visible_columns(table_, expanded=False)
    names = [c.name for c in shown]
    assert "c0" in names
    assert "c39" in names
    assert names == sorted(names, key=lambda n: int(n[1:]))  # original order


def test_tooltips_describe_columns_and_relations(qtbot: QtBot) -> None:
    scene, _ = build(qtbot)
    book = scene.card(key("book"))
    assert book is not None
    tip = book._tooltip(1)  # author_id
    assert "author_id" in tip
    assert "NOT NULL" in tip
    assert "→ author.id" in tip
    assert "book" in book._tooltip(-1)
    edge = scene.edges()[0]
    assert "→" in edge.toolTip()
    assert "1:" in edge.toolTip()


# ---------------------------------------------------------------------------- view


def test_zoom_buttons_and_limits(qtbot: QtBot) -> None:
    _, view = build(qtbot)
    view.set_zoom(1.0)
    view.zoom_in()
    assert view.zoom > 1.0
    for _ in range(40):
        view.zoom_in()
    assert view.zoom <= 2.5 + 1e-9
    for _ in range(80):
        view.zoom_out()
    assert view.zoom >= 0.01 - 1e-9


def test_fit_all_never_enlarges_past_actual_size(qtbot: QtBot) -> None:
    _, view = build(qtbot, schema_of(table("only", [col("id", pk=True)], ("id",))))
    view.fit_all()
    assert view.zoom == pytest.approx(1.0)
    _, view2 = build(qtbot)
    view2.resize(300, 200)
    view2.fit_all()
    assert view2.zoom < 1.0


def test_the_wheel_zooms(qtbot: QtBot) -> None:
    _, view = build(qtbot)
    view.set_zoom(0.5)
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent

    event = QWheelEvent(
        QPointF(100, 100),
        QPointF(100, 100),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(event)
    assert view.zoom > 0.5


def test_painting_at_every_level_of_detail_does_not_fail(qtbot: QtBot) -> None:
    scene, view = build(qtbot)
    for zoom in (2.0, 1.0, 0.3, 0.1, 0.05):
        view.set_zoom(zoom)
        image = QImage(400, 300, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        scene.render(painter)
        painter.end()
    assert not view.grab().isNull()


def test_the_help_button_opens_a_popup(qtbot: QtBot) -> None:
    from easydbms.ui.erd.view import HelpPopup

    _, view = build(qtbot)
    view.help_button.click()
    popups = view.findChildren(HelpPopup)
    assert popups
    assert (
        "Mouse wheel"
        in popups[0]
        .findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)[0]
        .text()
    )


def test_colours_follow_the_theme(qtbot: QtBot) -> None:
    from easydbms.ui.theme import THEMES

    scene, _ = build(qtbot)
    dark_card = scene.cards()[0]._colors.card.name()
    scene.set_tokens(THEMES["light"])
    assert scene.cards()[0]._colors.card.name() != dark_card
    assert isinstance(scene.edges()[0], RelationLine)
    scene.set_tokens(THEMES["dark"])


def test_many_schemas_are_grouped_when_the_diagram_is_big(qtbot: QtBot) -> None:
    from easydbms.ui.erd.scene import GROUP_FROM

    tables = []
    for name in ("a", "b"):
        for i in range(GROUP_FROM // 2 + 2):
            tables.append(table(f"t{i}", [col("id", pk=True)], ("id",), schema=name))
    schema = DatabaseSchema(DialectId.POSTGRESQL, ("a", "b"), "a", tuple(tables))
    scene, _ = build(qtbot, schema)
    assert len(scene._frames) == 2
    titles = {c.table.schema for c in scene.cards()}
    assert titles == {"a", "b"}
