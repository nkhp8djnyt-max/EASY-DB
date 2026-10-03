"""The SSL / SSH / service parts of the connection form, dialog, session panel and main window."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from easydbms.core.connections import (
    JUMP_PASSWORD,
    PASSWORD,
    SSH_PASSPHRASE,
    SSH_PASSWORD,
    SSL_KEY_PASSWORD,
    ServerConnection,
    SshAuth,
    SslMode,
)
from easydbms.core.session import SessionState
from easydbms.core.ssh import SshHostKeyUnknown, SshTunnelError
from easydbms.ui.connection_dialog import ConnectionDialog
from easydbms.ui.connection_form import ConnectionForm, FormError
from easydbms.ui.main_window import MainWindow
from tests.support.ssh_server import EchoServer, FakeSshServer, closed_port

from .conftest import Env, Prompts, shown_texts
from .test_connection_dialog import make_dialog
from .test_main_window import make_window, wait_for_state


@pytest.fixture
def form(qtbot: QtBot) -> ConnectionForm:
    widget = ConnectionForm("the keyring")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def server(**overrides: object) -> ServerConnection:
    data = {"name": "Shop", "dialect": "postgresql", "host": "db", "user": "u"} | overrides
    return ServerConnection.model_validate(data)


# ---------------------------------------------------------------------------- form: SSL


def test_the_new_tabs_exist_for_servers_and_not_for_files(form: ConnectionForm) -> None:
    assert [form.tabs.tabText(i) for i in range(form.tabs.count())] == [
        "URL",
        "Host / Port",
        "SSL / TLS",
        "SSH tunnel",
        "Cloud",
    ]
    assert form.tabs.isTabVisible(2)
    assert form.tabs.isTabVisible(3)
    form.dialect_buttons[form.dialect_id.SQLITE].setChecked(True)
    assert not form.tabs.isTabVisible(2)
    assert not form.tabs.isTabVisible(3)


def test_ssl_settings_round_trip_and_reach_the_url(form: ConnectionForm) -> None:
    original = server(
        ssl={"mode": "verify-full", "ca_file": "/ca.pem", "cert_file": "/c.pem", "key_file": "/k"}
    )
    form.load(original)
    assert form.ssl_editor.mode is SslMode.VERIFY_FULL
    assert form.ssl_editor.ca_file.text() == "/ca.pem"
    assert form.read_config(original.id) == original
    assert "sslmode=verify-full" in form.url_edit.text()
    assert "sslrootcert=%2Fca.pem" in form.url_edit.text()


def test_choosing_a_mode_rewrites_the_url_and_explains_itself(form: ConnectionForm) -> None:
    form.load(server())
    index = form.ssl_editor.mode_combo.findData("require")
    form.ssl_editor.mode_combo.setCurrentIndex(index)
    assert "sslmode=require" in form.url_edit.text()
    assert "identity is not checked" in form.ssl_editor.hint.text()
    form.ssl_editor.mode_combo.setCurrentIndex(form.ssl_editor.mode_combo.findData("verify-ca"))
    assert "signed by the CA" in form.ssl_editor.hint.text()


def test_typing_a_url_with_tls_parameters_fills_the_ssl_tab(form: ConnectionForm) -> None:
    form.url_edit.setText("postgresql://u@h/d?sslmode=verify-ca&sslrootcert=/etc/ca.pem")
    form.url_edit.textEdited.emit(form.url_edit.text())
    assert form.ssl_editor.mode is SslMode.VERIFY_CA
    assert form.ssl_editor.ca_file.text() == "/etc/ca.pem"


def test_allow_is_for_postgresql_only(form: ConnectionForm) -> None:
    form.load(server(ssl={"mode": "allow"}))
    modes: list[SslMode | None] = [form.ssl_editor.mode]
    form.dialect_buttons[form.dialect_id.MYSQL].setChecked(True)
    modes.append(form.ssl_editor.mode)
    assert modes == [SslMode.ALLOW, SslMode.PREFER]


def test_the_key_passphrase_is_a_secret_not_a_setting(form: ConnectionForm) -> None:
    form.load(server(ssl={"mode": "require", "cert_file": "/c", "key_file": "/k"}))
    form.ssl_editor.key_password.setText("hunter2")
    assert form.secrets == {SSL_KEY_PASSWORD: "hunter2"}
    assert SSL_KEY_PASSWORD in form.applicable_secret_fields
    assert "hunter2" not in repr(form.read_config("x").model_dump())
    form.ssl_editor.key_file.setText("")
    assert SSL_KEY_PASSWORD not in form.applicable_secret_fields


# ---------------------------------------------------------------------------- form: SSH


def test_ssh_settings_round_trip_with_a_jump_host(form: ConnectionForm) -> None:
    original = server(
        ssh={
            "server": {
                "host": "inner",
                "port": 2222,
                "user": "me",
                "auth": "key",
                "key_file": "/k",
            },
            "jump": {"host": "bastion", "user": "jumper", "auth": "password"},
        }
    )
    form.load(original)
    assert form.ssh_editor.enabled
    assert form.ssh_editor.jump_check.isChecked()
    assert form.ssh_editor.server.auth is SshAuth.KEY
    assert form.ssh_editor.jump.host_edit.text() == "bastion"
    assert form.read_config(original.id) == original


def test_ssh_is_off_by_default_and_saves_as_none(form: ConnectionForm) -> None:
    form.load(server())
    assert not form.ssh_editor.enabled
    assert form.read_config("x").ssh is None  # type: ignore[union-attr]
    assert form.secrets == {}


def test_ssh_secrets_follow_the_login_method(form: ConnectionForm) -> None:
    form.load(server(ssh={"server": {"host": "h", "auth": "password"}}))
    form.ssh_editor.server.secret_edit.setText("pw")
    assert form.secrets == {SSH_PASSWORD: "pw"}
    form.ssh_editor.server.auth_combo.setCurrentIndex(
        form.ssh_editor.server.auth_combo.findData("key")
    )
    form.ssh_editor.server.key_file.setText("/k")
    assert form.secrets == {SSH_PASSPHRASE: "pw"}
    form.ssh_editor.server.auth_combo.setCurrentIndex(
        form.ssh_editor.server.auth_combo.findData("agent")
    )
    assert form.secrets == {}
    assert form.applicable_secret_fields == []


def test_a_jump_host_adds_its_own_secrets(form: ConnectionForm) -> None:
    form.load(
        server(
            ssh={
                "server": {"host": "h", "auth": "password"},
                "jump": {"host": "j", "auth": "password"},
            }
        )
    )
    form.ssh_editor.jump.secret_edit.setText("jp")
    assert form.secrets == {JUMP_PASSWORD: "jp"}
    form.ssh_editor.jump_check.setChecked(False)
    assert form.secrets == {}


def test_an_enabled_tunnel_needs_its_host(form: ConnectionForm) -> None:
    form.load(server())
    form.name_edit.setText("x")
    form.ssh_editor.enable_check.setChecked(True)
    with pytest.raises(FormError, match="SSH server host"):
        form.read_config("x")
    form.ssh_editor.server.host_edit.setText("bastion")
    form.ssh_editor.jump_check.setChecked(True)
    with pytest.raises(FormError, match="jump host"):
        form.read_config("x")


def test_a_key_login_without_a_key_file_is_reported(form: ConnectionForm) -> None:
    form.load(server())
    form.name_edit.setText("x")
    form.ssh_editor.enable_check.setChecked(True)
    form.ssh_editor.server.host_edit.setText("bastion")
    form.ssh_editor.server.auth_combo.setCurrentIndex(
        form.ssh_editor.server.auth_combo.findData("key")
    )
    with pytest.raises(FormError, match="private key file"):
        form.read_config("x")


# ---------------------------------------------------------------------------- form: service


def test_a_service_may_replace_the_host(form: ConnectionForm) -> None:
    form.load(server(host="", service="prod"))
    assert form.service_combo.currentText() == "prod"
    config = form.read_config("x")
    assert isinstance(config, ServerConnection)
    assert (config.host, config.service) == ("", "prod")
    form.service_combo.setEditText("")
    with pytest.raises(FormError, match="host name"):
        form.read_config("x")


def test_services_found_in_the_service_file_are_offered(
    form: ConnectionForm, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_file = tmp_path / "svc.conf"
    service_file.write_text("[prod]\nhost=a\n[dev]\nhost=b\n")
    monkeypatch.setenv("PGSERVICEFILE", str(service_file))
    monkeypatch.setenv("PGSYSCONFDIR", str(tmp_path / "none"))
    form.load(server())
    items = [form.service_combo.itemText(i) for i in range(form.service_combo.count())]
    assert items == ["dev", "prod"]


def test_services_and_the_password_hint_are_for_postgresql_only(form: ConnectionForm) -> None:
    form.load(server())
    assert not form.service_combo.isHidden()
    form.dialect_buttons[form.dialect_id.MYSQL].setChecked(True)
    assert form.service_combo.isHidden()
    assert form.pgpass_hint.isHidden()
    assert form.read_config("x").service == ""  # type: ignore[union-attr]


def test_the_form_says_whether_pgpass_knows_the_connection(
    form: ConnectionForm, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("db:5432:*:u:secret\n")
    pgpass.chmod(0o600)
    monkeypatch.setenv("PGPASSFILE", str(pgpass))
    form.load(server(host="db", user="u"))
    assert "line 1" in form.pgpass_hint.text()
    assert "secret" not in form.pgpass_hint.text()
    form.user_edit.setText("someone-else")
    form.user_edit.textEdited.emit("someone-else")
    assert "no line for this connection" in form.pgpass_hint.text()
    pgpass.chmod(0o644)
    form.user_edit.textEdited.emit("u")
    assert "chmod 0600" in form.pgpass_hint.text()


# ---------------------------------------------------------------------------- dialog: secrets


def fill_ssh(dialog: ConnectionDialog, password: str = "ssh-pw") -> None:
    dialog.new_button.click()
    form = dialog.form
    form.name_edit.setText("Tunnelled")
    form.host_edit.setText("db.internal")
    form.host_edit.textEdited.emit("db.internal")
    form.ssh_editor.enable_check.setChecked(True)
    form.ssh_editor.server.host_edit.setText("bastion.example.com")
    form.ssh_editor.server.user_edit.setText("me")
    form.ssh_editor.server.auth_combo.setCurrentIndex(
        form.ssh_editor.server.auth_combo.findData("password")
    )
    form.ssh_editor.server.secret_edit.setText(password)
    form.password_edit.setText("db-pw")


def test_saving_stores_every_secret_in_the_secret_store_and_none_in_the_file(
    env: Env, qtbot: QtBot
) -> None:
    dialog = make_dialog(env, qtbot)
    fill_ssh(dialog)
    config = dialog._save()
    assert config is not None
    assert env.secrets.get(config.id, PASSWORD) == "db-pw"
    assert env.secrets.get(config.id, SSH_PASSWORD) == "ssh-pw"
    text = env.services.paths.connections_file.read_text()
    assert "bastion.example.com" in text
    assert "ssh-pw" not in text
    assert "db-pw" not in text


def test_a_saved_ssh_secret_is_announced_and_stale_ones_are_dropped(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    fill_ssh(dialog)
    config = dialog._save()
    assert config is not None
    dialog._load(env.services.store.get(config.id))
    assert "saved secret" in dialog.form.ssh_editor.server.secret_edit.placeholderText()
    form = dialog.form
    form.ssh_editor.server.auth_combo.setCurrentIndex(
        form.ssh_editor.server.auth_combo.findData("agent")
    )
    assert dialog._save() is not None
    assert env.secrets.get(config.id, SSH_PASSWORD) is None  # the login method changed
    assert env.secrets.get(config.id, PASSWORD) == "db-pw"


def test_unsaved_secrets_are_passed_along_with_the_connect_request(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    fill_ssh(dialog)
    dialog.form.save_password_check.setChecked(False)
    with qtbot.waitSignal(dialog.connectRequested) as signal:
        dialog.connect_button.click()
    connection_id, typed, secrets = signal.args
    assert typed == "db-pw"
    assert secrets == {SSH_PASSWORD: "ssh-pw"}
    assert env.secrets.get(connection_id, SSH_PASSWORD) is None


def test_deleting_a_connection_forgets_its_ssh_secrets(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    dialog = make_dialog(env, qtbot)
    fill_ssh(dialog)
    config = dialog._save()
    assert config is not None
    dialog._delete()
    assert env.secrets.get(config.id, SSH_PASSWORD) is None
    assert env.secrets.get(config.id, PASSWORD) is None


# ---------------------------------------------------------------------------- dialog: test report


@pytest.fixture
def bastion() -> Iterator[FakeSshServer]:
    with FakeSshServer(users={"alice": "wonderland"}) as running:
        yield running


@pytest.fixture
def echo() -> Iterator[EchoServer]:
    with EchoServer() as running:
        yield running


def fill_tunnel_to(dialog: ConnectionDialog, bastion: FakeSshServer, echo: EchoServer) -> None:
    dialog.new_button.click()
    form = dialog.form
    form.name_edit.setText("Through the tunnel")
    form.host_edit.setText("127.0.0.1")
    form.host_edit.textEdited.emit("127.0.0.1")
    form.port_spin.setValue(echo.port)
    form.ssh_editor.enable_check.setChecked(True)
    form.ssh_editor.server.host_edit.setText("127.0.0.1")
    form.ssh_editor.server.port_spin.setValue(bastion.port)
    form.ssh_editor.server.user_edit.setText("alice")
    form.ssh_editor.server.auth_combo.setCurrentIndex(
        form.ssh_editor.server.auth_combo.findData("password")
    )
    form.ssh_editor.server.secret_edit.setText("wonderland")


def report_text(dialog: ConnectionDialog) -> str:
    return shown_texts_of(dialog.report)


def shown_texts_of(widget: object) -> str:
    from PySide6.QtWidgets import QLabel, QWidget

    assert isinstance(widget, QWidget)
    return " | ".join(label.text() for label in widget.findChildren(QLabel))


def test_the_report_asks_to_trust_an_unknown_ssh_server_and_then_goes_on(
    env: Env, qtbot: QtBot, prompts: Prompts, bastion: FakeSshServer, echo: EchoServer
) -> None:
    dialog = make_dialog(env, qtbot)
    fill_tunnel_to(dialog, bastion, echo)
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    text = report_text(dialog)
    assert "SSH login" in text
    assert "not known yet" in text
    trust = next(b for b in dialog.report.findChildren(QPushButton) if "Trust" in b.text())
    trust.click()
    assert prompts.questions
    assert "SHA256:" in prompts.questions[0]
    qtbot.waitUntil(lambda: "SSH tunnel" in report_text(dialog), timeout=10000)
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    text = report_text(dialog)
    assert "SSH tunnel" in text  # the tunnel to the (echo) "database" is up ...
    assert "SSH login" in text
    assert env.services.paths.known_hosts_file.read_text().strip()  # ... and the key is remembered
    dialog._dirty = False


def test_declining_to_trust_changes_nothing(
    env: Env, qtbot: QtBot, prompts: Prompts, bastion: FakeSshServer, echo: EchoServer
) -> None:
    prompts.question_answer = QMessageBox.StandardButton.No
    dialog = make_dialog(env, qtbot)
    fill_tunnel_to(dialog, bastion, echo)
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    next(b for b in dialog.report.findChildren(QPushButton) if "Trust" in b.text()).click()
    assert not env.services.paths.known_hosts_file.exists()
    assert dialog.test_button.isEnabled()
    dialog._dirty = False


def test_a_wrong_ssh_password_is_explained(
    env: Env, qtbot: QtBot, bastion: FakeSshServer, echo: EchoServer
) -> None:
    env.services.manager.known_hosts.trust(  # type: ignore[union-attr]
        "127.0.0.1", bastion.port, bastion.host_key
    )
    dialog = make_dialog(env, qtbot)
    fill_tunnel_to(dialog, bastion, echo)
    dialog.form.ssh_editor.server.secret_edit.setText("wrong")
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    text = report_text(dialog)
    assert "rejected the password login" in text
    assert "refused the login" in text  # the hint
    dialog._dirty = False  # closing a dirty dialog would ask a modal question


# ------------------------------------------------------------------ session panel / window


def tunnelled_config(env: Env, bastion: FakeSshServer, echo: EchoServer) -> ServerConnection:
    """A connection whose database is not listening: the SSH side can be tested on its own."""
    return env.add_server(
        "tunnelled",
        host="127.0.0.1",
        port=closed_port(),
        save_password=False,
        ssh={
            "server": {
                "host": "127.0.0.1",
                "port": bastion.port,
                "user": "alice",
                "auth": "password",
            }
        },
    )


def test_the_window_asks_for_each_missing_secret(
    env: Env, qtbot: QtBot, prompts: Prompts, bastion: FakeSshServer, echo: EchoServer
) -> None:
    config = tunnelled_config(env, bastion, echo)
    env.services.manager.known_hosts.trust(  # type: ignore[union-attr]
        "127.0.0.1", bastion.port, bastion.host_key
    )
    prompts.text_answers = [("db-secret", True), ("wonderland", True)]
    window = make_window(env, qtbot)
    window.activate(config.id)
    assert prompts.text_prompts == [
        "Password for tunnelled:",
        "SSH password for tunnelled:",
    ]
    qtbot.waitUntil(
        lambda: (
            env.services.manager.state_of(config.id) in (SessionState.READY, SessionState.ERROR)
        ),
        timeout=10000,
    )
    session = env.services.manager.session(config.id)
    # the SSH login worked; what failed is the SSH server reaching the (closed) database port
    assert session.tunnel is None
    assert isinstance(session.error, SshTunnelError)


def test_cancelling_a_prompt_does_not_connect(
    env: Env, qtbot: QtBot, prompts: Prompts, bastion: FakeSshServer, echo: EchoServer
) -> None:
    config = tunnelled_config(env, bastion, echo)
    prompts.text_answers = [("", False)]
    window = make_window(env, qtbot)
    window.activate(config.id)
    assert env.services.manager.state_of(config.id) is SessionState.DISCONNECTED


def test_the_error_page_offers_to_trust_the_ssh_server(
    env: Env, qtbot: QtBot, prompts: Prompts, bastion: FakeSshServer, echo: EchoServer
) -> None:
    config = tunnelled_config(env, bastion, echo)
    prompts.text_answers = [("db", True), ("wonderland", True)]
    window = make_window(env, qtbot)
    window.activate(config.id)
    wait_for_state(env, qtbot, config.id, SessionState.ERROR)
    session = env.services.manager.session(config.id)
    assert isinstance(session.error, SshHostKeyUnknown)
    window.panel.show_session(session)
    assert "not known yet" in shown_texts(window.panel)
    assert "Trust this server" in " ".join(b.text() for b in window.panel.findChildren(QPushButton))
    window._trust_host(config.id)  # the answer is "yes" by default
    qtbot.waitUntil(
        lambda: (
            not isinstance(env.services.manager.session(config.id).error, SshHostKeyUnknown)
            and env.services.manager.state_of(config.id) is not SessionState.CONNECTING
        ),
        timeout=10000,
    )
    assert env.services.paths.known_hosts_file.exists()
    assert isinstance(window, MainWindow)
