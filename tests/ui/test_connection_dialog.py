from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from easydbms.core.connections import (
    ServerConnection,
    VaultSecretStore,
    parse_connection_url,
)
from easydbms.core.session import SessionState
from easydbms.ui.connection_dialog import ConnectionDialog

from .conftest import Env, Prompts


def make_dialog(env: Env, qtbot: QtBot, select_id: str | None = None) -> ConnectionDialog:
    dialog = ConnectionDialog(
        env.services.store,
        env.services.secrets,
        env.services.manager,
        env.runner,
        select_id=select_id,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def names(dialog: ConnectionDialog) -> list[str]:
    return [
        dialog.list.item(i).text()
        for i in range(dialog.list.count())
        if dialog.list.item(i).data(
            0x0100  # Qt.UserRole: only real connections carry an id
        )
    ]


def fill_new_postgres(dialog: ConnectionDialog, name: str = "Shop", password: str = "pw") -> None:
    dialog.new_button.click()
    dialog.form.name_edit.setText(name)
    dialog.form.host_edit.setText("db.internal")
    dialog.form.host_edit.textEdited.emit("db.internal")
    dialog.form.user_edit.setText("app")
    dialog.form.user_edit.textEdited.emit("app")
    dialog.form.password_edit.setText(password)
    dialog.form.password_edit.textEdited.emit(password)


# ---------------------------------------------------------------------------- list


def test_the_list_shows_connections_grouped_and_sorted(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("zeta")
    env.add_sqlite("alpha")
    env.add_sqlite("beta", group="work")
    dialog = make_dialog(env, qtbot)
    assert names(dialog) == ["beta", "alpha", "zeta"]  # named groups first, each group sorted
    headers = [
        dialog.list.item(i).text()
        for i in range(dialog.list.count())
        if dialog.list.item(i).data(0x0103)
    ]
    assert headers == ["work", "No group"]


def test_no_group_headers_when_nothing_is_grouped(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("a")
    env.add_sqlite("b")
    dialog = make_dialog(env, qtbot)
    assert dialog.list.count() == 2


def test_the_requested_connection_is_selected_and_loaded(env: Env, qtbot: QtBot) -> None:
    env.add_sqlite("one")
    target = env.add_sqlite("two")
    dialog = make_dialog(env, qtbot, select_id=target.id)
    assert dialog.form.name_edit.text() == "two"
    assert dialog.form.path_edit.text() == target.path
    assert dialog.duplicate_button.isEnabled()
    assert dialog.delete_button.isEnabled()


def test_an_empty_store_opens_a_blank_form(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    assert dialog.list.count() == 0
    assert dialog.form.name_edit.text() == ""
    assert not dialog.duplicate_button.isEnabled()
    assert not dialog.delete_button.isEnabled()


# ---------------------------------------------------------------------------- save


def test_saving_a_new_connection_stores_the_config_and_the_password(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    changed: list[int] = []
    dialog.connectionsChanged.connect(lambda: changed.append(1))
    fill_new_postgres(dialog)
    dialog.save_button.click()

    (saved,) = env.services.store.all()
    assert isinstance(saved, ServerConnection)
    assert (saved.name, saved.host, saved.user) == ("Shop", "db.internal", "app")
    assert env.secrets.get(saved.id) == "pw"
    assert "pw" not in (env.tmp_path / "config" / "connections.json").read_text()
    assert names(dialog) == ["Shop"]
    assert dialog.message.isVisible()
    assert changed
    assert dialog.form.password == ""  # the typed password is cleared after saving
    assert "saved password" in dialog.form.password_edit.placeholderText()


def test_validation_errors_are_shown_and_nothing_is_saved(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    dialog.save_button.click()  # blank name
    assert dialog.message.isVisible()
    assert "name" in dialog.message.text()
    assert env.services.store.all() == []


def test_editing_an_existing_connection_keeps_its_id(env: Env, qtbot: QtBot) -> None:
    original = env.add_sqlite("old")
    dialog = make_dialog(env, qtbot, select_id=original.id)
    dialog.form.name_edit.setText("renamed")
    dialog.form.name_edit.textEdited.emit("renamed")
    dialog.save_button.click()
    (saved,) = env.services.store.all()
    assert (saved.id, saved.name) == (original.id, "renamed")


def test_an_empty_password_field_keeps_the_stored_password(env: Env, qtbot: QtBot) -> None:
    config = env.add_server("srv")
    env.secrets.set(config.id, "kept")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.form.user_edit.setText("someone")
    dialog.form.user_edit.textEdited.emit("someone")
    dialog.save_button.click()
    assert env.secrets.get(config.id) == "kept"
    assert env.services.store.get(config.id).model_dump()["user"] == "someone"


def test_a_typed_password_replaces_the_stored_one(env: Env, qtbot: QtBot) -> None:
    config = env.add_server("srv")
    env.secrets.set(config.id, "old")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.form.password_edit.setText("new")
    dialog.form.password_edit.textEdited.emit("new")
    dialog.save_button.click()
    assert env.secrets.get(config.id) == "new"


def test_turning_off_save_password_removes_the_stored_secret(env: Env, qtbot: QtBot) -> None:
    config = env.add_server("srv")
    env.secrets.set(config.id, "old")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.form.save_password_check.setChecked(False)
    dialog.save_button.click()
    assert env.secrets.get(config.id) is None
    saved = env.services.store.get(config.id)
    assert isinstance(saved, ServerConnection)
    assert saved.save_password is False


def test_saving_a_changed_connection_drops_its_open_session(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("live")
    env.services.manager.activate(config.id).result(timeout=5)
    old_session = env.services.manager.session(config.id)
    assert env.services.manager.state_of(config.id) is SessionState.READY
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.form.read_only_check.setChecked(True)
    dialog.save_button.click()
    assert old_session.client is None  # closed; reconnecting picks up the new settings
    assert env.services.manager.session(config.id) is not old_session


def test_saving_without_changes_keeps_the_session(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("live")
    env.services.manager.activate(config.id).result(timeout=5)
    session = env.services.manager.session(config.id)
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.save_button.click()
    assert session.state is SessionState.READY


# ---------------------------------------------------------------------------- dirty handling


def test_switching_with_unsaved_changes_asks_and_can_cancel(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    a, b = env.add_sqlite("a"), env.add_sqlite("b")
    dialog = make_dialog(env, qtbot, select_id=a.id)
    dialog.form.name_edit.setText("a-edited")
    dialog.form.name_edit.textEdited.emit("a-edited")
    prompts.question_answer = QMessageBox.StandardButton.Cancel
    dialog.list.setCurrentRow(dialog._row_of(b.id))
    assert len(prompts.questions) == 1
    assert dialog.form.name_edit.text() == "a-edited"  # stayed
    assert dialog.list.currentItem().data(0x0100) == a.id


def test_switching_can_discard_or_save_the_changes(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    a, b = env.add_sqlite("a"), env.add_sqlite("b")
    dialog = make_dialog(env, qtbot, select_id=a.id)
    dialog.form.name_edit.setText("discard-me")
    dialog.form.name_edit.textEdited.emit("discard-me")
    prompts.question_answer = QMessageBox.StandardButton.Discard
    dialog.list.setCurrentRow(dialog._row_of(b.id))
    assert dialog.form.name_edit.text() == "b"
    assert env.services.store.get(a.id).name == "a"

    dialog.form.name_edit.setText("save-me")
    dialog.form.name_edit.textEdited.emit("save-me")
    prompts.question_answer = QMessageBox.StandardButton.Save
    dialog.list.setCurrentRow(dialog._row_of(a.id))
    assert env.services.store.get(b.id).name == "save-me"
    assert dialog.form.name_edit.text() == "a"


def test_no_question_when_nothing_changed(env: Env, qtbot: QtBot, prompts: Prompts) -> None:
    a, b = env.add_sqlite("a"), env.add_sqlite("b")
    dialog = make_dialog(env, qtbot, select_id=a.id)
    dialog.list.setCurrentRow(dialog._row_of(b.id))
    assert prompts.questions == []
    assert dialog.form.name_edit.text() == "b"


# ---------------------------------------------------------------------------- duplicate / delete


def test_duplicate_creates_a_copy_without_the_password(env: Env, qtbot: QtBot) -> None:
    config = env.add_server("prod")
    env.secrets.set(config.id, "secret")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.duplicate_button.click()
    assert sorted(c.name for c in env.services.store.all()) == ["prod", "prod (copy)"]
    assert dialog.form.name_edit.text() == "prod (copy)"
    copy = next(c for c in env.services.store.all() if c.name == "prod (copy)")
    assert env.secrets.get(copy.id) is None


def test_delete_removes_the_connection_session_and_secret(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    keep = env.add_sqlite("keep")
    doomed = env.add_sqlite("doomed")
    server = env.add_server("srv")
    env.secrets.set(server.id, "pw")
    env.services.manager.activate(doomed.id).result(timeout=5)
    dialog = make_dialog(env, qtbot, select_id=doomed.id)
    dialog.delete_button.click()
    assert doomed.id not in {c.id for c in env.services.store.all()}
    assert env.services.manager.active_id is None
    assert "doomed" in prompts.questions[0]

    dialog.list.setCurrentRow(dialog._row_of(server.id))
    prompts.question_answer = QMessageBox.StandardButton.Yes
    dialog.delete_button.click()
    assert env.secrets.get(server.id) is None
    assert [c.id for c in env.services.store.all()] == [keep.id]


def test_declining_the_delete_confirmation_keeps_everything(
    env: Env, qtbot: QtBot, prompts: Prompts
) -> None:
    config = env.add_sqlite("stay")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    prompts.question_answer = QMessageBox.StandardButton.No
    dialog.delete_button.click()
    assert len(env.services.store.all()) == 1


# ---------------------------------------------------------------------------- test button


def test_testing_a_sqlite_file_shows_every_step(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("lite")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.test_button.click()
    assert not dialog.test_button.isEnabled()  # busy while the worker runs
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    text = " ".join(
        label.text() for label in dialog.report.findChildren(type(dialog.message)) if label.text()
    )
    assert "Database file" in text
    assert "Open file" in text
    assert "Test query" in text
    assert "Connection works." in text
    assert dialog.report.isVisible()


def test_testing_a_missing_file_explains_what_to_check(env: Env, qtbot: QtBot) -> None:
    config = env.add_sqlite("gone")
    Path(config.path).unlink()
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    text = " ".join(label.text() for label in dialog.report.findChildren(type(dialog.message)))
    assert "does not exist" in text
    assert "Check the path of the database file." in text


def test_testing_with_an_undefined_environment_variable_reports_it(
    env: Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ERD_SURELY_UNDEFINED", raising=False)
    config = env.add_sqlite("envy", path="${ERD_SURELY_UNDEFINED}/x.db")
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    text = " ".join(label.text() for label in dialog.report.findChildren(type(dialog.message)))
    assert "ERD_SURELY_UNDEFINED" in text
    assert "environment variables" in text


def test_test_with_invalid_input_does_not_start_a_worker(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    dialog.test_button.click()
    assert dialog.test_button.isEnabled()
    assert not dialog.report.isVisible()
    assert dialog.message.isVisible()


# ---------------------------------------------------------------------------- connect


def test_connect_saves_and_requests_the_connection(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    fill_new_postgres(dialog)
    with qtbot.waitSignal(dialog.connectRequested) as signal:
        dialog.connect_button.click()
    connection_id, typed = signal.args
    assert env.services.store.get(connection_id).name == "Shop"
    assert typed is None  # saved passwords are read from the store, not passed around
    assert not dialog.isVisible()  # the dialog closes


def test_connect_passes_a_password_that_is_not_saved(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    fill_new_postgres(dialog, password="only-this-run")
    dialog.form.save_password_check.setChecked(False)
    with qtbot.waitSignal(dialog.connectRequested) as signal:
        dialog.connect_button.click()
    connection_id, typed = signal.args
    assert typed == "only-this-run"
    assert env.secrets.get(connection_id) is None


def test_connect_with_invalid_input_stays_open(env: Env, qtbot: QtBot) -> None:
    dialog = make_dialog(env, qtbot)
    dialog.connect_button.click()
    assert dialog.isVisible()
    assert dialog.message.isVisible()


# ---------------------------------------------------------------------------- vault


def test_a_locked_vault_is_unlocked_before_saving_a_password(
    env: Env, qtbot: QtBot, prompts: Prompts, tmp_path: Path
) -> None:
    vault = VaultSecretStore(tmp_path / "vault.json", scrypt_n=2**10)
    dialog = ConnectionDialog(env.services.store, vault, env.services.manager, env.runner)
    qtbot.addWidget(dialog)
    dialog.show()
    fill_new_postgres(dialog)
    prompts.text_answers = [("master-pw", True), ("master-pw", True)]  # create + repeat
    dialog.save_button.click()
    assert not vault.locked
    (saved,) = env.services.store.all()
    assert vault.get(saved.id) == "pw"
    assert "No system keyring" in prompts.text_prompts[0]


def test_declining_to_unlock_aborts_the_save_with_a_message(
    env: Env, qtbot: QtBot, prompts: Prompts, tmp_path: Path
) -> None:
    vault = VaultSecretStore(tmp_path / "vault.json", scrypt_n=2**10)
    dialog = ConnectionDialog(env.services.store, vault, env.services.manager, env.runner)
    qtbot.addWidget(dialog)
    dialog.show()
    fill_new_postgres(dialog)
    prompts.text_answers = [("", False)]
    dialog.save_button.click()
    assert vault.locked
    assert env.services.store.all() == []
    assert "locked" in dialog.message.text()


# ---------------------------------------------------------------------------- live servers


@pytest.mark.integration
def test_testing_a_real_server_end_to_end(env: Env, qtbot: QtBot) -> None:
    url = os.environ.get("EASYDBMS_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set EASYDBMS_TEST_POSTGRES_URL to run against PostgreSQL")
    parsed = parse_connection_url(url, name="live")
    env.services.store.save(parsed.config)
    env.secrets.set(parsed.config.id, parsed.password or "")
    dialog = make_dialog(env, qtbot, select_id=parsed.config.id)
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=15000)
    text = " ".join(label.text() for label in dialog.report.findChildren(type(dialog.message)))
    assert "Connection works." in text
    assert "Sign in" in text
