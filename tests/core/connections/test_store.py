from __future__ import annotations

import json
from pathlib import Path

import pytest

from sql_erd_studio.core.connections import (
    ConnectionNotFoundError,
    ConnectionStore,
    FileConnection,
    ServerConnection,
)


def pg(name: str = "pg", **extra: object) -> ServerConnection:
    return ServerConnection.model_validate(
        {"name": name, "dialect": "postgresql", "host": "h", "database": "d"} | extra
    )


def test_empty_store(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "connections.json")
    assert store.all() == []
    assert store.find("nope") is None
    assert store.recovered_from is None
    assert not (tmp_path / "connections.json").exists()


def test_save_get_and_persist(tmp_path: Path) -> None:
    path = tmp_path / "config" / "connections.json"
    store = ConnectionStore(path)
    first, second = pg("one"), FileConnection(name="two", path="/x.db")
    store.save(first)
    store.save(second)
    assert store.get(first.id) == first

    reloaded = ConnectionStore(path)
    assert [c.name for c in reloaded.all()] == ["one", "two"]
    assert reloaded.get(second.id) == second
    assert isinstance(reloaded.get(first.id), ServerConnection)
    assert isinstance(reloaded.get(second.id), FileConnection)


def test_save_replaces_the_connection_with_the_same_id(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    config = pg("old")
    store.save(config)
    store.save(config.model_copy(update={"name": "new"}))
    assert [c.name for c in store.all()] == ["new"]


def test_remove(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    config = pg()
    store.save(config)
    store.remove(config.id)
    assert store.all() == []
    assert ConnectionStore(tmp_path / "c.json").all() == []
    with pytest.raises(ConnectionNotFoundError):
        store.remove(config.id)
    with pytest.raises(ConnectionNotFoundError):
        store.get(config.id)


def test_duplicate_gets_a_new_id_and_name(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    original = pg("prod", production=True, group="work")
    store.save(original)
    copy = store.duplicate(original.id)
    assert copy.id != original.id
    assert copy.name == "prod (copy)"
    assert (copy.production, copy.group) == (True, "work")
    assert len(ConnectionStore(tmp_path / "c.json").all()) == 2


def test_groups(tmp_path: Path) -> None:
    store = ConnectionStore(tmp_path / "c.json")
    for name, group in [("a", "work"), ("b", ""), ("c", "home"), ("d", "work")]:
        store.save(pg(name, group=group))
    assert store.groups() == ["home", "work"]


def test_file_holds_no_secrets_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    store = ConnectionStore(path)
    store.save(pg(options={"sslmode": "require"}))
    document = json.loads(path.read_text())
    assert document["version"] == 1
    assert "password" not in path.read_text().lower().replace("save_password", "")
    assert document["connections"][0]["options"] == {"sslmode": "require"}
    assert [p.name for p in tmp_path.iterdir()] == ["c.json"]


def test_unreadable_file_is_set_aside_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{ this is not json")
    store = ConnectionStore(path)
    assert store.all() == []
    assert store.recovered_from is not None
    assert store.recovered_from.read_text() == "{ this is not json"
    assert not path.exists()
    store.save(pg())  # a fresh file is created next to the preserved one
    assert path.exists()
    assert store.recovered_from.exists()


def test_invalid_entries_are_dropped_but_the_original_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    good = pg("good")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "connections": [
                    good.model_dump(mode="json"),
                    {"kind": "server", "name": "bad", "dialect": "oracle"},
                ],
            }
        )
    )
    store = ConnectionStore(path)
    assert [c.name for c in store.all()] == ["good"]
    assert store.recovered_from is not None
    assert "oracle" in store.recovered_from.read_text()
    assert [c.name for c in ConnectionStore(path).all()] == ["good"]
    assert ConnectionStore(path).recovered_from is None


@pytest.mark.parametrize("content", ["[]", '{"connections": "nope"}', '{"nothing": 1}'])
def test_wrong_document_shapes(tmp_path: Path, content: str) -> None:
    path = tmp_path / "c.json"
    path.write_text(content)
    store = ConnectionStore(path)
    assert store.all() == []
    assert store.recovered_from is not None
