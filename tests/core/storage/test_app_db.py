from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from sql_erd_studio.core.storage import (
    MIGRATIONS,
    AppDatabase,
    DatabaseTooNewError,
    Migration,
)

TWO = (
    Migration(1, "first", ("CREATE TABLE a (x INTEGER)", "INSERT INTO a VALUES (1)")),
    Migration(2, "second", ("CREATE TABLE b (y INTEGER)",)),
)


def tables(db: AppDatabase) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_a_new_database_gets_all_migrations(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "data" / "app.db")
    assert db.schema_version == len(MIGRATIONS)
    assert "ui_state" in tables(db)
    db.close()


def test_state_round_trip_and_overwrite(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "app.db")
    assert db.get_state("missing") is None
    assert db.get_state("missing", 42) == 42
    for value in ({"sizes": [300, 700], "maximized": False}, "text ✓", 7, 1.5, [1, 2], None, True):
        db.set_state("key", value)
        assert db.get_state("key") == value
    db.set_state("key", "latest")
    assert db.get_state("key") == "latest"
    assert db.execute("SELECT count(*) FROM ui_state") == [(1,)]
    db.delete_state("key")
    assert db.get_state("key") is None
    db.delete_state("key")  # deleting a missing key is fine


def test_unreadable_values_fall_back_to_the_default(tmp_path: Path) -> None:
    db = AppDatabase(tmp_path / "app.db")
    db.execute("INSERT INTO ui_state VALUES ('bad', '{not json')")
    assert db.get_state("bad", "fallback") == "fallback"


def test_data_persists_and_migrations_are_not_rerun(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    first = AppDatabase(path, TWO)
    first.execute("INSERT INTO a VALUES (2)")
    first.close()
    second = AppDatabase(path, TWO)
    assert second.execute("SELECT x FROM a ORDER BY x") == [(1,), (2,)]  # the seed row exists once
    assert second.schema_version == 2


def test_upgrade_keeps_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    old = AppDatabase(path, TWO[:1])
    old.execute("INSERT INTO a VALUES (5)")
    assert old.schema_version == 1
    old.close()
    upgraded = AppDatabase(path, TWO)
    assert upgraded.schema_version == 2
    assert tables(upgraded) >= {"a", "b"}
    assert upgraded.execute("SELECT x FROM a ORDER BY x") == [(1,), (5,)]


def test_a_failing_migration_rolls_back_completely(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    AppDatabase(path, TWO[:1]).close()
    broken = (
        *TWO[:1],
        Migration(2, "half done", ("CREATE TABLE half (z INTEGER)", "THIS IS NOT SQL")),
    )
    with pytest.raises(sqlite3.Error):
        AppDatabase(path, broken)
    # the previous state is intact: version 1, and no leftover "half" table
    check = AppDatabase(path, TWO[:1])
    assert check.schema_version == 1
    assert "half" not in tables(check)


def test_a_database_from_a_newer_application_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    AppDatabase(path, TWO).close()
    with pytest.raises(DatabaseTooNewError, match="version 2"):
        AppDatabase(path, TWO[:1])
    # and nothing was modified
    assert AppDatabase(path, TWO).schema_version == 2


@pytest.mark.parametrize(
    "migrations",
    [
        (Migration(2, "starts at two", ("SELECT 1",)),),
        (Migration(1, "a", ("SELECT 1",)), Migration(3, "gap", ("SELECT 1",))),
        (Migration(1, "a", ("SELECT 1",)), Migration(1, "dup", ("SELECT 1",))),
    ],
)
def test_migration_numbering_is_validated(
    tmp_path: Path, migrations: tuple[Migration, ...]
) -> None:
    with pytest.raises(ValueError, match="without gaps"):
        AppDatabase(tmp_path / "app.db", migrations)


def test_shipped_migrations_are_numbered_correctly() -> None:
    assert [m.version for m in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))


def test_in_memory_database_and_threads() -> None:
    db = AppDatabase(":memory:")
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            for i in range(50):
                db.set_state(f"k{n}", i)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert [db.get_state(f"k{n}") for n in range(4)] == [49] * 4
