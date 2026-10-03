from __future__ import annotations

import sqlite3
from pathlib import Path

from sql_erd_studio.core.connections import FileConnection, MemorySecretStore
from sql_erd_studio.core.paths import AppPaths
from sql_erd_studio.core.services import build_services
from sql_erd_studio.core.session import ActiveChanged, SessionStateChanged


def test_everything_is_created_under_the_given_paths(tmp_path: Path) -> None:
    paths = AppPaths.under(tmp_path / "app")
    services = build_services(paths, secrets=MemorySecretStore())
    try:
        assert paths.config_dir.is_dir()
        assert paths.app_db.exists()
        assert services.store.all() == []
        assert services.settings.theme == "dark"
        assert services.db.schema_version >= 1
    finally:
        services.close()


def test_the_default_secret_store_is_chosen_when_none_is_given(tmp_path: Path) -> None:
    services = build_services(AppPaths.under(tmp_path))
    try:
        assert services.secrets.name in {"system keyring", "encrypted vault"}
    finally:
        services.close()


def test_the_listener_receives_manager_events_and_state_survives_a_restart(tmp_path: Path) -> None:
    paths = AppPaths.under(tmp_path)
    database = tmp_path / "x.db"
    sqlite3.connect(database).close()
    events: list[object] = []
    first = build_services(paths, listener=events.append, secrets=MemorySecretStore())
    config = FileConnection.model_validate({"name": "x", "path": str(database)})
    first.store.save(config)
    first.db.set_state("answer", 42)
    first.manager.activate(config.id).result(timeout=5)
    first.close()

    assert isinstance(events[0], ActiveChanged)
    assert [e.state.value for e in events if isinstance(e, SessionStateChanged)][:2] == [
        "connecting",
        "ready",
    ]

    second = build_services(paths, secrets=MemorySecretStore())
    try:
        assert [c.name for c in second.store.all()] == ["x"]
        assert second.db.get_state("answer") == 42
    finally:
        second.close()
