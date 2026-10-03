from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from pytestqt.qtbot import QtBot

from sql_erd_studio.core.connections import VaultSecretStore
from sql_erd_studio.core.paths import AppPaths
from sql_erd_studio.core.services import build_services
from sql_erd_studio.core.session import SessionState
from sql_erd_studio.core.storage import SettingsStore
from sql_erd_studio.ui.connection_dialog import ConnectionDialog
from sql_erd_studio.ui.main_window import MainWindow
from sql_erd_studio.ui.runtime import BackgroundRunner, EventBridge
from sql_erd_studio.ui.theme import current_tokens

from .conftest import Env, Prompts, shown_texts


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
    assert window.windowTitle() == "SQL ERD Studio"
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
    assert window.windowTitle() == "Shop — SQL ERD Studio"
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
