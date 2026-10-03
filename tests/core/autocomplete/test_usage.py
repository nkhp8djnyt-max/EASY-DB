"""The accepted-suggestion counters stored in ``app.db`` (migration 4)."""

from __future__ import annotations

import threading
from pathlib import Path

from easydbms.core.autocomplete import UsageStore
from easydbms.core.storage import MIGRATIONS, AppDatabase


def test_migration_four_creates_the_table(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "app.db")
    assert db.schema_version >= 4
    assert MIGRATIONS[3].version == 4
    columns = [r[1] for r in db.execute("PRAGMA table_info(completion_usage)")]
    assert columns == ["connection_id", "key", "uses", "last_used"]
    db.close()


def test_counts_start_empty_and_grow(tmp_path: Path) -> None:
    store = UsageStore(AppDatabase(tmp_path / "app.db"))
    assert dict(store.counts("c1")) == {}
    store.record("c1", "table:public.orders")
    store.record("c1", "table:public.orders")
    store.record("c1", "keyword:SELECT")
    assert dict(store.counts("c1")) == {"table:public.orders": 2, "keyword:SELECT": 1}


def test_connections_do_not_share_counts(tmp_path: Path) -> None:
    store = UsageStore(AppDatabase(tmp_path / "app.db"))
    store.record("c1", "a")
    store.record("c2", "a")
    store.record("c2", "a")
    assert dict(store.counts("c1")) == {"a": 1}
    assert dict(store.counts("c2")) == {"a": 2}


def test_counts_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    first = UsageStore(AppDatabase(path))
    for _ in range(3):
        first.record("c1", "column:public.orders.status")
    reopened = UsageStore(AppDatabase(path))
    assert dict(reopened.counts("c1")) == {"column:public.orders.status": 3}
    reopened.record("c1", "column:public.orders.status")
    assert UsageStore(AppDatabase(path)).counts("c1")["column:public.orders.status"] == 4


def test_an_empty_key_is_ignored(tmp_path: Path) -> None:
    store = UsageStore(AppDatabase(tmp_path / "app.db"))
    store.record("c1", "")
    assert dict(store.counts("c1")) == {}


def test_forget_removes_one_connection_only(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    store = UsageStore(AppDatabase(path))
    store.record("c1", "a")
    store.record("c2", "b")
    store.forget("c1")
    assert dict(store.counts("c1")) == {}
    assert dict(store.counts("c2")) == {"b": 1}
    assert dict(UsageStore(AppDatabase(path)).counts("c1")) == {}
    store.record("c1", "a")  # a forgotten connection starts again from one
    assert dict(store.counts("c1")) == {"a": 1}


def test_concurrent_recording_loses_nothing(tmp_path: Path) -> None:
    store = UsageStore(AppDatabase(tmp_path / "app.db"))

    def work() -> None:
        for _ in range(50):
            store.record("c1", "key")

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert store.counts("c1")["key"] == 200
    assert UsageStore(AppDatabase(tmp_path / "app.db")).counts("c1")["key"] == 200
