from __future__ import annotations

from pathlib import Path

from easydbms.core.editing import EditKeyStore
from easydbms.core.schema import TableKey
from easydbms.core.storage import MIGRATIONS, AppDatabase

STOCK = TableKey("public", "stock")


def test_migration_five_creates_the_table(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "app.db")
    assert MIGRATIONS[4].version == 5
    columns = [r[1] for r in db.execute("PRAGMA table_info(edit_keys)")]
    assert columns == ["connection_id", "schema", "name", "columns"]


def test_a_choice_is_remembered_per_connection_and_table(tmp_path: Path) -> None:
    store = EditKeyStore(AppDatabase(tmp_path / "app.db"))
    assert store.get("c1", STOCK) is None
    store.set("c1", STOCK, ["sku", "warehouse"])
    assert store.get("c1", STOCK) == ("sku", "warehouse")
    assert store.get("c2", STOCK) is None
    assert store.get("c1", TableKey("other", "stock")) is None
    store.set("c1", STOCK, ["sku"])  # replaced, not duplicated
    assert store.get("c1", STOCK) == ("sku",)


def test_it_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    EditKeyStore(AppDatabase(path)).set("c1", STOCK, ["sku"])
    assert EditKeyStore(AppDatabase(path)).get("c1", STOCK) == ("sku",)


def test_clear_and_forget(tmp_path: Path) -> None:
    store = EditKeyStore(AppDatabase(tmp_path / "app.db"))
    store.set("c1", STOCK, ["sku"])
    store.set("c1", TableKey("public", "other"), ["id"])
    store.set("c2", STOCK, ["sku"])
    store.clear("c1", STOCK)
    assert store.get("c1", STOCK) is None
    assert store.get("c1", TableKey("public", "other")) == ("id",)
    store.forget("c1")
    assert store.get("c1", TableKey("public", "other")) is None
    assert store.get("c2", STOCK) == ("sku",)


def test_damaged_stored_values_are_ignored(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "app.db")
    store = EditKeyStore(db)
    for bad in ("{not json", '"a string"', "[1, 2]", "[]"):
        db.execute("DELETE FROM edit_keys")
        db.execute("INSERT INTO edit_keys VALUES ('c1', 'public', 'stock', ?)", (bad,))
        assert store.get("c1", STOCK) is None
