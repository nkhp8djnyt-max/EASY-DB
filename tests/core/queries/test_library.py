"""History of executed statements and saved queries (``app.db``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from easydbms.core.queries import (
    HistoryOutcome,
    HistoryStore,
    SavedQueryStore,
    normalize_folder,
)
from easydbms.core.storage import AppDatabase

OK = HistoryOutcome.OK


@pytest.fixture
def db(tmp_path: Path) -> AppDatabase:
    return AppDatabase(tmp_path / "app.db")


def run(
    history: HistoryStore,
    sql: str,
    connection: str = "c1",
    at: float = 1000.0,
    outcome: HistoryOutcome = OK,
    **extra: object,
) -> None:
    history.record(connection, sql, "postgresql", duration=0.01, outcome=outcome, at=at, **extra)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- history


def test_entries_come_back_newest_first_with_everything_recorded(db: AppDatabase) -> None:
    history = HistoryStore(db)
    run(history, "SELECT 1", at=1.0, row_count=1)
    run(history, "SELECT bad", at=2.0, outcome=HistoryOutcome.ERROR, error="no such column")
    first, second = reversed(history.recent("c1"))
    assert (first.sql, first.outcome, first.row_count, first.runs) == ("SELECT 1", OK, 1, 1)
    assert (second.outcome, second.error) == (HistoryOutcome.ERROR, "no such column")
    assert second.row_count is None
    assert [e.sql for e in history.recent("c1")] == ["SELECT bad", "SELECT 1"]


def test_the_same_statement_twice_in_a_row_is_one_entry_with_a_counter(db: AppDatabase) -> None:
    history = HistoryStore(db)
    run(history, "SELECT 1", at=1.0)
    run(history, "SELECT 1", at=5.0, row_count=1)
    (entry,) = history.recent("c1")
    assert (entry.runs, entry.executed_at, entry.row_count) == (2, 5.0, 1)
    run(history, "SELECT 2", at=6.0)
    run(history, "SELECT 1", at=7.0)  # not in a row: a new line
    assert [e.sql for e in history.recent("c1")] == ["SELECT 1", "SELECT 2", "SELECT 1"]


def test_whitespace_around_a_statement_is_ignored_and_blank_ones_are_not_kept(
    db: AppDatabase,
) -> None:
    history = HistoryStore(db)
    run(history, "  SELECT 1;\n")
    run(history, "   \n")
    (entry,) = history.recent("c1")
    assert entry.sql == "SELECT 1;"
    assert entry.first_line == "SELECT 1;"


def test_every_connection_has_its_own_history_and_its_own_limit(db: AppDatabase) -> None:
    history = HistoryStore(db, limit=3)
    for number in range(6):
        run(history, f"SELECT {number}", at=float(number))
    run(history, "SELECT 'other'", connection="c2")
    assert [e.sql for e in history.recent("c1")] == ["SELECT 5", "SELECT 4", "SELECT 3"]
    assert history.count("c1") == 3
    assert history.count("c2") == 1
    assert history.count() == 4
    assert len(history.recent(None)) == 4


def test_search_is_literal_and_case_insensitive(db: AppDatabase) -> None:
    history = HistoryStore(db)
    run(history, "SELECT * FROM Orders WHERE total > 100", at=1.0)
    run(history, "SELECT 'a%b'", at=2.0)
    run(history, "SELECT 'a_b'", at=3.0)
    assert [e.sql for e in history.recent("c1", search="orders")] == [
        "SELECT * FROM Orders WHERE total > 100"
    ]
    assert [e.sql for e in history.recent("c1", search="a%b")] == [
        "SELECT 'a%b'"
    ]  # % is not a wildcard
    assert [e.sql for e in history.recent("c1", search="a_b")] == ["SELECT 'a_b'"]
    assert history.recent("c1", search="nothing like this") == []


def test_only_the_failures_can_be_shown_and_pages_work(db: AppDatabase) -> None:
    history = HistoryStore(db)
    for number in range(5):
        run(history, f"SELECT {number}", at=float(number))
    run(history, "BROKEN", at=10.0, outcome=HistoryOutcome.ERROR, error="syntax")
    assert [e.sql for e in history.recent("c1", errors_only=True)] == ["BROKEN"]
    assert [e.sql for e in history.recent("c1", limit=2)] == ["BROKEN", "SELECT 4"]
    assert [e.sql for e in history.recent("c1", limit=2, offset=2)] == ["SELECT 3", "SELECT 2"]


def test_entries_can_be_deleted_one_by_one_or_per_connection(db: AppDatabase) -> None:
    history = HistoryStore(db)
    run(history, "SELECT 1", at=1.0)
    run(history, "SELECT 2", at=2.0)
    run(history, "SELECT 3", connection="c2")
    history.delete(history.recent("c1")[0].id)
    assert [e.sql for e in history.recent("c1")] == ["SELECT 1"]
    history.clear("c1")
    assert history.recent("c1") == []
    assert history.count("c2") == 1
    history.forget("c2")
    assert history.count() == 0


def test_a_history_must_keep_something(db: AppDatabase) -> None:
    with pytest.raises(ValueError, match="at least one"):
        HistoryStore(db, limit=0)


def test_history_survives_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "again.db"
    first = AppDatabase(path)
    run(HistoryStore(first), "SELECT 'é ✓'")
    first.close()
    assert [e.sql for e in HistoryStore(AppDatabase(path)).recent("c1")] == ["SELECT 'é ✓'"]


# ---------------------------------------------------------------------------- saved queries


def test_a_saved_query_keeps_its_text_exactly(db: AppDatabase) -> None:
    saved = SavedQueryStore(db)
    sql = "SELECT *\n  FROM orders\n WHERE note = 'é ✓';\n"
    query = saved.add("  Orders  ", sql, "postgresql", folder=" Reports / Monthly ", at=10.0)
    assert (query.name, query.folder, query.sql) == ("Orders", "Reports/Monthly", sql)
    assert (query.created_at, query.updated_at) == (10.0, 10.0)
    assert saved.get(query.id) == query
    assert saved.get(9999) is None


def test_nothing_nameless_or_empty_is_saved(db: AppDatabase) -> None:
    saved = SavedQueryStore(db)
    with pytest.raises(ValueError, match="needs a name"):
        saved.add("  ", "SELECT 1", "postgresql")
    with pytest.raises(ValueError, match="nothing to save"):
        saved.add("x", " \n", "postgresql")


def test_queries_are_listed_by_folder_then_name_and_scoped_to_a_connection(
    db: AppDatabase,
) -> None:
    saved = SavedQueryStore(db)
    saved.add("zeta", "SELECT 1", "postgresql")
    saved.add("alpha", "SELECT 2", "postgresql", folder="Reports")
    saved.add("Beta", "SELECT 3", "postgresql", folder="Reports")
    saved.add("mine", "SELECT 4", "mysql", connection_id="c1")
    saved.add("theirs", "SELECT 5", "mysql", connection_id="c2")
    assert [q.name for q in saved.all()] == ["alpha", "Beta", "mine", "theirs", "zeta"]
    for_c1 = [q.name for q in saved.all("c1")]
    assert for_c1 == ["alpha", "Beta", "mine", "zeta"]  # folders first, then loose queries
    assert "theirs" not in for_c1
    assert saved.folders() == ["Reports"]


def test_update_changes_only_what_is_given(db: AppDatabase) -> None:
    saved = SavedQueryStore(db)
    query = saved.add("old", "SELECT 1", "postgresql", at=1.0)
    renamed = saved.update(query.id, name="new", at=2.0)
    assert (renamed.name, renamed.sql, renamed.updated_at, renamed.created_at) == (
        "new",
        "SELECT 1",
        2.0,
        1.0,
    )
    moved = saved.update(query.id, folder="A / B")
    assert moved.folder == "A/B"
    assert saved.update(query.id, sql="SELECT 2").sql == "SELECT 2"
    with pytest.raises(ValueError, match="needs a name"):
        saved.update(query.id, name=" ")
    with pytest.raises(ValueError, match="nothing to save"):
        saved.update(query.id, sql="")
    with pytest.raises(KeyError):
        saved.update(12345, name="x")


def test_deleting_and_forgetting(db: AppDatabase) -> None:
    saved = SavedQueryStore(db)
    keep = saved.add("shared", "SELECT 1", "postgresql")
    gone = saved.add("own", "SELECT 2", "postgresql", connection_id="c1")
    saved.add("other", "SELECT 3", "postgresql", connection_id="c1")
    saved.delete(gone.id)
    assert [q.name for q in saved.all("c1")] == ["other", "shared"]
    saved.forget("c1")  # the connection was deleted: its own queries go, shared ones stay
    assert [q.id for q in saved.all()] == [keep.id]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("", ""),
        ("  ", ""),
        ("a", "a"),
        (" a / b ", "a/b"),
        ("a//b/", "a/b"),
        ("a\\b", "a/b"),
    ],
)
def test_folder_names_are_tidied(raw: str, clean: str) -> None:
    assert normalize_folder(raw) == clean
