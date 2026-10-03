from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from pytestqt.qtbot import QtBot

from easydbms.core.connections import VaultSecretStore
from easydbms.core.paths import AppPaths
from easydbms.core.services import build_services
from easydbms.core.session import SessionState
from easydbms.core.storage import SettingsStore
from easydbms.ui.connection_dialog import ConnectionDialog
from easydbms.ui.main_window import MainWindow
from easydbms.ui.runtime import BackgroundRunner, EventBridge
from easydbms.ui.theme import current_tokens

from .conftest import Env, Prompts, add_shop, shown_texts


def make_window(env: Env, qtbot: QtBot) -> MainWindow:
    window = MainWindow(env.services, env.bridge, env.runner)
    qtbot.addWidget(window)
    window.show()
    return window


def wait_for_state(env: Env, qtbot: QtBot, connection_id: str, state: SessionState) -> None:
    qtbot.waitUntil(lambda: env.services.manager.state_of(connection_id) is state, timeout=10000)


def panel_text(window: MainWindow) -> str:
    return shown_texts(window.panel)


# ---------------------------------------------------------------------------- initial state


def test_a_fresh_window_is_empty(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    assert window.windowTitle() == "EasyDBMS"
    assert "No connection" in window._status_label.text()
    assert "No connection selected" in panel_text(window)
    assert "Select a connection" in window.switcher.text()
    assert not window._banner.isVisible()


def test_the_layout_has_the_panel_on_the_left_and_the_switcher_on_the_right(
    env: Env, qtbot: QtBot
) -> None:
    window = make_window(env, qtbot)
    assert window.splitter.widget(0) is window.left
    right = window.splitter.widget(1)
    assert right is not None
    assert window.switcher in right.findChildren(type(window.switcher))
    assert window.splitter.count() == 2
    assert not window.splitter.childrenCollapsible()


# ---------------------------------------------------------------------------- activating


def test_activating_a_connection_connects_in_the_background_and_updates_everything(
    env: Env, qtbot: QtBot
) -> None:
    config = env.add_sqlite("Shop")
    window = make_window(env, qtbot)
    window.activate(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.READY)
    qtbot.waitUntil(lambda: "Connected to Shop" in window._status_label.text(), timeout=5000)
    assert window.windowTitle() == "Shop — EasyDBMS"
    assert window.switcher.text().startswith("Shop")
    assert "SQLite" in panel_text(window)


def test_switching_between_connections(env: Env, qtbot: QtBot) -> None:
    a, b = env.add_sqlite("alpha"), env.add_sqlite("beta")
    window = make_window(env, qtbot)
    window.activate(a.id)
    wait_for_state(env, qtbot, a.id, SessionState.READY)
    window.activate(b.id)
    wait_for_state(env, qtbot, b.id, SessionState.READY)
    qtbot.waitUntil(lambda: window.windowTitle().startswith("beta"), timeout=5000)
    assert env.services.manager.state_of(a.id) is SessionState.READY  # the first stays open


def test_a_failing_connection_shows_the_error_page(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("gone")
    Path(config.path).unlink()
    window = make_window(env, qtbot)
    window.activate(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.ERROR)
    qtbot.waitUntil(lambda: "Could not connect to gone" in panel_text(window), timeout=5000)
    assert "does not exist" in panel_text(window)
    assert "Could not connect" in window._status_label.text()


def test_the_panel_buttons_drive_the_manager(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    window = make_window(env, qtbot)
    window.activate(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.READY)
    window.panel.disconnectRequested.emit(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.DISCONNECTED)
    qtbot.waitUntil(lambda: "is not connected" in panel_text(window), timeout=5000)
    window.panel.retryRequested.emit(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.READY)


def test_unknown_connections_are_ignored(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    window.activate("does-not-exist")
    assert env.services.manager.active_id is None


def test_production_connections_show_a_banner_that_goes_away(env: Env, qtbot: QtBot) -> None:
    prod = env.add_sqlite("prod", production=True)
    dev = env.add_sqlite("dev")
    window = make_window(env, qtbot)
    window.activate(prod.id)
    wait_for_state(env, qtbot, prod.id, SessionState.READY)
    qtbot.waitUntil(window._banner.isVisible, timeout=5000)
    assert "PRODUCTION" in window._banner.text()
    assert "prod" in window._banner.text()
    window.activate(dev.id)
    wait_for_state(env, qtbot, dev.id, SessionState.READY)
    qtbot.waitUntil(lambda: not window._banner.isVisible(), timeout=5000)


# ---------------------------------------------------------------------------- passwords


def test_a_connection_that_does_not_save_its_password_asks_for_it(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = env.add_server("srv", save_password=False)
    prompts.text_answers = [("typed-pw", True)]
    window = make_window(env, qtbot)
    window.activate(config.id)
    assert "Password for srv:" in prompts.text_prompts[0]
    assert not env.services.manager.needs_password(config.id)  # remembered for this run
    assert env.services.manager.active_id == config.id
    window.activate(config.id)  # not asked a second time
    assert len(prompts.text_prompts) == 1


def test_cancelling_the_password_prompt_does_not_connect(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = env.add_server("srv", save_password=False)
    prompts.text_answers = [("", False)]
    window = make_window(env, qtbot)
    window.activate(config.id)
    assert env.services.manager.active_id is None


def test_a_locked_vault_is_unlocked_before_connecting(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    vault = VaultSecretStore(env.tmp_path / "vault.json", scrypt_n=2**10)
    vault.unlock("master")
    config = env.add_server("srv")
    vault.set(config.id, "stored")
    vault.lock()
    services = build_services(
        AppPaths.under(env.tmp_path / "second"), listener=env.bridge.post, secrets=vault
    )
    services.store.save(config)
    window = MainWindow(services, env.bridge, env.runner)
    qtbot.addWidget(window)
    prompts.text_answers = [("master", True)]
    window.activate(config.id)
    assert not vault.locked
    assert services.manager.active_id == config.id
    window.close()


def test_declining_to_unlock_the_vault_aborts_the_connection(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    vault = VaultSecretStore(env.tmp_path / "vault.json", scrypt_n=2**10)
    vault.unlock("master")
    vault.lock()
    services = build_services(
        AppPaths.under(env.tmp_path / "second"), listener=env.bridge.post, secrets=vault
    )
    config = env.add_server("srv")
    services.store.save(config)
    window = MainWindow(services, env.bridge, env.runner)
    qtbot.addWidget(window)
    prompts.text_answers = [("", False)]
    window.activate(config.id)
    assert services.manager.active_id is None
    assert "locked" in window._status_label.text()
    window.close()


# ---------------------------------------------------------------------------- dialog hand-off


def test_the_connections_dialog_is_opened_for_the_active_connection(
    env: Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = env.add_sqlite("lite")
    opened: list[ConnectionDialog] = []

    def fake_exec(self: ConnectionDialog) -> int:
        opened.append(self)
        return 0

    monkeypatch.setattr(ConnectionDialog, "exec", fake_exec)
    window = make_window(env, qtbot)
    window.activate(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.READY)
    window.open_connections()
    assert len(opened) == 1
    assert opened[0].form.name_edit.text() == "lite"


def test_a_connect_request_from_the_dialog_activates_the_connection(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    window = make_window(env, qtbot)
    window._connect_from_dialog(config.id, None)
    wait_for_state(env, qtbot, config.id, SessionState.READY)


def test_a_typed_password_from_the_dialog_is_used_for_this_run(env: Env, qtbot: QtBot) -> None:
    config = env.add_server("srv", save_password=False)
    window = make_window(env, qtbot)
    window._connect_from_dialog(config.id, "typed")
    assert not env.services.manager.needs_password(config.id)


# ---------------------------------------------------------------------------- settings and state


def test_changing_the_theme_applies_and_persists_it(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    window.set_theme("light")
    assert current_tokens().name == "light"
    saved = SettingsStore(env.services.paths.settings_file).load()
    assert saved.theme == "light"
    window.set_theme("dark")
    assert current_tokens().name == "dark"
    assert SettingsStore(env.services.paths.settings_file).load().theme == "dark"


def test_changing_the_language_persists_it_and_explains_the_restart(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    window = make_window(env, qtbot)
    window.set_language("ru")
    assert SettingsStore(env.services.paths.settings_file).load().language == "ru"
    assert prompts.messages
    assert "next time" in prompts.messages[0][1]


def test_window_geometry_and_splitter_are_remembered(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    window.resize(780, 560)  # inside the small offscreen screen, so nothing gets clamped
    window.splitter.setSizes([430, 350])
    qtbot.wait(50)
    saved_sizes = window.splitter.sizes()
    window.close()
    reopened = build_services(
        AppPaths.under(env.tmp_path), listener=env.bridge.post, secrets=env.secrets
    )
    again = MainWindow(reopened, EventBridge(), BackgroundRunner())
    qtbot.addWidget(again)
    try:
        assert reopened.db.get_state("window/splitter") == saved_sizes
        again.show()
        qtbot.wait(50)
        assert (again.width(), again.height()) == (780, 560)
        left, right = again.splitter.sizes()
        assert abs(left - saved_sizes[0]) <= 6
        assert abs(right - saved_sizes[1]) <= 6
    finally:
        again.close()
        reopened.close()


def test_garbage_in_the_saved_state_is_ignored(env: Env, qtbot: QtBot) -> None:
    env.services.db.set_state("window/splitter", ["a", "b"])
    env.services.db.set_state("window/geometry", 12345)
    window = make_window(env, qtbot)
    assert window.splitter.sizes()[0] > 0


def test_startup_notices_report_set_aside_files(
    env: Env, qtbot: QtBot, prompts: Prompts, tmp_path: Path
) -> None:
    config_dir = tmp_path / "damaged" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "connections.json").write_text("{ broken")
    (config_dir / "settings.toml").write_text("theme = ")
    services = build_services(
        AppPaths.under(tmp_path / "damaged"), listener=env.bridge.post, secrets=env.secrets
    )
    window = MainWindow(services, env.bridge, env.runner)
    qtbot.addWidget(window)
    window.show_startup_notices()
    assert len(prompts.messages) == 2
    assert all("kept as" in text for _, text in prompts.messages)
    assert services.settings.theme == "dark"  # defaults were used
    window.close()


def test_no_startup_notice_when_everything_is_fine(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    window = make_window(env, qtbot)
    window.show_startup_notices()
    assert prompts.messages == []
    assert json.loads(json.dumps(env.services.settings.model_dump()))["theme"] == "dark"


# ---------------------------------------------------------------------------- stage 2: workspace


def menu_action(window: MainWindow, menu: str, text: str) -> QAction:
    for top in window.menuBar().actions():
        submenu = top.menu()
        if top.text() == menu and submenu is not None:
            for action in submenu.actions():  # type: ignore[attr-defined]
                if action.text() == text:
                    return action  # type: ignore[no-any-return]
    raise AssertionError(f"no menu action {menu} > {text}")


def test_the_workspace_replaces_the_info_panel_once_connected(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Shop")
    window = make_window(env, qtbot)
    assert window.left.currentWidget() is window.panel
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.workspaces, timeout=10000)
    workspace = window.current_workspace()
    assert workspace is not None
    assert workspace.config.id == config.id
    assert workspace.run_button.isEnabled()


def test_a_failed_connection_shows_the_info_panel_again(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Shop")
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.workspaces, timeout=10000)
    window.panel.disconnectRequested.emit(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.panel, timeout=5000)
    workspace = window._workspaces[config.id]
    assert not workspace.run_button.isEnabled()  # the workspace is kept, but cannot run
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.workspaces, timeout=10000)
    assert window.current_workspace() is workspace  # same tabs as before


def test_each_connection_has_its_own_workspace(env: Env, qtbot: QtBot) -> None:
    a, b = env.add_sqlite("alpha"), env.add_sqlite("beta")
    window = make_window(env, qtbot)
    window.activate(a.id)
    qtbot.waitUntil(lambda: a.id in window._workspaces, timeout=10000)
    window._workspaces[a.id].current_tab().editor.setPlainText("select 'a'")  # type: ignore[union-attr]
    window.activate(b.id)
    qtbot.waitUntil(lambda: b.id in window._workspaces, timeout=10000)
    assert window._workspaces[b.id].current_tab().editor.text() == ""  # type: ignore[union-attr]
    window.activate(a.id)
    qtbot.waitUntil(lambda: window.current_workspace() is window._workspaces[a.id], timeout=10000)
    assert window.current_workspace().current_tab().editor.text() == "select 'a'"  # type: ignore[union-attr]


def test_the_query_menu_runs_queries(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Shop")
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.workspaces, timeout=10000)
    workspace = window.current_workspace()
    assert workspace is not None
    tab = workspace.current_tab()
    assert tab is not None
    tab.editor.setPlainText("select 1 as one")
    tab.editor.go_to(3)
    menu_action(window, "&Query", "&Run").trigger()
    qtbot.waitUntil(lambda: tab.results.count() == 1, timeout=10000)
    menu_action(window, "&Query", "&New tab").trigger()
    assert workspace.tabs.count() == 2
    menu_action(window, "&Query", "&Close tab").trigger()
    assert workspace.tabs.count() == 1


def test_query_shortcuts_are_the_documented_ones(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    shortcuts = {
        text: menu_action(window, "&Query", text).shortcut().toString()
        for text in ("&Run", "Run &script", "S&top", "&Format", "&New tab", "&Close tab")
    }
    assert shortcuts == {
        "&Run": "Ctrl+Return",
        "Run &script": "F5",
        "S&top": "Esc",
        "&Format": "Ctrl+Shift+F",
        "&New tab": "Ctrl+T",
        "&Close tab": "Ctrl+W",
    }


def test_query_actions_do_nothing_without_a_workspace(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    menu_action(window, "&Query", "&Run").trigger()  # no connection yet: must not raise


def test_the_row_limit_menu_persists_the_choice(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    window.set_row_limit(100)
    assert SettingsStore(env.services.paths.settings_file).load().row_limit == 100
    config = env.add_sqlite("Shop")
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.current_workspace() is not None, timeout=10000)
    tab = window.current_workspace().current_tab()  # type: ignore[union-attr]
    tab.editor.setPlainText(  # type: ignore[union-attr]
        "with recursive c(x) as (select 1 union all select x + 1 from c where x < 500) "
        "select x from c"
    )
    tab.editor.go_to(3)  # type: ignore[union-attr]
    window.current_workspace().run_current()  # type: ignore[union-attr]
    qtbot.waitUntil(lambda: tab.results.count() == 1, timeout=10000)  # type: ignore[union-attr]
    assert tab.results.current_page().model.rowCount() == 100  # type: ignore[union-attr]


def test_deleting_a_connection_drops_its_workspace_and_saved_tabs(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Shop")
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(lambda: config.id in window._workspaces, timeout=10000)
    window._workspaces[config.id].current_tab().editor.setPlainText("select 1")  # type: ignore[union-attr]
    window._workspaces[config.id].flush()
    assert env.services.tab_store.load(config.id)
    env.services.store.remove(config.id)
    window._refresh()
    assert config.id not in window._workspaces
    assert env.services.tab_store.load(config.id) == []


def test_tabs_survive_a_restart(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Shop")
    first = make_window(env, qtbot)
    first.activate(config.id)
    qtbot.waitUntil(lambda: config.id in first._workspaces, timeout=10000)
    workspace = first._workspaces[config.id]
    workspace.current_tab().editor.setPlainText("select 'remember me'")  # type: ignore[union-attr]
    workspace.new_tab("Second", "select 2")
    first.close()

    bridge = EventBridge()
    services = build_services(
        AppPaths.under(env.tmp_path), listener=bridge.post, secrets=env.secrets
    )
    second = MainWindow(services, bridge, BackgroundRunner())
    qtbot.addWidget(second)
    try:
        second.activate(config.id)
        qtbot.waitUntil(lambda: config.id in second._workspaces, timeout=10000)
        restored = second._workspaces[config.id]
        assert [restored.tabs.tabText(i) for i in range(2)] == ["Query 1", "Second"]
        first_tab = restored.tabs.widget(0)
        assert first_tab is not None
        assert first_tab.editor.text() == "select 'remember me'"  # type: ignore[attr-defined]
    finally:
        second.close()


# ---------------------------------------------------------------------------- the diagram


def shop_window(env: Env, qtbot: QtBot, name: str = "Shop") -> tuple[MainWindow, str]:
    from .conftest import add_shop

    config = add_shop(env, name, rows=12)
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(
        lambda: (p := window._erd_panes.get(config.id)) is not None and len(p.scene.cards()) > 0,
        timeout=10000,
    )
    return window, config.id


def test_the_diagram_hint_shows_until_a_connection_is_ready(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    assert window.erd_stack.currentWidget() is window._erd_hint
    assert window.current_pane() is None


def test_connecting_shows_the_diagram_of_the_database(env: Env, qtbot: QtBot) -> None:
    window, cid = shop_window(env, qtbot)
    pane = window.current_pane()
    assert pane is window._erd_panes[cid]
    assert len(pane.scene.cards()) == 6
    assert "6 tables" in pane.summary.text()
    assert window.erd_stack.currentWidget() is pane
    assert window.switcher.text().startswith("Shop")  # the switcher stays above the diagram


def test_each_connection_has_its_own_diagram(env: Env, qtbot: QtBot) -> None:
    window, first = shop_window(env, qtbot, "One")
    second_cfg = env.add_sqlite("Two")  # an empty database
    window.activate(second_cfg.id)
    qtbot.waitUntil(lambda: second_cfg.id in window._erd_panes, timeout=10000)
    assert window.current_pane() is window._erd_panes[second_cfg.id]
    assert window._erd_panes[first] is not window._erd_panes[second_cfg.id]
    window.activate(first)
    qtbot.waitUntil(lambda: window.current_pane() is window._erd_panes[first], timeout=10000)
    assert len(window._erd_panes[first].scene.cards()) == 6


def test_a_failed_connection_goes_back_to_the_hint(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Broken")
    window = make_window(env, qtbot)
    config_path = config.path
    from pathlib import Path

    Path(config_path).unlink()
    window.activate(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.ERROR)
    qtbot.waitUntil(lambda: window.erd_stack.currentWidget() is window._erd_hint, timeout=5000)


def test_opening_a_table_from_the_diagram_shows_its_rows(env: Env, qtbot: QtBot) -> None:
    window, cid = shop_window(env, qtbot)
    pane = window._erd_panes[cid]
    book = pane.scene.card(next(k for k in (c.key for c in pane.scene.cards()) if k.name == "book"))
    assert book is not None
    pane.scene.card_opened(book)
    workspace = window._workspaces[cid]
    tab = workspace.current_tab()
    assert tab is not None
    qtbot.waitUntil(lambda: tab.results.table_count() == 1, timeout=5000)
    table_tab = tab.results.table_tab(book.key)
    assert table_tab is not None
    qtbot.waitUntil(lambda: not table_tab._loading, timeout=5000)
    assert table_tab.grid.model().rowCount() == 12  # all books


def test_a_column_clicked_in_the_diagram_is_typed_into_the_editor(env: Env, qtbot: QtBot) -> None:
    window, cid = shop_window(env, qtbot)
    pane = window._erd_panes[cid]
    book = next(c for c in pane.scene.cards() if c.table.name == "book")
    pane.scene.card_clicked(book, book.table.column("title"))
    tab = window._workspaces[cid].current_tab()
    assert tab is not None
    assert tab.editor.text().endswith("title")
    pane.selectStarRequested.emit(book.table)
    assert "SELECT * FROM book LIMIT 100;" in tab.editor.text()


def test_the_database_menu(env: Env, qtbot: QtBot) -> None:
    window, cid = shop_window(env, qtbot)
    assert menu_action(window, "&Database", "&Go to table…").shortcut().toString() == "Ctrl+P"
    assert (
        menu_action(window, "&Database", "&Refresh structure").shortcut().toString()
        == "Ctrl+Shift+R"
    )
    session = env.services.manager.session(cid)
    version = session.schema_version
    menu_action(window, "&Database", "&Refresh structure").trigger()
    qtbot.waitUntil(lambda: session.schema_version > version, timeout=10000)

    pane = window._erd_panes[cid]
    tag = next(c for c in pane.scene.cards() if c.table.name == "tag")
    tag.setPos(500, 500)
    pane.scene.card_moved(tag)
    menu_action(window, "&Database", "&Reset diagram layout").trigger()
    assert env.services.erd_store.load(cid, "*") == {}
    view = pane.view
    view.set_zoom(0.3)
    menu_action(window, "&Database", "&Fit diagram").trigger()
    assert view.zoom != 0.3


def test_database_menu_actions_do_nothing_without_a_connection(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    for text in ("&Refresh structure", "&Fit diagram", "&Reset diagram layout"):
        menu_action(window, "&Database", text).trigger()
    menu_action(window, "&Database", "&Go to table…").trigger()
    assert "not loaded yet" in window._status_label.text()


def test_go_to_table_selects_it_in_the_diagram_and_opens_its_rows(
    env: Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easydbms.ui.quick_open import QuickOpenDialog

    window, cid = shop_window(env, qtbot)
    session = env.services.manager.session(cid)
    assert session.schema is not None
    wanted = session.schema.find("author")

    def fake_exec(self: QuickOpenDialog) -> bool:
        self.chosen = (wanted, None)  # type: ignore[assignment]
        return True

    monkeypatch.setattr(QuickOpenDialog, "exec", fake_exec)
    menu_action(window, "&Database", "&Go to table…").trigger()
    pane = window._erd_panes[cid]
    assert pane.scene.focus_key == wanted.key  # type: ignore[union-attr]
    tab = window._workspaces[cid].current_tab()
    assert tab is not None
    assert tab.results.table_tab(wanted.key) is not None  # type: ignore[union-attr]


def test_go_to_table_cancelled_does_nothing(
    env: Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from easydbms.ui.quick_open import QuickOpenDialog

    window, cid = shop_window(env, qtbot)
    monkeypatch.setattr(QuickOpenDialog, "exec", lambda self: False)
    menu_action(window, "&Database", "&Go to table…").trigger()
    tab = window._workspaces[cid].current_tab()
    assert tab is not None
    assert tab.results.table_count() == 0


def test_the_diagram_can_be_collapsed_and_the_choice_is_remembered(env: Env, qtbot: QtBot) -> None:
    window, _ = shop_window(env, qtbot)
    action = menu_action(window, "&Database", "Show the &diagram")
    assert action.isChecked()
    window.collapse_button.click()
    assert not window.erd_stack.isVisible()
    assert not action.isChecked()
    assert window._erd_pane_widget.maximumWidth() == 300
    assert env.services.db.get_state("window/erd_collapsed") is True
    window.set_erd_collapsed(False)
    assert window.erd_stack.isVisible()
    assert action.isChecked()
    assert window._erd_pane_widget.maximumWidth() > 1000
    window.set_erd_collapsed(True)
    window.close()
    reopened = build_services(
        AppPaths.under(env.tmp_path), listener=env.bridge.post, secrets=env.secrets
    )
    again = MainWindow(reopened, EventBridge(), BackgroundRunner())
    qtbot.addWidget(again)
    try:
        assert again._erd_collapsed
        assert not again.erd_stack.isVisible()
    finally:
        again.close()
        reopened.close()


def test_deleting_a_connection_drops_its_diagram_and_saved_positions(
    env: Env, qtbot: QtBot
) -> None:
    window, cid = shop_window(env, qtbot)
    env.services.erd_store.save(
        cid, "*", {next(iter(c.key for c in window._erd_panes[cid].scene.cards())): (1.0, 2.0)}
    )
    env.services.store.remove(cid)
    window._refresh()
    assert cid not in window._erd_panes
    assert env.services.erd_store.load(cid, "*") == {}


def test_changing_the_theme_recolours_the_diagram(env: Env, qtbot: QtBot) -> None:
    window, cid = shop_window(env, qtbot)
    pane = window._erd_panes[cid]
    before = pane.scene.cards()[0]._colors.card.name()
    window.set_theme("light")
    assert pane.scene.cards()[0]._colors.card.name() != before
    window.set_theme("dark")


# ---------------------------------------------------------------------------- autocomplete


def test_the_keyword_case_menu_persists_the_choice(env: Env, qtbot: QtBot) -> None:
    window = make_window(env, qtbot)
    assert SettingsStore(env.services.paths.settings_file).load().keyword_case == "upper"
    case_menu = next(
        a.menu()
        for a in menu_action_list(window, "&Query")
        if a.menu() is not None and a.text() == "Keyword case"
    )
    assert case_menu is not None
    actions = case_menu.actions()
    assert [a.isChecked() for a in actions] == [True, False, False]
    actions[1].trigger()
    assert env.services.settings.keyword_case == "lower"
    assert SettingsStore(env.services.paths.settings_file).load().keyword_case == "lower"
    window.set_keyword_case("bogus")  # ignored
    assert env.services.settings.keyword_case == "lower"


def menu_action_list(window: MainWindow, menu: str) -> list[QAction]:
    for action in window.menuBar().actions():
        if action.text() == menu and action.menu() is not None:
            return action.menu().actions()  # type: ignore[union-attr]
    raise AssertionError(menu)


def test_the_autocomplete_menu_item_opens_the_list(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Shop")
    window = make_window(env, qtbot)
    action = menu_action(window, "&Query", "&Autocomplete")
    assert action.shortcut().toString() == "Ctrl+Space"
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.workspaces, timeout=10000)
    tab = window.current_workspace().current_tab()  # type: ignore[union-attr]
    action.trigger()
    qtbot.waitUntil(lambda: tab.editor.completion.visible, timeout=5000)  # type: ignore[union-attr]


def test_the_keyword_case_setting_reaches_the_editors(env: Env, qtbot: QtBot) -> None:
    config = add_shop(env, "Shop")
    window = make_window(env, qtbot)
    window.set_keyword_case("lower")
    window.activate(config.id)
    qtbot.waitUntil(lambda: window.left.currentWidget() is window.workspaces, timeout=10000)
    tab = window.current_workspace().current_tab()  # type: ignore[union-attr]
    tab.editor.completion.debounce_ms = 5  # type: ignore[union-attr]
    from PySide6.QtTest import QTest

    QTest.keyClicks(tab.editor, "SEL")  # type: ignore[union-attr]
    qtbot.waitUntil(lambda: tab.editor.completion.visible, timeout=5000)  # type: ignore[union-attr]
    model = tab.editor.completion.popup.model  # type: ignore[union-attr]
    assert "select" in [model.item(r).label for r in range(model.rowCount())]  # type: ignore[union-attr]


def test_deleting_a_connection_forgets_what_was_accepted(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("Shop")
    window = make_window(env, qtbot)
    window.activate(config.id)
    qtbot.waitUntil(lambda: config.id in window._workspaces, timeout=10000)
    env.services.usage_store.record(config.id, "table:main.book")
    env.services.store.remove(config.id)
    window._refresh()
    assert dict(env.services.usage_store.counts(config.id)) == {}


def test_usage_of_connections_that_vanished_is_pruned_at_startup(env: Env, qtbot: QtBot) -> None:
    kept = env.add_sqlite("Kept")
    env.services.usage_store.record(kept.id, "a")
    env.services.usage_store.record("gone", "a")
    make_window(env, qtbot)
    rows = env.services.db.execute("SELECT DISTINCT connection_id FROM completion_usage")
    assert rows == [(kept.id,)]
