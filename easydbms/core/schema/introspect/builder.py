"""Shared assembly of :class:`Table` objects from the rows each catalog returns."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..model import Column, ForeignKey, Index, Table, TableKey, TableKind


@dataclass
class Draft:
    schema: str
    name: str
    kind: TableKind = TableKind.TABLE
    comment: str | None = None
    row_estimate: int | None = None
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)

    def build(self) -> Table:
        keys = set(self.primary_key)
        columns = tuple(
            replace(column, primary_key=True) if column.name in keys else column
            for column in self.columns
        )
        return Table(
            schema=self.schema,
            name=self.name,
            kind=self.kind,
            columns=columns,
            primary_key=tuple(self.primary_key),
            foreign_keys=tuple(self.foreign_keys),
            indexes=tuple(self.indexes),
            comment=self.comment or None,
            row_estimate=self.row_estimate,
        )


@dataclass
class ForeignKeyParts:
    """A foreign key while its (possibly multi-column) rows are still arriving."""

    name: str | None
    ref_schema: str
    ref_table: str
    on_delete: str | None = None
    on_update: str | None = None
    columns: list[str] = field(default_factory=list)
    ref_columns: list[str | None] = field(default_factory=list)

    def build(self) -> ForeignKey:
        return ForeignKey(
            self.name,
            tuple(self.columns),
            self.ref_schema,
            self.ref_table,
            tuple(c for c in self.ref_columns if c is not None),
            self.on_delete,
            self.on_update,
        )


class Drafts(dict[TableKey, Draft]):
    """Drafts by key; rows that name a table the catalog did not list (a race) are ignored."""

    def add(self, draft: Draft) -> None:
        self[TableKey(draft.schema, draft.name)] = draft

    def at(self, schema: str, name: str) -> Draft | None:
        return self.get(TableKey(schema, name))

    def built(self) -> tuple[Table, ...]:
        return tuple(draft.build() for _, draft in sorted(self.items(), key=lambda item: item[0]))


def text(value: object) -> str | None:
    """A catalog value as ``str`` (some drivers hand back ``bytes`` for information_schema)."""
    if value is None:
        return None
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def names(value: object) -> tuple[str, ...]:
    """A catalog array / list of names as a tuple of ``str``."""
    if value is None:
        return ()
    return tuple(str(item) for item in value)  # type: ignore[attr-defined]
