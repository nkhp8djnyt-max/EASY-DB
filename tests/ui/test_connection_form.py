from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from sql_erd_studio.core.connections import (
    ConnectionColor,
    FileConnection,
    ServerConnection,
)
from sql_erd_studio.core.dialects import DialectId
from sql_erd_studio.ui.connection_form import ConnectionForm, FormError

from .conftest import Prompts


@pytest.fixture
def form(qtbot: QtBot) -> ConnectionForm:
    widget = ConnectionForm("system keyring")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def type_url(form: ConnectionForm, text: str, *, finish: bool = True) -> None:
    form.url_edit.setFocus()
    form.url_edit.setText(text)
    form.url_edit.textEdited.emit(text)
    if finish:
        form.url_edit.editingFinished.emit()


def pg(**extra: object) -> ServerConnection:
    data = {"name": "pg", "dialect": "postgresql", "host": "h"} | extra
    return ServerConnection.model_validate(data)


# ---------------------------------------------------------------------------- defaults


def test_a_blank_form_describes_a_new_postgresql_connection(form: ConnectionForm) -> None:
    assert form.dialect_id is DialectId.POSTGRESQL
    assert form.host_edit.text() == "localhost"
    assert form.port_spin.value() == 0
    assert "5432" in form.port_spin.specialValueText()
    assert form.save_password is True
    assert form.password == ""
    assert not form.read_only_check.isChecked()
    assert "system keyring" in form.save_password_check.text()
    assert form.url_edit.text() == "postgresql://localhost"


def test_a_blank_form_is_not_a_valid_connection_until_it_has_a_name(form: ConnectionForm) -> None:
    with pytest.raises(FormError, match="name"):
        form.read_config("x")
    form.name_edit.setText("Local")
    config = form.read_config("abc")
    assert isinstance(config, ServerConnection)
    assert (config.id, config.name, config.host) == ("abc", "Local", "localhost")
    assert config.port is None


# ------------------------------------------------------------------ load / read round trips


def test_server_config_round_trip(form: ConnectionForm) -> None:
    original = pg(
        name="Shop",
        port=6543,
        user="app",
        database="shop",
        options={"sslmode": "require", "application_name": "my app"},
        color="blue",
        group="work",
        read_only=True,
        production=True,
        save_password=False,
    )
    form.load(original)
    assert form.read_config(original.id) == original
    assert form.group_combo.currentText() == "work"
    assert form.color_combo.currentData() == ConnectionColor.BLUE
    assert form.url_edit.text() == (
        "postgresql://app@h:6543/shop?sslmode=require&application_name=my%20app"
    )


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_both_server_dialects_round_trip(form: ConnectionForm, dialect: str) -> None:
    original = ServerConnection.model_validate(
        {"name": "x", "dialect": dialect, "host": "db", "user": "u", "database": "d"}
    )
    form.load(original)
    assert form.dialect_id is DialectId(dialect)
    assert form.read_config(original.id) == original
    assert form.stack.currentIndex() == 0


def test_file_config_round_trip(form: ConnectionForm) -> None:
    original = FileConnection.model_validate(
        {"name": "lite", "path": "/data/x.db", "read_only": True, "create_if_missing": False}
    )
    form.load(original)
    assert form.dialect_id is DialectId.SQLITE
    assert form.stack.currentIndex() == 1
    assert not form.password_widget.isVisible()
    assert form.url_edit.text() == "sqlite:////data/x.db?mode=ro"
    assert form.read_config(original.id) == original


def test_loading_does_not_report_a_user_change(form: ConnectionForm, qtbot: QtBot) -> None:
    emitted: list[int] = []
    form.changed.connect(lambda: emitted.append(1))
    form.load(pg(name="a", user="u"))
    form.load(None)
    assert emitted == []


def test_a_saved_password_is_signalled_in_the_placeholder(form: ConnectionForm) -> None:
    form.load(pg(), has_saved_password=True)
    assert "saved password" in form.password_edit.placeholderText()
    form.load(pg(), has_saved_password=False)
    assert form.password_edit.placeholderText() == ""


# ---------------------------------------------------------------------------- URL -> fields


def test_pasting_a_url_fills_the_fields_and_switches_the_dialect(form: ConnectionForm) -> None:
    type_url(form, "mysql://app:s3cret@db.example.com:3307/shop?connect_timeout=5")
    assert form.dialect_id is DialectId.MYSQL
    assert form.host_edit.text() == "db.example.com"
    assert form.port_spin.value() == 3307
    assert form.user_edit.text() == "app"
    assert form.database_edit.text() == "shop"
    assert form.params_edit.text() == "connect_timeout=5"
    assert form.password == "s3cret"
    assert form.name_edit.text() == "app@shop"  # a blank name is filled in, never overwritten
    assert not form.url_error.isVisible()


def test_the_canonical_url_drops_the_password_after_editing_finishes(form: ConnectionForm) -> None:
    type_url(form, "postgres://app:s3cret@h/d")
    assert form.url_edit.text() == "postgresql://app@h/d"
    assert form.password == "s3cret"


def test_an_existing_name_is_not_overwritten_by_a_pasted_url(form: ConnectionForm) -> None:
    form.name_edit.setText("My name")
    type_url(form, "postgresql://u@h/d")
    assert form.name_edit.text() == "My name"


def test_pasting_a_sqlite_url_switches_to_the_file_page(form: ConnectionForm) -> None:
    type_url(form, "sqlite:////tmp/x.db?mode=ro")
    assert form.dialect_id is DialectId.SQLITE
    assert form.path_edit.text() == "/tmp/x.db"
    assert form.read_only_check.isChecked()
    assert form.stack.currentIndex() == 1
    assert not form.password_widget.isVisible()


def test_an_invalid_url_shows_an_error_and_leaves_the_fields_alone(form: ConnectionForm) -> None:
    form.load(pg(name="a", host="keep-me"))
    type_url(form, "mssql://sa@h/d")
    assert form.url_error.isVisible()
    assert "unsupported" in form.url_error.text()
    assert form.host_edit.text() == "keep-me"
    assert form.url_edit.property("invalid") is True
    type_url(form, "postgresql://u@fixed/d")  # fixing it clears the error
    assert not form.url_error.isVisible()
    assert form.url_edit.property("invalid") is False
    assert form.host_edit.text() == "fixed"


def test_no_error_is_shown_while_the_url_is_still_being_typed(form: ConnectionForm) -> None:
    type_url(form, "postgres", finish=False)
    assert not form.url_error.isVisible()


def test_an_empty_url_clears_a_previous_error(form: ConnectionForm) -> None:
    type_url(form, "nonsense")
    assert form.url_error.isVisible()
    type_url(form, "")
    assert not form.url_error.isVisible()


# ---------------------------------------------------------------------------- fields -> URL


def test_editing_fields_rewrites_the_url(form: ConnectionForm) -> None:
    form.host_edit.setText("db.internal")
    form.host_edit.textEdited.emit("db.internal")
    form.user_edit.setText("me")
    form.user_edit.textEdited.emit("me")
    form.database_edit.setText("sales")
    form.database_edit.textEdited.emit("sales")
    form.port_spin.setValue(6000)
    assert form.url_edit.text() == "postgresql://me@db.internal:6000/sales"
    form.params_edit.setText("sslmode=require")
    form.params_edit.textEdited.emit("sslmode=require")
    assert form.url_edit.text().endswith("?sslmode=require")


def test_switching_the_dialect_updates_the_url_ui_and_port_hint(
    form: ConnectionForm, qtbot: QtBot
) -> None:
    emitted: list[int] = []
    form.changed.connect(lambda: emitted.append(1))
    form.dialect_buttons[DialectId.MYSQL].click()
    assert form.url_edit.text().startswith("mysql://")
    assert "3306" in form.port_spin.specialValueText()
    form.dialect_buttons[DialectId.SQLITE].click()
    assert form.stack.currentIndex() == 1
    assert form.tabs.tabText(1) == "File"
    assert not form.password_widget.isVisible()
    form.dialect_buttons[DialectId.POSTGRESQL].click()
    assert form.stack.currentIndex() == 0
    assert form.tabs.tabText(1) == "Host / Port"
    assert form.password_widget.isVisible()
    assert len(emitted) >= 3


def test_user_edits_emit_changed(form: ConnectionForm) -> None:
    emitted: list[int] = []
    form.changed.connect(lambda: emitted.append(1))
    form.name_edit.textEdited.emit("x")
    form.password_edit.textEdited.emit("pw")
    form.read_only_check.setChecked(True)
    form.production_check.setChecked(True)
    form.color_combo.setCurrentIndex(2)
    assert len(emitted) == 5


# ---------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda f: f.name_edit.setText(""), "name"),
        (lambda f: f.host_edit.setText("  "), "host"),
    ],
)
def test_server_validation_messages(form: ConnectionForm, setup: object, message: str) -> None:
    form.load(pg(name="ok"))
    setup(form)  # type: ignore[operator]
    with pytest.raises(FormError, match=message):
        form.read_config("x")


def test_file_validation_message(form: ConnectionForm) -> None:
    form.load(FileConnection.model_validate({"name": "f", "path": "x.db"}))
    form.path_edit.setText("")
    with pytest.raises(FormError, match="file"):
        form.read_config("x")


def test_malformed_extra_parameters_do_not_crash(form: ConnectionForm) -> None:
    form.load(pg(name="ok"))
    form.params_edit.setText("novalue&a=1&&b=")
    config = form.read_config("x")
    assert isinstance(config, ServerConnection)
    assert config.options == {"novalue": "", "a": "1", "b": ""}


# ---------------------------------------------------------------------------- file pickers


def test_browse_and_new_file_buttons(form: ConnectionForm, prompts: Prompts) -> None:
    form.load(FileConnection.model_validate({"name": "f", "path": "old.db"}))
    prompts.open_file = "/chosen/existing.db"
    form.browse_button.click()
    assert form.path_edit.text() == "/chosen/existing.db"
    assert not form.create_check.isChecked()

    prompts.save_file = "/chosen/new.db"
    form.new_file_button.click()
    assert form.path_edit.text() == "/chosen/new.db"
    assert form.create_check.isChecked()
    config = form.read_config("x")
    assert isinstance(config, FileConnection)
    assert config.create_if_missing

    prompts.open_file = ""  # the user cancelled the picker
    form.browse_button.click()
    assert form.path_edit.text() == "/chosen/new.db"


def test_groups_are_offered_in_the_combo(form: ConnectionForm) -> None:
    form.set_groups(["home", "work"])
    assert [form.group_combo.itemText(i) for i in range(form.group_combo.count())] == [
        "home",
        "work",
    ]
