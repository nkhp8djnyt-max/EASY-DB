from __future__ import annotations

from pathlib import Path

import pytest

from easydbms.core.queries import QueryTabStore, TabState
from easydbms.core.storage import AppDatabase


@pytest.fixture
def store(tmp_path: Path) -> QueryTabStore:
    return QueryTabStore(AppDatabase(tmp_path / "app.db"))


def test_nothing_saved_yet(store: QueryTabStore) -> None:
    assert store.load("c1") == []
    assert store.active_index("c1") == 0


def test_tabs_round_trip_in_order(store: QueryTabStore) -> None:
    tabs = [
        TabState("Query 1", "select 1;\nselect 'é ✓';", "postgresql"),
        TabState("Report", "", "mysql"),
        TabState("Query 3", "-- note", "sqlite"),
    ]
    store.save("c1", tabs)
    assert store.load("c1") == tabs


def test_saving_replaces_and_connections_are_independent(store: QueryTabStore) -> None:
    store.save("a", [TabState("A1", "x", "sqlite"), TabState("A2", "y", "sqlite")])
    store.save("b", [TabState("B1", "z", "mysql")])
    store.save("a", [TabState("only", "new", "sqlite")])
    assert [t.title for t in store.load("a")] == ["only"]
    assert [t.title for t in store.load("b")] == ["B1"]
    store.save("a", [])
    assert store.load("a") == []


def test_forget_and_active_index(store: QueryTabStore) -> None:
    store.save("a", [TabState("A", "x", "sqlite")])
    store.set_active_index("a", 3)
    assert store.active_index("a") == 3
    store.forget("a")
    assert store.load("a") == []


def test_a_failing_save_leaves_the_previous_tabs(store: QueryTabStore) -> None:
    store.save("a", [TabState("keep", "x", "sqlite")])
    bad = TabState("bad", "x", None)  # type: ignore[arg-type]  # NOT NULL violation
    with pytest.raises(Exception, match="NOT NULL"):
        store.save("a", [TabState("new", "y", "sqlite"), bad])
    assert [t.title for t in store.load("a")] == ["keep"]
