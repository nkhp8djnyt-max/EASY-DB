"""From an introspected schema to the diagram: relations with cardinality, junction tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..schema import DatabaseSchema, ForeignKey, Table, TableKey


class Cardinality(StrEnum):
    #: Each parent row has any number of child rows (the foreign key is not unique).
    ONE_TO_MANY = "1:N"
    #: Each parent row has at most one child row (the foreign key columns are unique).
    ONE_TO_ONE = "1:1"


@dataclass(frozen=True, slots=True)
class Relation:
    """One foreign key, seen as an edge from the referencing (child) to the referenced table."""

    child: TableKey
    parent: TableKey
    name: str | None
    columns: tuple[str, ...]
    ref_columns: tuple[str, ...]
    cardinality: Cardinality
    #: The foreign key may be NULL, so a child need not have a parent.
    optional: bool

    @property
    def self_reference(self) -> bool:
        return self.child == self.parent


@dataclass(frozen=True)
class ErdModel:
    tables: tuple[Table, ...]
    relations: tuple[Relation, ...]
    #: Tables that exist only to link two others (a many-to-many through a middle table).
    junctions: frozenset[TableKey] = frozenset()
    _by_key: dict[TableKey, Table] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self._by_key:
            self._by_key.update({t.key: t for t in self.tables})

    def table(self, key: TableKey) -> Table | None:
        return self._by_key.get(key)

    def neighbors(self, key: TableKey) -> set[TableKey]:
        """Tables directly connected to ``key`` by a foreign key, in either direction."""
        found: set[TableKey] = set()
        for relation in self.relations:
            if relation.child == key:
                found.add(relation.parent)
            elif relation.parent == key:
                found.add(relation.child)
        found.discard(key)
        return found

    def relations_of(self, key: TableKey) -> list[Relation]:
        return [r for r in self.relations if key in (r.child, r.parent)]

    def many_to_many(self) -> list[tuple[TableKey, TableKey, TableKey]]:
        """``(a, b, via)`` for every pair of tables linked through a junction table."""
        links = []
        for via in sorted(self.junctions):
            targets = sorted({r.parent for r in self.relations if r.child == via})
            for i, a in enumerate(targets):
                for b in targets[i + 1 :]:
                    links.append((a, b, via))
        return links


def build_erd(schema: DatabaseSchema, scope: str | None = None) -> ErdModel:
    """The diagram of ``scope`` (one schema) or of every schema when ``scope`` is ``None``.

    A foreign key to a table outside the scope has no edge to draw and is left out.
    """
    tables = tuple(schema.tables_in(scope))
    keys = {t.key for t in tables}
    relations: list[Relation] = []
    for table in tables:
        for fk in table.foreign_keys:
            if fk.target in keys:
                relations.append(_relation(table, fk, schema))
    junctions = frozenset(t.key for t in tables if is_junction(t))
    return ErdModel(tables, tuple(relations), junctions)


def _relation(table: Table, fk: ForeignKey, schema: DatabaseSchema) -> Relation:
    nullable = any(
        (column := table.column(name)) is not None and column.nullable for name in fk.columns
    )
    return Relation(
        child=table.key,
        parent=fk.target,
        name=fk.name,
        columns=fk.columns,
        ref_columns=fk.ref_columns,
        cardinality=(
            Cardinality.ONE_TO_ONE if table.is_unique(fk.columns) else Cardinality.ONE_TO_MANY
        ),
        optional=nullable,
    )


def is_junction(table: Table) -> bool:
    """A table whose primary key is made of foreign-key columns of at least two foreign keys.

    ``order_items(order_id, product_id, quantity, PRIMARY KEY (order_id, product_id))`` is one;
    a table with its own surrogate ``id`` is treated as an ordinary entity.
    """
    if table.is_view or len(table.foreign_keys) < 2:
        return False
    fk_columns = table.foreign_key_columns
    if table.primary_key:
        covered = [fk for fk in table.foreign_keys if set(fk.columns) <= set(table.primary_key)]
        return set(table.primary_key) <= fk_columns and len(covered) >= 2
    return {c.name for c in table.columns} <= fk_columns
