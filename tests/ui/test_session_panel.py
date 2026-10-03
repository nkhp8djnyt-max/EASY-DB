from __future__ import annotations

import pytest
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from sql_erd_studio.core.db import ConnectionFailed, DatabaseFileError
from sql_erd_studio.core.session import Session, SessionState
from sql_erd_studio.ui.session_panel import SessionPanel

from .conftest import Env, shown_texts


@pytest.fixture
def panel(qtbot: QtBot) -> SessionPanel:
    widget = SessionPanel()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def texts(panel: SessionPanel) -> str:
    return shown_texts(panel)


def button(panel: SessionPanel, text: str) -> QPushButton:
    page = panel._stack.currentWidget()
    assert page is not None
    return next(b for b in page.findChildren(QPushButton) if b.text() == text)


def test_no_session_shows_the_empty_state_with_a_connections_button(
    panel: SessionPanel, qtbot: QtBot
) -> None:
    panel.show_session(None)
    assert "No connection selected" in texts(panel)
    with qtbot.waitSignal(panel.manageRequested):
        button(panel, "Connections…").click()


def test_a_ready_session_shows_what_it_is_connected_to(env: Env, panel: SessionPanel) -> None:
    config = env.add_sqlite("lite")
    session = env.services.manager.activate(config.id).result(timeout=5)
    panel.show_session(session)
    shown = texts(panel)
    assert "lite" in shown
    assert "SQLite" in shown
    assert config.path in shown
    assert "Read and write" in shown
    assert "PRODUCTION" not in shown


def test_read_only_and_production_are_called_out(env: Env, panel: SessionPanel) -> None:
    config = env.add_sqlite("prod", production=True, read_only=True)
    session = env.services.manager.activate(config.id).result(timeout=5)
    panel.show_session(session)
    shown = texts(panel)
    assert "PRODUCTION" in shown
    assert "Read-only" in shown


def test_ready_buttons_emit_the_connection_id(env: Env, panel: SessionPanel, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    panel.show_session(env.services.manager.activate(config.id).result(timeout=5))
    with qtbot.waitSignal(panel.disconnectRequested) as signal:
        button(panel, "Disconnect").click()
    assert signal.args == [config.id]


def test_a_failed_session_shows_the_error_and_a_hint(env: Env, panel: SessionPanel) -> None:
    config = env.add_sqlite("gone")
    session = Session(config)
    session.fail(DatabaseFileError("the file 'x' does not exist"))
    panel.show_session(session)
    shown = texts(panel)
    assert "Could not connect to gone" in shown
    assert "does not exist" in shown
    assert "Check the path of the database file." in shown


def test_failed_session_buttons(env: Env, panel: SessionPanel, qtbot: QtBot) -> None:
    config = env.add_sqlite("gone")
    session = Session(config)
    session.fail(ConnectionFailed("boom"))
    panel.show_session(session)
    with qtbot.waitSignal(panel.retryRequested) as retry:
        button(panel, "Retry").click()
    with qtbot.waitSignal(panel.editRequested) as edit:
        button(panel, "Edit connection…").click()
    assert retry.args == edit.args == [config.id]


def test_an_error_without_a_known_hint_hides_the_hint_line(env: Env, panel: SessionPanel) -> None:
    session = Session(env.add_sqlite("x"))
    session.fail(ConnectionFailed("something odd"))
    panel.show_session(session)
    assert "something odd" in texts(panel)
    assert panel._error_hint.isHidden()


def test_a_connecting_session_shows_progress_and_can_be_cancelled(
    env: Env, panel: SessionPanel, qtbot: QtBot
) -> None:
    config = env.add_sqlite("slow")
    session = Session(config)
    session._state = SessionState.CONNECTING  # the state a worker thread would be in
    panel.show_session(session)
    assert "Connecting to slow…" in texts(panel)
    with qtbot.waitSignal(panel.cancelRequested) as signal:
        button(panel, "Cancel").click()
    assert signal.args == [config.id]


def test_a_disconnected_session_is_not_shown_as_an_error(env: Env, panel: SessionPanel) -> None:
    session = Session(env.add_sqlite("idle"))
    panel.show_session(session)
    assert "idle is not connected" in texts(panel)
