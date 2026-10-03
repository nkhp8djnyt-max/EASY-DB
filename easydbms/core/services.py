"""Wires the Qt-free services together; the UI layer and tests both build the app through this."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .autocomplete import UsageStore
from .connections import ConnectionStore, SecretStore, choose_secret_store
from .editing import EditKeyStore
from .erd import ErdLayoutStore
from .paths import AppPaths
from .queries import QueryTabStore
from .session import ConnectionManager, ManagerEvent
from .storage import AppDatabase, AppSettings, SettingsStore


@dataclass
class Services:
    paths: AppPaths
    settings_store: SettingsStore
    settings: AppSettings
    db: AppDatabase
    store: ConnectionStore
    secrets: SecretStore
    manager: ConnectionManager
    tab_store: QueryTabStore
    erd_store: ErdLayoutStore
    usage_store: UsageStore
    edit_keys: EditKeyStore

    def close(self) -> None:
        self.manager.close_all()
        self.db.close()


def build_services(
    paths: AppPaths,
    *,
    listener: Callable[[ManagerEvent], None] | None = None,
    secrets: SecretStore | None = None,
    environ: Mapping[str, str] | None = None,
) -> Services:
    """Create directories and open every store; ``secrets`` overrides the keyring/vault choice."""
    paths.ensure()
    settings_store = SettingsStore(paths.settings_file)
    store = ConnectionStore(paths.connections_file)
    chosen_secrets = secrets or choose_secret_store(paths.vault_file)
    db = AppDatabase(paths.app_db)
    return Services(
        paths=paths,
        settings_store=settings_store,
        settings=settings_store.load(),
        db=db,
        store=store,
        secrets=chosen_secrets,
        manager=ConnectionManager(
            store, chosen_secrets, listener=listener, environ=environ, load_schema=True
        ),
        tab_store=QueryTabStore(db),
        erd_store=ErdLayoutStore(db),
        usage_store=UsageStore(db),
        edit_keys=EditKeyStore(db),
    )
