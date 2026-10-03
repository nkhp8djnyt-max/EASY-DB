"""A cloud provider's token becomes the password: connecting, testing, the meta lane, secrets."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

import easydbms.core.connections.providers as providers
from easydbms.core.connections import (
    PASSWORD,
    ConnectionStore,
    MemorySecretStore,
    ProviderKind,
    ServerConnection,
)
from easydbms.core.connections.providers import Provider, ProviderAuthError, Token
from easydbms.core.session import (
    ConnectionManager,
    Session,
    SessionState,
    check_connection,
    prepare,
)

from .conftest import ScriptedClient, server_config, state_of


class FakeCloud(Provider):
    kind = ProviderKind.AWS_RDS
    title = "Fake cloud"
    note = ""
    uses_token = True

    def __init__(self, lifetime: float = 900.0, fail: bool = False) -> None:
        self.lifetime = lifetime
        self.fail = fail
        self.issued: list[str] = []
        self.seen_users: list[str] = []

    def token(
        self, config: ServerConnection, params: Mapping[str, str], environ: Mapping[str, str]
    ) -> Token:
        if self.fail:
            raise ProviderAuthError("the cloud said no")
        self.seen_users.append(config.user)
        value = f"token-{len(self.issued) + 1}"
        self.issued.append(value)
        return Token(value, time.time() + self.lifetime)

    def describe(self, token: Token | None) -> str:
        return "a fake token"


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setitem(providers._BY_KIND, ProviderKind.AWS_RDS, fake)
    return fake


def cloud_config(**extra: Any) -> ServerConnection:
    return server_config(provider={"kind": "aws-rds", "params": {"region": "eu-west-1"}}, **extra)


def test_the_token_replaces_the_password_and_is_reported_as_a_step(cloud: FakeCloud) -> None:
    prepared = prepare(cloud_config(), "typed-and-ignored", {}, {}, None)
    assert prepared.password == "token-1"
    assert [(s.name, s.ok) for s in prepared.steps] == [("Cloud", True)]
    assert "Fake cloud — a fake token" in prepared.steps[0].detail
    assert cloud.seen_users == ["u"]


def test_a_failing_provider_is_a_failed_cloud_step(cloud: FakeCloud) -> None:
    cloud.fail = True
    check = check_connection(cloud_config(), None, {}, {}, None, ScriptedClient)
    assert isinstance(check.failure, ProviderAuthError)
    assert [(s.name, s.ok) for s in check.steps] == [("Cloud", False)]
    assert "cloud said no" in check.steps[0].detail


def test_the_connection_test_goes_on_after_the_cloud_step(cloud: FakeCloud) -> None:
    check = check_connection(cloud_config(), None, {}, {}, None, ScriptedClient)
    assert check.ok, check.failure
    assert next(s.name for s in check.steps) == "Cloud"
    assert ScriptedClient.created[0].password == "token-1"


def test_template_providers_add_no_step(monkeypatch: pytest.MonkeyPatch) -> None:
    config = server_config(provider={"kind": "supabase", "params": {"project_ref": "abc"}})
    prepared = prepare(config, "pw", {}, {}, None)
    assert prepared.steps == []
    assert prepared.password == "pw"
    assert prepared.token is None


def test_the_meta_lane_gets_a_fresh_token_once_the_first_one_ran_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeCloud(lifetime=1.0)  # inside the renewal margin right away
    monkeypatch.setitem(providers._BY_KIND, ProviderKind.AWS_RDS, fake)
    session = Session(cloud_config(), client_factory=ScriptedClient)
    session.connect()
    assert state_of(session) is SessionState.READY
    session.run_on_meta(lambda client: client.execute("SELECT 1")).result(timeout=5)
    assert [c.password for c in ScriptedClient.created] == ["token-1", "token-2"]
    session.disconnect()


def test_a_token_that_is_still_good_is_reused(cloud: FakeCloud) -> None:
    session = Session(cloud_config(), client_factory=ScriptedClient)
    session.connect()
    session.run_on_meta(lambda client: client.execute("SELECT 1")).result(timeout=5)
    assert [c.password for c in ScriptedClient.created] == ["token-1", "token-1"]
    assert cloud.issued == ["token-1"]
    session.disconnect()


def test_a_session_fails_cleanly_when_the_cloud_refuses(cloud: FakeCloud) -> None:
    cloud.fail = True
    session = Session(cloud_config(), client_factory=ScriptedClient)
    session.connect()
    assert state_of(session) is SessionState.ERROR
    assert isinstance(session.error, ProviderAuthError)
    assert ScriptedClient.created == []


@pytest.fixture
def library(tmp_path: Path) -> Iterator[tuple[ConnectionManager, ConnectionStore]]:
    store = ConnectionStore(tmp_path / "c.json")
    secrets = MemorySecretStore()
    manager = ConnectionManager(
        store,
        secrets,
        environ={"PGPASSFILE": str(tmp_path / "none")},
        client_factory=ScriptedClient,
    )
    yield manager, store
    manager.close_all()


def test_a_token_provider_never_asks_for_a_password(
    library: tuple[ConnectionManager, ConnectionStore], cloud: FakeCloud
) -> None:
    manager, store = library
    plain = server_config(save_password=False)
    cloudy = cloud_config(save_password=False)
    templated = server_config(
        save_password=False, provider={"kind": "neon", "params": {}}, name="neon"
    )
    for config in (plain, cloudy, templated):
        store.save(config)
    assert PASSWORD in manager.missing_secrets(plain.id)
    assert manager.missing_secrets(cloudy.id) == []
    assert PASSWORD in manager.missing_secrets(templated.id)  # a template still has a password
    session = manager.activate(cloudy.id).result(timeout=10)
    assert session.state is SessionState.READY
    assert ScriptedClient.created[0].password == "token-1"
