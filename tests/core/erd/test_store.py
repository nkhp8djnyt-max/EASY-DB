from __future__ import annotations

from pathlib import Path

import pytest

from easydbms.core.erd import ALL_SCHEMAS, ErdLayoutStore
from easydbms.core.schema import TableKey
from easydbms.core.storage import AppDatabase


@pytest.fixture
def store(tmp_path: Path) -> ErdLayoutStore:
    return ErdLayoutStore(AppDatabase(tmp_path / "app.db"))


A, B = TableKey("public", "a"), TableKey("public", "b")


def test_round_trip(store: ErdLayoutStore) -> None:
    store.save("c1", "public", {A: (10.5, 20.0), B: (-5.0, 7.25)})
    assert store.load("c1", "public") == {A: (10.5, 20.0), B: (-5.0, 7.25)}


def test_save_updates_in_place(store: ErdLayoutStore) -> None:
    store.save("c1", "public", {A: (1.0, 2.0), B: (3.0, 4.0)})
    store.save("c1", "public", {A: (9.0, 9.0)})
    assert store.load("c1", "public") == {A: (9.0, 9.0), B: (3.0, 4.0)}


def test_scopes_and_connections_are_separate(store: ErdLayoutStore) -> None:
    store.save("c1", "public", {A: (1.0, 1.0)})
    store.save("c1", ALL_SCHEMAS, {A: (2.0, 2.0)})
    store.save("c2", "public", {A: (3.0, 3.0)})
    assert store.load("c1", "public")[A] == (1.0, 1.0)
    assert store.load("c1", ALL_SCHEMAS)[A] == (2.0, 2.0)
    assert store.load("c2", "public")[A] == (3.0, 3.0)
    assert store.load("c3", "public") == {}


def test_discard_clear_forget(store: ErdLayoutStore) -> None:
    store.save("c1", "s", {A: (1.0, 1.0), B: (2.0, 2.0)})
    store.save("c1", "t", {A: (5.0, 5.0)})
    store.discard("c1", "s", [A])
    assert store.load("c1", "s") == {B: (2.0, 2.0)}
    store.clear("c1", "s")
    assert store.load("c1", "s") == {}
    assert store.load("c1", "t") == {A: (5.0, 5.0)}
    store.forget("c1")
    assert store.load("c1", "t") == {}


def test_names_with_dots_survive(store: ErdLayoutStore) -> None:
    odd = TableKey("my.schema", "my.table")
    store.save("c", "*", {odd: (1.0, 2.0)})
    assert store.load("c", "*") == {odd: (1.0, 2.0)}


def test_empty_save_is_a_noop(store: ErdLayoutStore) -> None:
    store.save("c", "s", {})
    assert store.load("c", "s") == {}
