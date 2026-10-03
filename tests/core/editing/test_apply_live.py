"""Applying a change set: SQLite always, PostgreSQL / MySQL when configured."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from easydbms.core.db import BoundStatement, DatabaseClient
from easydbms.core.editing import (
    ChangeSet,
    EditTarget,
    ReadOnly,
    build_statements,
    parse_value,
    target_for_table,
)
from easydbms.core.schema import Table, introspect
from tests.core.conftest import Target

_TIMESTAMP = {"postgresql": "TIMESTAMP", "mysql": "DATETIME", "sqlite": "DATETIME"}


@dataclass
class Book:
    target: Target
    client: DatabaseClient
    name: str

    @property
    def quoted(self) -> str:
        return self.target.quote(self.name)

    def rows(self) -> list[tuple[Any, ...]]:
        return list(self.client.execute(f"SELECT * FROM {self.quoted} ORDER BY id").rows)

    def table(self) -> Table:
        found = introspect(self.client).find(self.name)
        assert found is not None
        return found

    def edit_target(self, table: Table | None = None, **options: Any) -> EditTarget:
        table = table or self.table()
        result = target_for_table(
            table, self.quoted, self.client.dialect, [c.name for c in table.columns], **options
        )
        assert not isinstance(result, ReadOnly), result
        return result

    def changes(self, **options: Any) -> ChangeSet:
        return ChangeSet(self.edit_target(**options))

    def apply(self, changes: ChangeSet) -> Any:
        planned = build_statements(changes, self.client.dialect)
        return self.client.apply([p.statement for p in planned])

    def original(self, row_id: int) -> dict[str, Any]:
        target = self.edit_target()
        for row in self.client.execute(f"SELECT * FROM {self.quoted}").rows:
            if row[target.key_index("id")] == row_id:
                return target.values_of(row)
        raise AssertionError(row_id)


@pytest.fixture
def book(target: Target) -> Iterator[Book]:
    name = target.table()
    quoted = target.quote(name)
    with target.client() as client:
        client.execute(
            f"CREATE TABLE {quoted} (id INTEGER PRIMARY KEY, title VARCHAR(60) NOT NULL, "
            f"pages INTEGER, price NUMERIC(10,2), published DATE, added {_TIMESTAMP[target.name]}, "
            "in_print BOOLEAN, note VARCHAR(200))"
        )
        client.execute(
            f"INSERT INTO {quoted} (id, title, pages, price, published, added, in_print, note) "
            "VALUES (1, 'Dune', 412, 9.90, '1965-08-01', '2020-05-05 10:00:00', TRUE, NULL), "
            "(2, 'Emma', 474, 7.50, '1815-12-23', NULL, FALSE, 'classic'), "
            "(3, 'Ulysses', 730, NULL, NULL, NULL, NULL, NULL)"
        )
        yield Book(target, client, name)


def other_connection(book: Book) -> DatabaseClient:
    client = book.target.client()
    client.connect()
    return client


# ------------------------------------------------------------------ the happy path


def test_edits_are_written_in_one_apply(book: Book) -> None:
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "title", "Dune Messiah")
    changes.set_cell((1,), book.original(1), "pages", 256)
    changes.set_cell((2,), book.original(2), "note", None)
    result = book.apply(changes)
    assert result.ok, result
    assert result.affected == (1, 1)
    rows = {r[0]: r for r in book.rows()}
    assert rows[1][1:3] == ("Dune Messiah", 256)
    assert rows[2][7] is None
    assert rows[3][1] == "Ulysses"  # untouched


def test_inserts_deletes_and_updates_together(book: Book) -> None:
    changes = book.changes()
    changes.delete_row((2,), book.original(2))
    changes.set_cell((3,), book.original(3), "pages", 731)
    new_id = changes.add_row({"id": 10, "title": "Emma 2", "pages": 1})
    changes.set_new_cell(new_id, "price", Decimal("1.25"))
    result = book.apply(changes)
    assert result.ok, result
    assert result.affected == (1, 1, 1)
    ids = [r[0] for r in book.rows()]
    assert ids == [1, 3, 10]
    assert book.original(10)["title"] == "Emma 2"
    assert book.original(3)["pages"] == 731


def test_values_typed_as_text_land_in_typed_columns(book: Book) -> None:
    target = book.edit_target()
    changes = ChangeSet(target)
    original = book.original(3)
    for column, text in (
        ("price", "12.50"),
        ("published", "2001-02-03"),
        ("added", "2024-02-29 13:45:10"),
        ("in_print", "yes"),
    ):
        kind = target.by_name(column).kind  # type: ignore[union-attr]
        changes.set_cell((3,), original, column, parse_value(kind, text))
    result = book.apply(changes)
    assert result.ok, result
    row = book.original(3)
    assert Decimal(str(row["price"])) == Decimal("12.5")
    assert str(row["published"]).startswith("2001-02-03")
    assert str(row["added"]).startswith("2024-02-29 13:45:10")
    assert bool(row["in_print"]) is True


def test_text_with_quotes_percent_signs_and_unicode_is_stored_verbatim(book: Book) -> None:
    changes = book.changes()
    text = '50% off — it\'s "quoted"; DROP TABLE x; -- ✓'
    changes.set_cell((1,), book.original(1), "note", text)
    assert book.apply(changes).ok
    assert book.original(1)["note"] == text


def test_an_update_can_change_the_key(book: Book) -> None:
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "id", 100)
    assert book.apply(changes).ok
    assert [r[0] for r in book.rows()] == [2, 3, 100]


def test_nothing_to_apply_is_fine(book: Book) -> None:
    result = book.client.apply([])
    assert result.ok
    assert result.affected == ()


def test_a_null_old_value_is_matched_with_is_null(book: Book) -> None:
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "note", "now there is one")
    assert book.apply(changes).ok
    assert book.original(1)["note"] == "now there is one"


# ------------------------------------------------------------------ conflicts and failures


def test_an_update_of_a_row_changed_by_someone_else_is_a_conflict(book: Book) -> None:
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "title", "Mine")
    changes.set_cell((3,), book.original(3), "pages", 1)  # a fine second statement
    with other_connection(book) as other:
        other.execute(f"UPDATE {book.quoted} SET title = 'Theirs' WHERE id = 1")
    planned = build_statements(changes, book.client.dialect)
    result = book.client.apply([p.statement for p in planned])
    assert not result.ok
    assert result.conflict
    assert result.matched == 0
    assert planned[result.failed or 0].kind == "update"
    assert book.original(1)["title"] == "Theirs"
    assert book.original(3)["pages"] == 730  # the other statement was rolled back as well


def test_an_update_to_a_value_somebody_else_changed_in_another_cell_still_applies(
    book: Book,
) -> None:
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "title", "Mine")
    with other_connection(book) as other:
        other.execute(f"UPDATE {book.quoted} SET pages = 999 WHERE id = 1")
    assert book.apply(changes).ok  # only the cells we change are checked
    row = book.original(1)
    assert (row["title"], row["pages"]) == ("Mine", 999)


def test_a_row_deleted_by_someone_else_is_a_conflict_for_update_and_delete(book: Book) -> None:
    update = book.changes()
    update.set_cell((2,), book.original(2), "title", "Mine")
    delete = book.changes()
    delete.delete_row((2,), book.original(2))
    with other_connection(book) as other:
        other.execute(f"DELETE FROM {book.quoted} WHERE id = 2")
    for changes in (update, delete):
        result = book.apply(changes)
        assert not result.ok
        assert result.conflict


def test_a_failing_statement_rolls_everything_back(book: Book) -> None:
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "title", "Changed")
    new_id = changes.add_row({"id": 2, "title": "duplicate key"})  # id 2 exists
    planned = build_statements(changes, book.client.dialect)
    result = book.client.apply([p.statement for p in planned])
    assert not result.ok
    assert not result.conflict
    assert result.failed is not None
    assert planned[result.failed].subject == new_id
    assert result.error
    assert book.original(1)["title"] == "Dune"


def test_a_not_null_violation_is_reported_not_raised(book: Book) -> None:
    changes = book.changes()
    changes.add_row({"id": 20})  # no title
    result = book.apply(changes)
    assert not result.ok
    assert result.error
    assert len(book.rows()) == 3


def test_a_key_that_is_not_unique_touches_several_rows_and_is_refused(book: Book) -> None:
    table = book.table()
    changes = ChangeSet(book.edit_target(table, chosen_key=["in_print"]))
    book.client.execute(f"UPDATE {book.quoted} SET in_print = TRUE")
    original = book.original(1)
    changes.set_cell((original["in_print"],), original, "note", "everyone")
    result = book.apply(changes)
    assert not result.ok
    assert result.matched is not None
    assert result.matched > 1  # the key matched several rows (the lock on `note` spared one)
    assert not result.conflict
    assert all(r[7] != "everyone" for r in book.rows())


def test_the_session_stays_usable_after_a_failed_apply(book: Book) -> None:
    bad = book.changes()
    bad.add_row({"id": 1, "title": "dup"})
    assert not book.apply(bad).ok
    good = book.changes()
    good.set_cell((1,), book.original(1), "title", "fine")
    assert book.apply(good).ok
    assert book.original(1)["title"] == "fine"


def test_nothing_is_visible_to_others_before_the_commit(book: Book) -> None:
    """The statements run inside a transaction: a rolled back apply leaves no trace for anyone."""
    changes = book.changes()
    changes.set_cell((1,), book.original(1), "title", "Temp")
    changes.add_row({"id": 2, "title": "duplicate"})  # fails after the update ran
    book.apply(changes)
    with other_connection(book) as other:
        rows = other.execute(f"SELECT title FROM {book.quoted} WHERE id = 1").rows
    assert rows == (("Dune",),)


def test_found_rows_semantics_count_matched_rows_even_if_nothing_changes(book: Book) -> None:
    statement = BoundStatement(
        f"UPDATE {book.quoted} SET title = title WHERE id = "
        + ("?" if book.target.name == "sqlite" else "%s"),
        (1,),
        1,
    )
    result = book.client.apply([statement])
    assert result.ok, result
    assert result.affected == (1,)


def test_applying_while_not_connected_raises(book: Book) -> None:
    from easydbms.core.db import NotConnectedError

    client = book.target.client()
    with pytest.raises(NotConnectedError):
        client.apply([])


def test_date_and_datetime_old_values_lock_correctly(book: Book) -> None:
    changes = book.changes()
    original = book.original(1)
    changes.set_cell((1,), original, "published", date(2000, 1, 1))
    changes.set_cell((1,), original, "added", datetime(2001, 2, 3, 4, 5, 6))
    changes.set_cell((1,), original, "price", Decimal("1.00"))
    assert book.apply(changes).ok
    again = book.changes()
    fresh = book.original(1)
    again.set_cell((1,), fresh, "published", date(2010, 1, 1))
    again.set_cell((1,), fresh, "added", datetime(2011, 2, 3, 4, 5, 6))
    again.set_cell((1,), fresh, "price", Decimal("2.00"))
    result = book.apply(again)
    assert result.ok, result
