"""The "Cloud" tab: choosing a hosted service fills the form; token services hide the password."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from easydbms.core.connections import ProviderKind, ServerConnection, SslMode
from easydbms.core.connections.providers import (
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
)
from easydbms.core.dialects import DialectId
from easydbms.ui.connection_form import ConnectionForm, FormError
from easydbms.ui.failure_hints import hint_for

from .conftest import Env
from .test_connection_dialog import make_dialog


@pytest.fixture
def form(qtbot: QtBot) -> ConnectionForm:
    widget = ConnectionForm("the keyring")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def choose(form: ConnectionForm, kind: ProviderKind | None) -> None:
    combo = form.cloud_editor.combo
    combo.setCurrentIndex(combo.findData(kind.value if kind else ""))
    form.cloud_editor._on_activated(combo.currentIndex())  # what a click on the item does


def fill(form: ConnectionForm, kind: ProviderKind, **params: str) -> None:
    choose(form, kind)
    for name, value in params.items():
        edit = form.cloud_editor._inputs[name]
        edit.setText(value)
        edit.textEdited.emit(value)


def test_the_tab_is_there_for_servers_only(form: ConnectionForm) -> None:
    assert form.tabs.tabText(4) == "Cloud"
    assert form.tabs.isTabVisible(4)
    form.dialect_buttons[DialectId.SQLITE].setChecked(True)
    assert not form.tabs.isTabVisible(4)


def test_no_service_is_the_default_and_saves_as_none(form: ConnectionForm) -> None:
    form.load(None)
    form.name_edit.setText("x")
    assert form.cloud_editor.kind is None
    assert form.read_config("x").provider is None  # type: ignore[union-attr]
    assert not form.password_widget.isHidden()


def test_the_list_offers_only_services_of_the_chosen_dialect(form: ConnectionForm) -> None:
    def offered() -> list[str]:
        combo = form.cloud_editor.combo
        return [str(combo.itemData(i)) for i in range(combo.count())]

    assert "supabase" in offered()
    assert "planetscale" not in offered()
    form.dialect_buttons[DialectId.MYSQL].setChecked(True)
    assert "planetscale" in offered()
    assert "supabase" not in offered()
    assert "aws-rds" in offered()


def test_supabase_fills_host_user_database_port_and_tls(form: ConnectionForm) -> None:
    form.load(None)
    fill(form, ProviderKind.SUPABASE, project_ref="abcd1234")
    assert form.host_edit.text() == "db.abcd1234.supabase.co"
    assert (form.user_edit.text(), form.database_edit.text()) == ("postgres", "postgres")
    assert form.port_spin.value() == 5432
    assert form.ssl_editor.mode is SslMode.REQUIRE
    assert form.dialect_id is DialectId.POSTGRESQL
    assert "db.abcd1234.supabase.co" in form.url_edit.text()
    assert "sslmode=require" in form.url_edit.text()


def test_typing_the_project_reference_updates_the_host_it_generated(form: ConnectionForm) -> None:
    form.load(None)
    fill(form, ProviderKind.SUPABASE, project_ref="first")
    assert form.host_edit.text() == "db.first.supabase.co"
    edit = form.cloud_editor._inputs["project_ref"]
    edit.setText("second")
    edit.textEdited.emit("second")
    assert form.host_edit.text() == "db.second.supabase.co"
    form.host_edit.setText("mine.example.com")  # a host typed by hand is never overwritten
    form.host_edit.textEdited.emit("mine.example.com")
    edit.setText("third")
    edit.textEdited.emit("third")
    assert form.host_edit.text() == "mine.example.com"


def test_choosing_a_service_never_overwrites_what_was_typed(form: ConnectionForm) -> None:
    form.load(None)
    form.host_edit.setText("my.host")
    form.host_edit.textEdited.emit("my.host")
    form.user_edit.setText("me")
    form.user_edit.textEdited.emit("me")
    form.database_edit.setText("mine")
    form.database_edit.textEdited.emit("mine")
    fill(form, ProviderKind.SUPABASE, project_ref="abc")
    assert (form.host_edit.text(), form.user_edit.text(), form.database_edit.text()) == (
        "my.host",
        "me",
        "mine",
    )
    assert form.port_spin.value() == 5432  # an empty field is filled


def test_planetscale_switches_to_mysql_and_verifies_the_certificate(form: ConnectionForm) -> None:
    form.load(None)
    form.dialect_buttons[DialectId.MYSQL].setChecked(True)
    fill(form, ProviderKind.PLANETSCALE, host="aws.connect.psdb.cloud")
    assert form.dialect_id is DialectId.MYSQL
    assert form.host_edit.text() == "aws.connect.psdb.cloud"
    assert form.port_spin.value() == 3306
    assert form.ssl_editor.mode is SslMode.VERIFY_FULL


def test_cockroach_puts_the_cluster_in_the_database_name(form: ConnectionForm) -> None:
    form.load(None)
    fill(form, ProviderKind.COCKROACH, cluster="my-cluster-12", host="x.cockroachlabs.cloud")
    assert form.port_spin.value() == 26257
    assert form.database_edit.text() == "my-cluster-12.defaultdb"


def test_a_token_service_hides_the_password_and_keeps_its_settings(form: ConnectionForm) -> None:
    form.load(None)
    form.name_edit.setText("Prod")
    form.host_edit.setText("db.abc.eu-west-1.rds.amazonaws.com")
    form.user_edit.setText("iam_user")
    fill(form, ProviderKind.AWS_RDS, region="eu-west-1", profile="prod")
    assert form.password_widget.isHidden()
    assert form.cloud_editor.uses_token
    config = form.read_config("id1")
    assert isinstance(config, ServerConnection)
    assert config.provider is not None
    assert (config.provider.kind, config.provider.params) == (
        ProviderKind.AWS_RDS,
        {"region": "eu-west-1", "profile": "prod"},
    )
    assert form.ssl_editor.mode is SslMode.REQUIRE
    choose(form, None)
    assert not form.password_widget.isHidden()
    assert form.read_config("id1").provider is None  # type: ignore[union-attr]


def test_a_saved_provider_comes_back_with_its_fields(form: ConnectionForm) -> None:
    original = ServerConnection.model_validate(
        {
            "name": "Azure",
            "dialect": "postgresql",
            "host": "x.postgres.database.azure.com",
            "user": "me@contoso.onmicrosoft.com",
            "provider": {"kind": "azure", "params": {"client_id": "1234"}},
        }
    )
    form.load(original)
    assert form.cloud_editor.kind is ProviderKind.AZURE
    assert form.cloud_editor._inputs["client_id"].text() == "1234"
    assert form.password_widget.isHidden()
    assert form.read_config(original.id) == original


def test_switching_to_a_dialect_the_service_does_not_support_drops_it(
    form: ConnectionForm,
) -> None:
    form.load(None)
    fill(form, ProviderKind.SUPABASE, project_ref="abc")
    form.dialect_buttons[DialectId.MYSQL].setChecked(True)
    assert form.cloud_editor.kind is None
    form.name_edit.setText("n")
    assert form.read_config("x").provider is None  # type: ignore[union-attr]


def test_a_service_needs_a_name_like_any_connection(form: ConnectionForm) -> None:
    form.load(None)
    fill(form, ProviderKind.NEON, endpoint="ep-x.neon.tech")
    with pytest.raises(FormError, match="name"):
        form.read_config("x")


def test_the_hints_for_provider_failures() -> None:
    assert "pip install 'easydbms[aws]'" in hint_for(ProviderUnavailable("boto3", "aws"))
    assert "Sign in" in hint_for(ProviderAuthError("x"))
    assert "Cloud tab" in hint_for(ProviderError("x"))


def test_the_report_shows_a_failed_cloud_step_with_advice(
    env: Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    import easydbms.core.connections.providers as providers
    from easydbms.core.connections.providers import Provider, Token

    class Refusing(Provider):
        kind = ProviderKind.AWS_RDS
        title = "Refusing cloud"
        note = ""
        uses_token = True

        def token(self, config: object, params: object, environ: object) -> Token:
            raise ProviderAuthError("the account is not signed in")

        def describe(self, token: Token | None) -> str:
            return ""

    monkeypatch.setitem(providers._BY_KIND, ProviderKind.AWS_RDS, Refusing())
    config = env.add_server(
        "aws", host="db.abc.eu-west-1.rds.amazonaws.com", provider={"kind": "aws-rds", "params": {}}
    )
    dialog = make_dialog(env, qtbot, select_id=config.id)
    dialog.test_button.click()
    qtbot.waitUntil(lambda: dialog.test_button.isEnabled(), timeout=10000)
    texts = " | ".join(label.text() for label in dialog.report.findChildren(QLabel))
    assert "Cloud token" in texts
    assert "the account is not signed in" in texts
    assert "Sign in to the cloud account" in texts
    dialog._dirty = False
