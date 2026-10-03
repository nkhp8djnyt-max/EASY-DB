from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from easydbms.core.dialects import MYSQL, POSTGRESQL, SQLITE, Dialect
from easydbms.core.editing import ChangeSet, bind_value, build_statements, preview_script, row_label
from easydbms.core.schema import Table

from .conftest import column, composite_table, target_of

ORIGINAL = {
    "id": 1,
    "title": "Dune",
    "pages": 412,
    "price": Decimal("9.90"),
    "rating": 4.5,
    "published": date(1965, 8, 1),
    "added": None,
    "in_print": True,
    "meta": None,
    "cover": None,
}


def build(changes: ChangeSet, dialect: Dialect = POSTGRESQL):  # type: ignore[no-untyped-def]
    return build_statements(changes, dialect)


def changeset(dialect: Dialect = POSTGRESQL, table: Table | None = None) -> ChangeSet:
    return ChangeSet(target_of(table, dialect))


def test_an_update_checks_the_key_and_the_old_value_of_every_changed_cell() -> None:
    changes = changeset()
    changes.set_cell((1,), ORIGINAL, "title", "Dune Messiah")
    changes.set_cell((1,), ORIGINAL, "pages", 256)
    (planned,) = build(changes)
    assert planned.kind == "update"
    assert planned.statement.sql == (
        'UPDATE "public"."books" SET "title" = %s, "pages" = %s '
        'WHERE "id" = %s AND "title" = %s AND "pages" = %s'
    )
    assert planned.statement.params == ("Dune Messiah", 256, 1, "Dune", 412)
    assert planned.statement.expect_rows == 1
    assert planned.subject == (1,)
    assert planned.columns == ("title", "pages")


def test_an_old_null_is_compared_with_is_null_and_needs_no_parameter() -> None:
    changes = changeset()
    changes.set_cell((1,), ORIGINAL, "added", datetime(2024, 1, 1))
    (planned,) = build(changes)
    assert planned.statement.sql.endswith('WHERE "id" = %s AND "added" IS NULL')
    assert planned.statement.params == (datetime(2024, 1, 1), 1)


def test_unreliable_types_are_not_part_of_the_lock() -> None:
    changes = changeset()
    changes.set_cell((1,), ORIGINAL, "rating", 3.0)  # float
    changes.set_cell((1,), ORIGINAL, "meta", '{"a": 1}')  # json
    (planned,) = build(changes)
    assert planned.statement.sql.endswith('WHERE "id" = %s')
    assert planned.statement.params == (3.0, '{"a": 1}', 1)


def test_the_key_is_not_locked_twice_when_it_is_edited() -> None:
    changes = changeset()
    changes.set_cell((1,), ORIGINAL, "id", 5)
    (planned,) = build(changes)
    assert planned.statement.sql == 'UPDATE "public"."books" SET "id" = %s WHERE "id" = %s'
    assert planned.statement.params == (5, 1)  # the new key, then the key it was loaded with


def test_composite_keys_are_all_in_the_where_clause() -> None:
    changes = ChangeSet(target_of(composite_table()))
    original = {"order_id": 1, "line_no": 2, "qty": 3, "note": "n"}
    changes.set_cell((1, 2), original, "qty", 4)
    changes.delete_row((9, 8), {"order_id": 9, "line_no": 8, "qty": 1, "note": None})
    delete, update = build(changes)
    assert (
        delete.statement.sql
        == 'DELETE FROM "public"."lines" WHERE "order_id" = %s AND "line_no" = %s'
    )
    assert delete.statement.params == (9, 8)
    assert update.statement.params == (4, 1, 2, 3)


def test_delete_uses_the_key_only() -> None:
    changes = changeset()
    changes.delete_row((1,), ORIGINAL)
    (planned,) = build(changes)
    assert planned.statement.sql == 'DELETE FROM "public"."books" WHERE "id" = %s'
    assert planned.statement.params == (1,)
    assert planned.statement.expect_rows == 1
    assert planned.columns == ()


def test_insert_lists_only_the_columns_that_were_set_in_table_order() -> None:
    changes = changeset()
    new_id = changes.add_row({"title": "Emma"})
    changes.set_new_cell(new_id, "pages", 300)
    changes.set_new_cell(new_id, "price", None)
    (planned,) = build(changes)
    assert planned.kind == "insert"
    assert planned.subject == new_id
    assert planned.statement.sql == (
        'INSERT INTO "public"."books" ("title", "pages", "price") VALUES (%s, %s, %s)'
    )
    assert planned.statement.params == ("Emma", 300, None)


@pytest.mark.parametrize(
    ("dialect", "tail"),
    [
        (POSTGRESQL, 'INSERT INTO "public"."books" DEFAULT VALUES'),
        (SQLITE, 'INSERT INTO "public"."books" DEFAULT VALUES'),
        (MYSQL, "INSERT INTO `public`.`books` () VALUES ()"),
    ],
)
def test_an_insert_without_values_uses_the_defaults(dialect: Dialect, tail: str) -> None:
    changes = changeset(dialect)
    changes.add_row()
    (planned,) = build(changes, dialect)
    assert planned.statement.sql == tail
    assert planned.statement.params == ()


def test_deletes_come_first_then_updates_then_inserts() -> None:
    changes = changeset()
    changes.add_row({"title": "n"})
    changes.set_cell((1,), ORIGINAL, "title", "A")
    changes.delete_row((2,), {**ORIGINAL, "id": 2})
    assert [p.kind for p in build(changes)] == ["delete", "update", "insert"]


@pytest.mark.parametrize(
    ("dialect", "marker", "quote"),
    [(POSTGRESQL, "%s", '"'), (MYSQL, "%s", "`"), (SQLITE, "?", '"')],
)
def test_placeholders_and_quoting_follow_the_dialect(
    dialect: Dialect, marker: str, quote: str
) -> None:
    changes = changeset(dialect)
    changes.set_cell((1,), ORIGINAL, "title", "x")
    (planned,) = build(changes, dialect)
    assert planned.statement.sql.count(marker) == 3
    assert f"{quote}title{quote} = {marker}" in planned.statement.sql


def test_percent_signs_in_names_are_escaped_for_percent_style_drivers() -> None:
    table = Table(
        "public",
        "50% off",
        columns=(column("id", pk=True), column("a%b", "text")),
        primary_key=("id",),
    )
    changes = ChangeSet(target_of(table))
    changes.set_cell((1,), {"id": 1, "a%b": "old"}, "a%b", "new")
    (planned,) = build(changes)
    assert '"50%% off"' in planned.statement.sql
    assert '"a%%b" = %s' in planned.statement.sql
    assert "%%" in planned.statement.sql
    assert "50% off" in planned.preview  # the preview is for people: no escaping
    sqlite = ChangeSet(target_of(table, SQLITE))
    sqlite.set_cell((1,), {"id": 1, "a%b": "old"}, "a%b", "new")
    assert '"a%b" = ?' in build(sqlite, SQLITE)[0].statement.sql


def test_values_are_never_pasted_into_the_executable_sql() -> None:
    changes = changeset()
    nasty = "x'; DROP TABLE books; --"
    changes.set_cell((1,), ORIGINAL, "title", nasty)
    (planned,) = build(changes)
    assert nasty not in planned.statement.sql
    assert nasty in planned.statement.params
    assert "DROP" not in planned.statement.sql
    assert "'x''; DROP TABLE books; --'" in planned.preview  # quoted in the preview


def test_the_preview_shows_literals() -> None:
    changes = changeset()
    changes.set_cell((1,), ORIGINAL, "title", "O'Neil")
    changes.set_cell((1,), ORIGINAL, "added", datetime(2024, 1, 2, 3, 4, 5))
    changes.delete_row((2,), {**ORIGINAL, "id": 2})
    planned = build(changes)
    assert planned[0].preview == 'DELETE FROM "public"."books" WHERE "id" = 2'
    assert planned[1].preview == (
        'UPDATE "public"."books" SET "title" = \'O\'\'Neil\', "added" = \'2024-01-02 03:04:05\' '
        'WHERE "id" = 1 AND "title" = \'Dune\' AND "added" IS NULL'
    )
    script = preview_script(planned)
    assert script.count(";") == 2
    assert script.splitlines()[0].endswith(";")


def test_a_value_that_cannot_be_rendered_falls_back_to_repr() -> None:
    changes = changeset(MYSQL)
    changes.set_cell(
        (1,), ORIGINAL, "rating", float("nan")
    )  # parse_value refuses NaN; the builder must not crash
    (planned,) = build(changes, MYSQL)
    assert "nan" in planned.preview


def test_row_label() -> None:
    assert row_label(target_of(), (7,)) == "id = 7"
    assert row_label(target_of(composite_table()), (1, 2)) == "order_id = 1, line_no = 2"


@pytest.mark.parametrize(
    ("dialect", "value", "bound"),
    [
        (SQLITE, datetime(2024, 1, 2, 3, 4, 5), "2024-01-02 03:04:05"),
        (SQLITE, date(2024, 1, 2), "2024-01-02"),
        (SQLITE, Decimal("12.50"), 12.5),
        (SQLITE, Decimal("12"), 12),
        (SQLITE, UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (SQLITE, True, True),
        (MYSQL, UUID(int=1), "00000000-0000-0000-0000-000000000001"),
        (MYSQL, Decimal("1.5"), Decimal("1.5")),
        (POSTGRESQL, UUID(int=1), UUID(int=1)),
        (POSTGRESQL, datetime(2024, 1, 1), datetime(2024, 1, 1)),
        (POSTGRESQL, None, None),
    ],
)
def test_bind_value(dialect: Dialect, value: object, bound: object) -> None:
    result = bind_value(dialect, value)
    assert result == bound
    assert type(result) is type(bound)
