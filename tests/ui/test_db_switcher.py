from __future__ import annotations

from PySide6.QtGui import QAction
from pytestqt.qtbot import QtBot

from sql_erd_studio.ui.db_switcher import DbSwitcher

from .conftest import Env


def make_switcher(env: Env, qtbot: QtBot) -> DbSwitcher:
    switcher = DbSwitcher(env.services.store, env.services.manager)
    qtbot.addWidget(switcher)
    return switcher


def menu_entries(switcher: DbSwitcher) -> list[QAction]:
    switcher.menu().aboutToShow.emit()
    return switcher.menu().actions()


def test_without_a_selection_the_button_invites_to_choose(env: Env, qtbot: QtBot) -> None:
    switcher = make_switcher(env, qtbot)
    assert "Select a connection" in switcher.text()
    assert switcher.property("production") is False


def test_the_menu_lists_connections_and_a_manage_entry(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("alpha")
    env.add_sqlite("beta")
    switcher = make_switcher(env, qtbot)
    entries = menu_entries(switcher)
    labels = [a.text() for a in entries if not a.isSeparator()]
    assert labels == ["alpha", "beta", "Manage connections…"]


def test_groups_get_disabled_headers_only_when_there_are_several(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("solo")
    switcher = make_switcher(env, qtbot)
    assert [a.text() for a in menu_entries(switcher)][:1] == ["solo"]
    env.add_sqlite("work-db", group="work")
    entries = menu_entries(switcher)
    headers = [a.text() for a in entries if not a.isEnabled() and a.text()]
    assert headers == ["WORK", "NO GROUP"]
    order = [a.text() for a in entries if a.isEnabled() and not a.isSeparator()]
    assert order == ["work-db", "solo", "Manage connections…"]


def test_an_empty_store_says_so(env: Env, qtbot: QtBot) -> None:
    switcher = make_switcher(env, qtbot)
    entries = menu_entries(switcher)
    assert entries[0].text() == "No saved connections"
    assert not entries[0].isEnabled()


def test_choosing_an_entry_emits_the_connection_id(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("alpha")
    target = env.add_sqlite("beta")
    switcher = make_switcher(env, qtbot)
    entry = next(a for a in menu_entries(switcher) if a.text() == "beta")
    with qtbot.waitSignal(switcher.connectionChosen) as signal:
        entry.trigger()
    assert signal.args == [target.id]


def test_the_manage_entry_emits_manage_requested(env: Env, qtbot: QtBot) -> None:
    switcher = make_switcher(env, qtbot)
    entry = next(a for a in menu_entries(switcher) if a.text() == "Manage connections…")
    with qtbot.waitSignal(switcher.manageRequested):
        entry.trigger()


def test_the_active_connection_is_checked_and_named_on_the_button(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("alpha")
    b = env.add_sqlite("beta")
    env.services.manager.activate(b.id).result(timeout=5)
    switcher = make_switcher(env, qtbot)
    assert switcher.text().startswith("beta")
    checked = [a.text() for a in menu_entries(switcher) if a.isChecked()]
    assert checked == ["beta"]
    assert "Connected" in switcher.toolTip()


def test_production_connections_mark_the_button(env: Env, qtbot: QtBot) -> None:
    prod = env.add_sqlite("prod", production=True)
    env.services.manager.activate(prod.id).result(timeout=5)
    switcher = make_switcher(env, qtbot)
    assert switcher.property("production") is True


def test_refresh_follows_state_changes(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("alpha")
    switcher = make_switcher(env, qtbot)
    env.services.manager.activate(config.id).result(timeout=5)
    switcher.refresh()
    assert "Connected" in switcher.toolTip()
    env.services.manager.disconnect(config.id)
    switcher.refresh()
    assert "Not connected" in switcher.toolTip()
    env.services.manager.forget(config.id)
    switcher.refresh()
    assert "Select a connection" in switcher.text()
