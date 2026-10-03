"""Autocomplete on schemas read from the real engines (SQLite always, PostgreSQL / MySQL if set)."""

from __future__ import annotations

from easydbms.core.autocomplete import Completer, Completions, Kind
from easydbms.core.schema import DatabaseSchema, introspect

from .conftest import Shop


def run(shop: Shop, schema: DatabaseSchema, sql: str, **options: object) -> Completions:
    offset = sql.rindex("|")
    text = sql[:offset] + sql[offset + 1 :]
    dialect = shop.target.config.dialect_impl
    return Completer().complete(text, offset, dialect, schema, **options)  # type: ignore[arg-type]


def labels(result: Completions, kind: Kind | None = None) -> list[str]:
    return [i.label for i in result.items if kind is None or i.kind is kind]


def test_tables_of_the_database_are_offered(shop: Shop) -> None:
    schema = introspect(shop.client)
    result = run(shop, schema, f"SELECT * FROM {shop.prefix}_|")
    tables = labels(result, Kind.TABLE)
    assert {shop.name("author"), shop.name("book"), shop.name("book_tag")} <= set(tables)
    assert labels(result, Kind.VIEW) == [shop.name("authors_v")]
    item = next(i for i in result.items if i.label == shop.name("book"))
    assert item.insert == shop.name("book")  # the default schema is left out
    assert item.detail == "5 cols"


def test_columns_by_alias_and_their_types(shop: Shop) -> None:
    schema = introspect(shop.client)
    result = run(shop, schema, f"SELECT b.| FROM {shop.name('book')} b")
    assert labels(result) == ["author_id", "id", "isbn", "published", "title"]
    types = {i.label: i.detail.lower() for i in result.items}
    assert "int" in types["author_id"]
    assert "char" in types["title"] or "text" in types["title"]


def test_the_join_condition_comes_from_the_foreign_key(shop: Shop) -> None:
    schema = introspect(shop.client)
    sql = f"SELECT * FROM {shop.name('book')} b JOIN {shop.name('author')} a ON |"
    first = run(shop, schema, sql).items[0]
    assert first.kind is Kind.JOIN
    assert first.insert == "b.author_id = a.id"
    reverse = f"SELECT * FROM {shop.name('author')} a JOIN {shop.name('book')} b ON |"
    assert run(shop, schema, reverse).items[0].insert == "b.author_id = a.id"


def test_composite_foreign_keys_and_self_joins(shop: Shop) -> None:
    schema = introspect(shop.client)
    sql = f"SELECT * FROM {shop.name('note')} n JOIN {shop.name('line')} l ON |"
    assert run(shop, schema, sql).items[0].insert == (
        "n.order_id = l.order_id AND n.line_no = l.line_no"
    )
    sql = f"SELECT * FROM {shop.name('node')} c JOIN {shop.name('node')} p ON |"
    joins = {i.insert for i in run(shop, schema, sql).items if i.kind is Kind.JOIN}
    assert joins == {"c.parent_id = p.id", "p.parent_id = c.id"}


def test_tables_related_by_a_key_come_first_after_join(shop: Shop) -> None:
    schema = introspect(shop.client)
    result = run(shop, schema, f"SELECT * FROM {shop.name('book')} b JOIN {shop.prefix}_|")
    tables = labels(result, Kind.TABLE)
    assert set(tables[:3]) == {shop.name("author"), shop.name("book_tag")} | {shop.name("book")}
    assert shop.name("tag") not in tables[:3]


def test_star_expansion_lists_the_real_columns(shop: Shop) -> None:
    schema = introspect(shop.client)
    sql = f"SELECT *| FROM {shop.name('author')}"
    result = run(shop, schema, sql, forced=True)
    star = result.items[0]
    assert star.kind is Kind.STAR
    assert star.insert == "id, name, email, bio"


def test_views_expose_their_columns(shop: Shop) -> None:
    schema = introspect(shop.client)
    result = run(shop, schema, f"SELECT | FROM {shop.name('authors_v')}")
    assert {"id", "name"} <= set(labels(result, Kind.COLUMN))


def test_insert_target_columns_skip_the_ones_already_listed(shop: Shop) -> None:
    schema = introspect(shop.client)
    result = run(shop, schema, f"INSERT INTO {shop.name('author')} (id, |")
    assert labels(result) == ["name", "email", "bio"]
