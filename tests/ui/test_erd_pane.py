from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from easydbms.core.dialects import DialectId
from easydbms.core.schema import Column, DatabaseSchema, Table, TableKey
from easydbms.core.session import SchemaState, Session
from easydbms.ui.erd import ErdPane

from .conftest import Env, add_shop


def open_session(env: Env, name: str = "Shop", rows: int = 0) -> Session:
    config = add_shop(env, name, rows)
    return env.services.manager.activate(config.id).result(timeout=10)


def make_pane(env: Env, qtbot: QtBot, session: Session) -> ErdPane:
    pane = ErdPane(session, env.services.erd_store, env.services.db)
    qtbot.addWidget(pane)
    pane.resize(700, 600)
    pane.show()
    return pane


def ready_pane(env: Env, qtbot: QtBot, name: str = "Shop") -> ErdPane:
    session = open_session(env, name)
    qtbot.waitUntil(lambda: session.schema_state is SchemaState.READY, timeout=10000)
    return make_pane(env, qtbot, session)


def key(name: str) -> TableKey:
    return TableKey("main", name)


# ---------------------------------------------------------------------------- states


def test_ready_pane_shows_the_diagram_and_a_summary(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    assert pane.stack.currentWidget() is pane.view
    assert len(pane.scene.cards()) == 6  # 5 tables and a view
    assert len(pane.scene.edges()) == 3
    assert "6 tables · 3 relations" in pane.summary.text()
    assert "read in" in pane.summary.text()
    assert not pane.scope_combo.isVisible()  # a single schema needs no selector
    assert pane.refresh_button.isEnabled()


def test_before_the_schema_is_read_the_pane_says_so(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env)
    session = Session(env.services.manager.session(config.id).config)
    session.connect()
    pane = make_pane(env, qtbot, session)
    assert pane.stack.currentWidget() is not pane.view
    assert "has not been read yet" in pane._message_title.text()
    assert not pane.retry_button.isVisible()
    session.disconnect()


def test_loading_state_without_a_schema(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env)
    session = Session(env.services.manager.session(config.id).config)
    session.connect()
    pane = make_pane(env, qtbot, session)
    session._schema_state = SchemaState.LOADING
    pane.update_from_session()
    assert "Reading the database structure" in pane._message_title.text()
    assert not pane.refresh_button.isEnabled()
    session.disconnect()


def test_an_empty_database_gets_a_friendly_message(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Empty")
    session = env.services.manager.activate(config.id).result(timeout=10)
    qtbot.waitUntil(lambda: session.schema_state is SchemaState.READY, timeout=10000)
    pane = make_pane(env, qtbot, session)
    assert pane.stack.currentWidget() is not pane.view
    assert "no tables" in pane._message_title.text()


def test_an_introspection_error_is_shown_and_can_be_retried(
    env: Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = open_session(env)
    qtbot.waitUntil(lambda: session.schema_state is SchemaState.READY, timeout=10000)
    pane = make_pane(env, qtbot, session)
    from easydbms.core.session import session as session_module

    def broken(client: Any) -> Any:
        raise RuntimeError("catalog is locked")

    monkeypatch.setattr(session_module, "introspect", broken)
    session.load_schema().result(timeout=10)
    pane.update_from_session()
    assert "Could not read" in pane._message_title.text()
    assert "catalog is locked" in pane._message_body.text()
    assert pane.retry_button.isVisible()

    monkeypatch.undo()
    pane.retry_button.click()
    qtbot.waitUntil(lambda: session.schema_state is SchemaState.READY, timeout=10000)
    pane.update_from_session()
    assert pane.stack.currentWidget() is pane.view


def test_a_reload_picks_up_new_tables(env: Env, qtbot: QtBot) -> None:
    session = open_session(env)
    qtbot.waitUntil(lambda: session.schema_state is SchemaState.READY, timeout=10000)
    pane = make_pane(env, qtbot, session)
    assert session.client is not None
    session.client.execute(
        "CREATE TABLE extra (id INTEGER PRIMARY KEY, shop_id INTEGER REFERENCES author(id))"
    )
    version = session.schema_version
    pane.refresh_button.click()
    qtbot.waitUntil(lambda: session.schema_version > version, timeout=10000)
    pane.update_from_session()
    assert pane.scene.card(key("extra")) is not None
    assert len(pane.scene.edges()) == 4


def test_a_new_session_object_for_the_same_connection_is_followed(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    old = pane._session
    env.services.manager.forget(old.id)
    env.services.store.save(old.config)
    new = env.services.manager.activate(old.id).result(timeout=10)
    qtbot.waitUntil(lambda: new.schema_state is SchemaState.READY, timeout=10000)
    assert new is not old
    pane.set_session(new)
    assert pane._session is new
    assert pane.stack.currentWidget() is pane.view


# ---------------------------------------------------------------------------- search, layout


def test_search_filters_after_a_short_pause_and_enter_selects_the_first_match(
    env: Env, qtbot: QtBot
) -> None:
    pane = ready_pane(env, qtbot)
    pane.search.setText("tag")
    qtbot.waitUntil(lambda: not pane.scene.card(key("author")).isVisible(), timeout=2000)  # type: ignore[union-attr]
    assert pane.scene.card(key("tag")).isVisible()  # type: ignore[union-attr]
    QTest.keyClick(pane.search, Qt.Key.Key_Return)
    selected = [c for c in pane.scene.cards() if c.isSelected()]
    assert len(selected) == 1
    assert "tag" in selected[0].table.name
    pane.search.clear()
    qtbot.waitUntil(lambda: pane.scene.card(key("author")).isVisible(), timeout=2000)  # type: ignore[union-attr]


def test_moved_cards_are_remembered_and_restored(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    tag = pane.scene.card(key("tag"))
    assert tag is not None
    tag.setPos(777, 555)
    pane.scene.card_moved(tag)
    session_id = pane._session.id
    assert env.services.erd_store.load(session_id, "*")[key("tag")] == (777.0, 555.0)

    pane.refresh_button.click()
    version = pane._session.schema_version
    qtbot.waitUntil(lambda: pane._session.schema_version > version, timeout=10000)
    pane.update_from_session()
    again = pane.scene.card(key("tag"))
    assert again is not None
    assert (again.x(), again.y()) == (777.0, 555.0)


def test_reset_layout_forgets_moved_cards(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    tag = pane.scene.card(key("tag"))
    assert tag is not None
    auto = (tag.x(), tag.y())
    tag.setPos(900, 900)
    pane.scene.card_moved(tag)
    pane.reset_button.click()
    assert env.services.erd_store.load(pane._session.id, "*") == {}
    again = pane.scene.card(key("tag"))
    assert again is not None
    assert (again.x(), again.y()) == pytest.approx(auto)


def test_positions_of_dropped_tables_are_forgotten(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    session = pane._session
    env.services.erd_store.save(session.id, "*", {key("ghost"): (1.0, 2.0), key("tag"): (3.0, 4.0)})
    pane.reset_layout()  # clears; save again then rebuild through a reload
    env.services.erd_store.save(session.id, "*", {key("ghost"): (1.0, 2.0), key("tag"): (3.0, 4.0)})
    version = session.schema_version
    pane.reload()
    qtbot.waitUntil(lambda: session.schema_version > version, timeout=10000)
    pane.update_from_session()
    assert key("ghost") not in env.services.erd_store.load(session.id, "*")
    assert env.services.erd_store.load(session.id, "*")[key("tag")] == (3.0, 4.0)


def test_focus_table_selects_it_and_clears_a_search_that_hides_it(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    pane.search.setText("author")
    qtbot.waitUntil(lambda: not pane.scene.card(key("tag")).isVisible(), timeout=2000)  # type: ignore[union-attr]
    assert pane.focus_table(key("tag"))
    assert pane.search.text() == ""
    assert pane.scene.card(key("tag")).isSelected()  # type: ignore[union-attr]
    assert not pane.focus_table(key("nope"))


# ---------------------------------------------------------------------------- editor hooks


def test_clicking_a_column_asks_the_editor_for_its_name(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    book = pane.scene.card(key("book"))
    assert book is not None
    with qtbot.waitSignal(pane.insertRequested, timeout=2000) as sig:
        pane.scene.card_clicked(book, book.table.column("title"))
    assert sig.args == ["title"]
    odd = Column("order", "text")  # reserved word: quoted
    with qtbot.waitSignal(pane.insertRequested, timeout=2000) as quoted:
        pane.scene.card_clicked(book, odd)
    assert quoted.args == ['"order"']
    with qtbot.assertNotEmitted(pane.insertRequested):
        pane.scene.card_clicked(book, None)


def test_the_context_menu_offers_the_table_actions(env: Env, qtbot: QtBot) -> None:
    pane = ready_pane(env, qtbot)
    book = pane.scene.card(key("book"))
    assert book is not None
    menu = pane.build_menu(book.table)
    actions = {a.text(): a for a in menu.actions()}
    assert list(actions) == [
        "Open data",
        "SELECT * FROM this table",
        "Insert table name",
        "Copy table name",
    ]
    with qtbot.waitSignal(pane.tableOpenRequested, timeout=2000) as opened:
        actions["Open data"].trigger()
    assert opened.args[0] is book.table
    with qtbot.waitSignal(pane.selectStarRequested, timeout=2000):
        actions["SELECT * FROM this table"].trigger()
    with qtbot.waitSignal(pane.insertRequested, timeout=2000) as inserted:
        actions["Insert table name"].trigger()
    assert inserted.args == ["book"]
    actions["Copy table name"].trigger()
    from PySide6.QtGui import QGuiApplication

    assert QGuiApplication.clipboard().text() == "book"
    pane._show_menu("not a table", QPoint(0, 0))  # ignored, does not open anything


# ---------------------------------------------------------------------------- schemas


class FakeSession:
    """The little a pane needs from a session, with a multi-schema database."""

    id = "fake"

    def __init__(self, schema: DatabaseSchema) -> None:
        from easydbms.core.connections import ServerConnection

        self.schema = schema
        self.schema_state = SchemaState.READY
        self.schema_error = None
        self.schema_version = 1
        self.client = object()
        self.config = ServerConnection.model_validate(
            {"name": "pg", "dialect": "postgresql", "host": "h", "user": "u"}
        )

    def load_schema(self) -> None:
        pass


def two_schemas() -> DatabaseSchema:
    def t(schema: str, name: str) -> Table:
        return Table(
            schema, name, columns=(Column("id", "int", nullable=False),), primary_key=("id",)
        )

    return DatabaseSchema(
        DialectId.POSTGRESQL,
        ("public", "sales"),
        "public",
        (t("public", "users"), t("sales", "orders"), t("sales", "items")),
    )


def test_a_selector_appears_for_several_schemas_and_remembers_the_choice(
    env: Env, qtbot: QtBot
) -> None:
    fake: Any = FakeSession(two_schemas())
    pane = ErdPane(fake, env.services.erd_store, env.services.db)
    qtbot.addWidget(pane)
    pane.resize(600, 500)
    pane.show()
    assert pane.scope_combo.isVisible()
    assert [pane.scope_combo.itemText(i) for i in range(pane.scope_combo.count())] == [
        "All schemas",
        "public",
        "sales",
    ]
    assert pane.scope_combo.currentText() == "public"  # the default schema
    assert [c.table.name for c in pane.scene.cards()] == ["users"]

    pane.scope_combo.setCurrentIndex(2)
    pane._on_scope_chosen(2)
    assert sorted(c.table.name for c in pane.scene.cards()) == ["items", "orders"]
    assert env.services.db.get_state("erd/fake/scope") == "sales"

    pane.scope_combo.setCurrentIndex(0)
    pane._on_scope_chosen(0)
    assert len(pane.scene.cards()) == 3
    assert env.services.db.get_state("erd/fake/scope") == "*"

    again = ErdPane(fake, env.services.erd_store, env.services.db)
    qtbot.addWidget(again)
    assert again.scope_combo.currentText() == "All schemas"  # restored


def test_positions_are_kept_per_schema_scope(env: Env, qtbot: QtBot) -> None:
    fake: Any = FakeSession(two_schemas())
    pane = ErdPane(fake, env.services.erd_store, env.services.db)
    qtbot.addWidget(pane)
    pane.show()
    pane.scope_combo.setCurrentIndex(2)
    pane._on_scope_chosen(2)
    card = pane.scene.cards()[0]
    card.setPos(10, 20)
    pane.scene.card_moved(card)
    assert env.services.erd_store.load("fake", "sales")
    assert env.services.erd_store.load("fake", "public") == {}
    assert env.services.erd_store.load("fake", "*") == {}


def test_focusing_a_table_of_another_schema_switches_the_scope(env: Env, qtbot: QtBot) -> None:
    fake: Any = FakeSession(two_schemas())
    pane = ErdPane(fake, env.services.erd_store, env.services.db)
    qtbot.addWidget(pane)
    pane.show()
    assert pane.focus_table(TableKey("sales", "orders"))
    assert pane.scope_combo.currentText() == "sales"
    assert pane.scene.card(TableKey("sales", "orders")).isSelected()  # type: ignore[union-attr]
    assert pane.table_reference(Table("sales", "orders")) == "sales.orders"
    assert pane.table_reference(Table("public", "users")) == "users"


def test_a_refresh_keeps_the_zoom_and_the_spot_the_user_is_looking_at(
    env: Env, qtbot: QtBot
) -> None:
    pane = ready_pane(env, qtbot)
    qtbot.waitUntil(lambda: not pane._needs_fit, timeout=2000)
    fitted = pane.view.zoom
    pane.view.set_zoom(0.8)
    tag = pane.scene.card(key("tag"))
    assert tag is not None
    pane.view.centerOn(tag)
    centre_before = pane.view.mapToScene(pane.view.viewport().rect().center())
    session = pane._session
    version = session.schema_version
    pane.refresh_button.click()
    qtbot.waitUntil(lambda: session.schema_version > version, timeout=10000)
    pane.update_from_session()
    qtbot.wait(50)
    assert pane.view.zoom == pytest.approx(0.8)
    assert pane.view.zoom != pytest.approx(fitted)
    centre_after = pane.view.mapToScene(pane.view.viewport().rect().center())
    assert abs(centre_after.x() - centre_before.x()) < 5
    assert abs(centre_after.y() - centre_before.y()) < 5


def test_switching_the_schema_scope_fits_the_diagram_again(env: Env, qtbot: QtBot) -> None:
    fake: Any = FakeSession(two_schemas())
    pane = ErdPane(fake, env.services.erd_store, env.services.db)
    qtbot.addWidget(pane)
    pane.resize(700, 600)
    pane.show()
    qtbot.waitUntil(lambda: not pane._needs_fit, timeout=2000)
    pane.view.set_zoom(0.3)
    pane.scope_combo.setCurrentIndex(2)
    pane._on_scope_chosen(2)
    qtbot.waitUntil(lambda: not pane._needs_fit, timeout=2000)
    assert pane.view.zoom != pytest.approx(0.3)
