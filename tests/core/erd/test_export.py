"""Mermaid and DBML text of the diagram."""

from __future__ import annotations

import re

from easydbms.core.dialects import DialectId
from easydbms.core.erd import TextFormat, build_erd, export_text, to_dbml, to_mermaid
from easydbms.core.schema import (
    Column,
    DatabaseSchema,
    ForeignKey,
    Index,
    Table,
    TableKind,
)

from .test_model import col, fk, schema_of, shop, table


def test_mermaid_lists_entities_keys_and_relations() -> None:
    text = to_mermaid(build_erd(shop()))
    assert text.startswith("erDiagram\n")
    assert "    author {\n        integer id PK\n        integer name\n    }" in text
    assert "        integer author_id FK" in text
    # a not-null foreign key: every book has an author; the nullable one is optional
    assert 'author ||--o{ book : "author_id"' in text
    assert 'author |o--o{ book : "editor_id"' in text


def test_a_unique_foreign_key_is_one_to_one() -> None:
    schema = schema_of(
        table("person", [col("id", pk=True)], ("id",)),
        table(
            "passport",
            [col("id", pk=True), col("person_id", nullable=False)],
            ("id",),
            [fk(("person_id",), "person")],
            [Index("passport_person", ("person_id",), unique=True)],
        ),
    )
    assert 'person ||--o| passport : "person_id"' in to_mermaid(build_erd(schema))


def test_awkward_names_and_types_are_made_safe_for_mermaid() -> None:
    odd = Table(
        "public",
        "order items",
        TableKind.TABLE,
        (
            Column("id", "integer", nullable=False, primary_key=True),
            Column("created at", "timestamp without time zone", comment='the "when"'),
            Column("price", "numeric(10,2)"),
        ),
        ("id",),
    )
    schema = DatabaseSchema(DialectId.POSTGRESQL, ("public",), "public", (odd,))
    text = to_mermaid(build_erd(schema))
    assert "order_items {" in text
    assert "timestamp_without_time_zone created_at \"the 'when'\"" in text
    assert "numeric(10,2) price" in text
    assert re.search(r"^\s+\S+ \S+( PK| FK)*( \".*\")?$", text.splitlines()[2])


def test_names_that_start_with_a_digit_and_equal_names_in_two_schemas() -> None:
    a = table("2024", [col("id", pk=True)], ("id",), schema="one")
    b = table("2024", [col("id", pk=True)], ("id",), schema="two")
    text = export_text(build_erd(schema_of(a, b)), TextFormat.MERMAID)
    assert "one_2024 {" in text
    assert "two_2024 {" in text
    plain = export_text(build_erd(schema_of(a)), TextFormat.MERMAID)
    assert "t_2024 {" in plain
    collapsed = to_mermaid(build_erd(schema_of(a, b)), qualified=False)
    assert "t_2024 {" in collapsed
    assert "t_2024_2 {" in collapsed


def test_dbml_has_tables_columns_settings_indexes_and_refs() -> None:
    parent = Table(
        "public",
        "author",
        TableKind.TABLE,
        (
            Column(
                "id",
                "integer",
                nullable=False,
                default="nextval('author_id_seq')",
                primary_key=True,
            ),
            Column("name", "character varying(80)", nullable=False, comment="Full name"),
            Column("email", "text"),
        ),
        ("id",),
        (),
        (
            Index("author_pkey", ("id",), unique=True, primary=True),
            Index("author_email", ("email",), unique=True),
            Index("author_name", ("name", "email")),
        ),
        comment="People who write",
    )
    child = Table(
        "public",
        "book",
        TableKind.TABLE,
        (Column("id", "integer", nullable=False, primary_key=True), Column("author_id", "integer")),
        ("id",),
        (ForeignKey("book_author_fk", ("author_id",), "public", "author", ("id",)),),
    )
    schema = DatabaseSchema(DialectId.POSTGRESQL, ("public",), "public", (parent, child))
    text = to_dbml(build_erd(schema))
    assert "Table author {" in text
    assert "  id integer [pk, default: `nextval('author_id_seq')`]" in text
    assert "  name \"character varying(80)\" [not null, note: 'Full name']" in text
    assert "    email [unique, name: 'author_email']" in text
    assert "    (name, email) [name: 'author_name']" in text
    assert "author_pkey" not in text  # the primary key is already marked on its column
    assert "  Note: 'People who write'" in text
    assert "Ref book_author_fk: book.author_id > author.id" in text


def test_dbml_one_to_one_composite_keys_views_and_qualified_names() -> None:
    a = Table(
        "s1",
        "left side",
        TableKind.TABLE,
        (Column("a", "int", nullable=False), Column("b", "int", nullable=False)),
        ("a", "b"),
    )
    b = Table(
        "s2",
        "right",
        TableKind.TABLE,
        (Column("a", "int"), Column("b", "int")),
        (),
        (ForeignKey(None, ("a", "b"), "s1", "left side", ("a", "b")),),
        (Index("right_ab", ("a", "b"), unique=True),),
    )
    v = Table("s2", "summary", TableKind.MATERIALIZED_VIEW, (Column("n", "bigint"),), ())
    schema = DatabaseSchema(DialectId.POSTGRESQL, ("s1", "s2"), "s1", (a, b, v))
    text = export_text(build_erd(schema), TextFormat.DBML)
    assert 'Table s1."left side" {' in text
    assert "Table s2.right {" in text
    assert "  Note: 'materialized view'" in text
    assert 'Ref: s2.right.(a, b) - s1."left side".(a, b)' in text
    unqualified = export_text(build_erd(schema), TextFormat.DBML, qualify=False)
    assert "Table right {" in unqualified


def test_an_empty_diagram_exports_to_a_valid_empty_document() -> None:
    model = build_erd(schema_of(table("t", [col("id")])))
    empty = type(model)((), ())
    assert export_text(empty, TextFormat.MERMAID) == "erDiagram\n"
    assert export_text(empty, TextFormat.DBML) == "\n"
    assert TextFormat.MERMAID.suffix == ".mmd"
    assert TextFormat.DBML.suffix == ".dbml"
