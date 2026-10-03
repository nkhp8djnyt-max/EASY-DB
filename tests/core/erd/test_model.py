from __future__ import annotations

from easydbms.core.dialects import DialectId
from easydbms.core.erd import Cardinality, build_erd, is_junction
from easydbms.core.schema import (
    Column,
    DatabaseSchema,
    ForeignKey,
    Index,
    Table,
    TableKey,
    TableKind,
)


def col(name: str, *, nullable: bool = True, pk: bool = False) -> Column:
    return Column(name, "integer", nullable=nullable and not pk, primary_key=pk)


def table(
    name: str,
    columns: list[Column],
    pk: tuple[str, ...] = (),
    fks: list[ForeignKey] | None = None,
    indexes: list[Index] | None = None,
    schema: str = "main",
    kind: TableKind = TableKind.TABLE,
) -> Table:
    return Table(
        schema,
        name,
        kind,
        tuple(columns),
        pk,
        tuple(fks or ()),
        tuple(indexes or ()),
    )


def fk(
    columns: tuple[str, ...], ref: str, ref_columns: tuple[str, ...] = ("id",), schema: str = "main"
) -> ForeignKey:
    return ForeignKey(None, columns, schema, ref, ref_columns)


def schema_of(*tables: Table) -> DatabaseSchema:
    names = tuple(sorted({t.schema for t in tables}))
    return DatabaseSchema(DialectId.SQLITE, names, names[0], tuple(tables))


def shop() -> DatabaseSchema:
    return schema_of(
        table("author", [col("id", pk=True), col("name")], ("id",)),
        table(
            "book",
            [col("id", pk=True), col("author_id", nullable=False), col("editor_id")],
            ("id",),
            [fk(("author_id",), "author"), fk(("editor_id",), "author")],
        ),
        table("tag", [col("id", pk=True)], ("id",)),
        table(
            "book_tag",
            [col("book_id", nullable=False), col("tag_id", nullable=False)],
            ("book_id", "tag_id"),
            [fk(("book_id",), "book"), fk(("tag_id",), "tag")],
        ),
        table(
            "profile",
            [col("author_id", pk=True), col("bio")],
            ("author_id",),
            [fk(("author_id",), "author")],
        ),
        table(
            "node",
            [col("id", pk=True), col("parent_id")],
            ("id",),
            [fk(("parent_id",), "node")],
        ),
        table("lonely", [col("id", pk=True)], ("id",)),
    )


def relation(model, child: str, columns: tuple[str, ...]):  # type: ignore[no-untyped-def]
    (found,) = [r for r in model.relations if r.child.name == child and r.columns == columns]
    return found


def test_cardinality_from_uniqueness() -> None:
    model = build_erd(shop())
    assert relation(model, "book", ("author_id",)).cardinality is Cardinality.ONE_TO_MANY
    # the foreign key is the primary key: at most one profile per author
    assert relation(model, "profile", ("author_id",)).cardinality is Cardinality.ONE_TO_ONE


def test_unique_index_makes_it_one_to_one() -> None:
    schema = schema_of(
        table("a", [col("id", pk=True)], ("id",)),
        table(
            "b",
            [col("id", pk=True), col("a_id")],
            ("id",),
            [fk(("a_id",), "a")],
            [Index("b_a", ("a_id",), unique=True)],
        ),
        table(
            "c",
            [col("id", pk=True), col("a_id")],
            ("id",),
            [fk(("a_id",), "a")],
            [Index("c_a", ("a_id",), unique=True, covers_all_rows=False)],  # partial: not unique
        ),
    )
    model = build_erd(schema)
    assert relation(model, "b", ("a_id",)).cardinality is Cardinality.ONE_TO_ONE
    assert relation(model, "c", ("a_id",)).cardinality is Cardinality.ONE_TO_MANY


def test_optional_follows_nullability() -> None:
    model = build_erd(shop())
    assert not relation(model, "book", ("author_id",)).optional
    assert relation(model, "book", ("editor_id",)).optional


def test_self_reference() -> None:
    model = build_erd(shop())
    node = relation(model, "node", ("parent_id",))
    assert node.self_reference
    assert node.optional


def test_junction_detection_and_many_to_many() -> None:
    model = build_erd(shop())
    assert {k.name for k in model.junctions} == {"book_tag"}
    assert [(a.name, b.name, v.name) for a, b, v in model.many_to_many()] == [
        ("book", "tag", "book_tag")
    ]


def test_junction_needs_a_pk_made_of_foreign_keys() -> None:
    surrogate = table(
        "link",
        [col("id", pk=True), col("a_id"), col("b_id")],
        ("id",),
        [fk(("a_id",), "a"), fk(("b_id",), "b")],
    )
    assert not is_junction(surrogate)
    keyless = table(
        "pair",
        [col("a_id"), col("b_id")],
        (),
        [fk(("a_id",), "a"), fk(("b_id",), "b")],
    )
    assert is_junction(keyless)
    with_payload = table(
        "item",
        [col("order_id", nullable=False), col("product_id", nullable=False), col("qty")],
        ("order_id", "product_id"),
        [fk(("order_id",), "o"), fk(("product_id",), "p")],
    )
    assert is_junction(with_payload)
    one_fk = table("x", [col("a_id", pk=True)], ("a_id",), [fk(("a_id",), "a")])
    assert not is_junction(one_fk)
    view = table("v", [col("a")], kind=TableKind.VIEW)
    assert not is_junction(view)


def test_neighbors_and_relations_of() -> None:
    model = build_erd(shop())
    author = TableKey("main", "author")
    assert {k.name for k in model.neighbors(author)} == {"book", "profile"}
    assert {k.name for k in model.neighbors(TableKey("main", "node"))} == set()
    assert len(model.relations_of(author)) == 3
    assert model.neighbors(TableKey("main", "lonely")) == set()


def test_scope_limits_tables_and_drops_foreign_edges() -> None:
    other = table(
        "fan",
        [col("id", pk=True), col("author_id")],
        ("id",),
        [fk(("author_id",), "author")],
        schema="s2",
    )
    base = shop()
    schema = DatabaseSchema(DialectId.POSTGRESQL, ("main", "s2"), "main", (*base.tables, other))
    everything = build_erd(schema)
    assert len(everything.tables) == len(base.tables) + 1
    assert any(
        r.child.schema == "s2" for r in everything.relations
    )  # fan -> author is not resolved
    scoped = build_erd(schema, "main")
    assert all(t.schema == "main" for t in scoped.tables)
    assert not any(r.child.schema == "s2" for r in scoped.relations)
    only_s2 = build_erd(schema, "s2")
    assert [t.name for t in only_s2.tables] == ["fan"]
    assert only_s2.relations == ()
