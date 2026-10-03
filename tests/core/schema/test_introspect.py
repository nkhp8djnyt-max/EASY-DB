from __future__ import annotations

import pytest

from easydbms.core.schema import DatabaseSchema, Table, TableKey, TableKind, introspect

from .conftest import Shop


def load(shop: Shop) -> tuple[DatabaseSchema, dict[str, Table]]:
    schema = introspect(shop.client)
    tables = {}
    for short in (
        "author",
        "book",
        "tag",
        "book_tag",
        "profile",
        "line",
        "note",
        "node",
        "authors_v",
    ):
        table = schema.find(shop.name(short))
        assert table is not None, f"{short} missing from {[t.name for t in schema.tables][:20]}"
        tables[short] = table
    return schema, tables


def test_lists_tables_and_views(shop: Shop) -> None:
    _, t = load(shop)
    assert t["author"].kind is TableKind.TABLE
    assert t["authors_v"].kind is TableKind.VIEW
    assert [c.name for c in t["authors_v"].columns] == ["id", "name"]
    assert t["authors_v"].foreign_keys == ()


def test_columns(shop: Shop) -> None:
    _, t = load(shop)
    author = t["author"]
    assert [c.name for c in author.columns] == ["id", "name", "email", "bio"]
    id_, name, email, bio = author.columns
    assert "int" in id_.type.lower()
    assert id_.primary_key
    assert not name.primary_key
    assert not name.nullable
    assert email.nullable
    assert bio.default is not None
    assert "n/a" in bio.default
    assert name.default is None


def test_primary_keys_keep_key_order(shop: Shop) -> None:
    _, t = load(shop)
    assert t["author"].primary_key == ("id",)
    assert t["book_tag"].primary_key == ("book_id", "tag_id")
    assert [c.name for c in t["book_tag"].columns if c.primary_key] == ["book_id", "tag_id"]
    assert t["authors_v"].primary_key == ()


def test_foreign_keys(shop: Shop) -> None:
    schema, t = load(shop)
    (fk,) = t["book"].foreign_keys
    assert fk.columns == ("author_id",)
    assert fk.ref_table == shop.name("author")
    assert fk.ref_columns == ("id",)
    assert fk.on_delete == "CASCADE"
    assert fk.target == t["author"].key
    assert schema.table(fk.target) is t["author"]

    assert len(t["book_tag"].foreign_keys) == 2
    assert {f.ref_table for f in t["book_tag"].foreign_keys} == {
        shop.name("book"),
        shop.name("tag"),
    }


def test_composite_foreign_key(shop: Shop) -> None:
    _, t = load(shop)
    (fk,) = t["note"].foreign_keys
    assert fk.columns == ("order_id", "line_no")
    assert fk.ref_columns == ("order_id", "line_no")
    assert fk.ref_table == shop.name("line")


def test_self_reference(shop: Shop) -> None:
    _, t = load(shop)
    (fk,) = t["node"].foreign_keys
    assert fk.target == t["node"].key
    assert fk.columns == ("parent_id",)


def test_unique_keys_and_indexes(shop: Shop) -> None:
    _, t = load(shop)
    assert ("email",) in t["author"].unique_keys
    assert t["author"].is_unique(("email",))
    assert t["author"].is_unique(("id", "name"))
    assert not t["author"].is_unique(("name",))
    assert t["profile"].is_unique(("author_id",))  # primary key
    title = [i for i in t["book"].indexes if i.columns == ("title",)]
    assert title
    assert not title[0].unique
    assert ("isbn",) in t["book"].unique_keys


def test_search_and_find(shop: Shop) -> None:
    schema, t = load(shop)
    assert schema.find(shop.name("book")) is t["book"]
    assert schema.find("no_such_table") is None
    hits = schema.search("isbn")
    assert t["book"] in hits
    assert t["author"] not in hits
    assert t["author"] in schema.search(shop.prefix)
    assert len(schema.search("")) == len(schema.tables)


def test_schema_metadata(shop: Shop) -> None:
    schema, _ = load(shop)
    assert schema.default_schema in schema.schemas
    assert schema.duration > 0
    assert all(isinstance(t.key, TableKey) for t in schema.tables)
    assert schema.table(TableKey("nope", "nope")) is None


def test_comments_and_row_estimates(shop: Shop) -> None:
    name = shop.target.name
    if name == "sqlite":
        pytest.skip("SQLite has no comments")
    quote = shop.target.quote
    author = quote(shop.name("author"))
    if name == "postgresql":
        shop.client.execute(f"COMMENT ON TABLE {author} IS 'People who write'")
        shop.client.execute(f"COMMENT ON COLUMN {author}.name IS 'Full name'")
    else:
        shop.client.execute(
            f"ALTER TABLE {author} COMMENT = 'People who write', "
            f"MODIFY name VARCHAR(100) NOT NULL COMMENT 'Full name'"
        )
    _, t = load(shop)
    assert t["author"].comment == "People who write"
    assert t["author"].column("name") is not None
    assert t["author"].column("name").comment == "Full name"  # type: ignore[union-attr]
    assert t["author"].column("email").comment in (None, "")  # type: ignore[union-attr]


def test_partial_and_expression_indexes_do_not_count_as_unique(shop: Shop) -> None:
    name = shop.target.name
    if name == "mysql":
        pytest.skip("MySQL has no partial indexes")
    quote = shop.target.quote
    book = quote(shop.name("book"))
    shop.client.execute(
        f"CREATE UNIQUE INDEX {quote(shop.name('book') + '_part')} ON {book} (published) "
        "WHERE published IS NOT NULL"
    )
    _, t = load(shop)
    assert not t["book"].is_unique(("published",))
    part = [i for i in t["book"].indexes if i.columns == ("published",)]
    assert part
    assert part[0].unique
    assert not part[0].covers_all_rows


def test_postgres_other_schema_and_cross_schema_foreign_key(shop: Shop) -> None:
    if shop.target.name != "postgresql":
        pytest.skip("PostgreSQL schemas")
    other = f"{shop.prefix}_s"
    author = shop.target.quote(shop.name("author"))
    try:
        shop.client.execute(f'CREATE SCHEMA "{other}"')
        shop.client.execute(
            f'CREATE TABLE "{other}".fan (id INTEGER PRIMARY KEY, author_id INTEGER '
            f"REFERENCES {author} (id))"
        )
        schema = introspect(shop.client)
        assert other in schema.schemas
        assert schema.qualify
        fan = schema.table(TableKey(other, "fan"))
        assert fan is not None
        (fk,) = fan.foreign_keys
        assert fk.ref_schema == schema.default_schema
        assert schema.table(fk.target) is schema.find(shop.name("author"))
        assert schema.find(f"{other}.fan") is fan
    finally:
        shop.client.execute(f'DROP SCHEMA IF EXISTS "{other}" CASCADE')


def test_sqlite_broken_view_does_not_hide_the_rest(shop: Shop) -> None:
    if shop.target.name != "sqlite":
        pytest.skip("SQLite only")
    shop.client.execute(f'CREATE VIEW "{shop.prefix}_broken" AS SELECT * FROM "{shop.prefix}_gone"')
    try:
        _, t = load(shop)
        assert t["author"].columns
    finally:
        shop.client.execute(f'DROP VIEW "{shop.prefix}_broken"')


def test_reference_qualifies_only_outside_the_default_schema(shop: Shop) -> None:
    schema, t = load(shop)
    dialect = shop.client.dialect
    plain = schema.reference(t["author"], dialect)
    assert plain == shop.name("author")  # generated names never need quoting
    other = Table("elsewhere", "Mixed Case")
    assert schema.reference(other, dialect) == (
        f"{dialect.quote_ident_if_needed('elsewhere')}.{dialect.quote_ident('Mixed Case')}"
    )


def test_row_estimates_exist_for_analysed_tables_and_never_for_views(shop: Shop) -> None:
    name = shop.target.name
    quote = shop.target.quote
    author = quote(shop.name("author"))
    for i in range(1, 51):
        shop.client.execute(f"INSERT INTO {author} (id, name) VALUES ({i}, 'n{i}')")
    if name == "postgresql":
        shop.client.execute(f"ANALYZE {author}")
    elif name == "mysql":
        shop.client.execute(f"ANALYZE TABLE {author}")
    _, t = load(shop)
    assert t["authors_v"].row_estimate is None
    if name == "sqlite":
        assert t["author"].row_estimate is None
    else:
        assert t["author"].row_estimate is not None
        assert 25 <= t["author"].row_estimate <= 100  # an estimate of 50
    assert t["tag"].row_estimate is None  # empty or never analysed: unknown, not "0 rows"
