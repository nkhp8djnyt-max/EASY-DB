"""Case-insensitive lookups over an introspected schema (built once per schema snapshot)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ..schema import DatabaseSchema, ForeignKey, Table, TableKey


class SchemaIndex:
    """What autocomplete asks of a schema: find a table by a possibly qualified, any-case name,
    list the schemas, and walk the foreign-key graph."""

    def __init__(self, schema: DatabaseSchema) -> None:
        self.schema = schema
        self._by_name: dict[str, list[Table]] = defaultdict(list)
        self._by_qualified: dict[tuple[str, str], Table] = {}
        self._schemas = {name.casefold(): name for name in schema.schemas}
        for table in schema.tables:
            self._by_name[table.name.casefold()].append(table)
            self._by_qualified[(table.schema.casefold(), table.name.casefold())] = table
        #: tables joined to a table by a foreign key in either direction
        self._neighbors: dict[TableKey, set[TableKey]] = defaultdict(set)
        known = {t.key for t in schema.tables}
        for table in schema.tables:
            for fk in table.foreign_keys:
                if fk.target in known:
                    self._neighbors[table.key].add(fk.target)
                    self._neighbors[fk.target].add(table.key)

    # ------------------------------------------------------------------ lookup

    def table(self, parts: Sequence[str]) -> Table | None:
        """``name``, ``schema.name`` or ``db.schema.name`` (the last two parts count)."""
        if not parts:
            return None
        name = parts[-1].casefold()
        if len(parts) >= 2:
            return self._by_qualified.get((parts[-2].casefold(), name))
        candidates = self._by_name.get(name, [])
        for table in candidates:
            if table.schema == self.schema.default_schema:
                return table
        return candidates[0] if candidates else None

    def schema_name(self, name: str) -> str | None:
        """The schema called ``name`` in any case, or ``None``."""
        return self._schemas.get(name.casefold())

    def tables_of(self, schema_name: str) -> list[Table]:
        return [t for t in self.schema.tables if t.schema == schema_name]

    # ------------------------------------------------------------------ foreign keys

    def is_related(self, a: Table, b: Table) -> bool:
        return b.key in self._neighbors.get(a.key, ())

    def related_to(self, tables: Sequence[Table]) -> set[TableKey]:
        """Keys of every table joined by a foreign key to at least one of ``tables``."""
        found: set[TableKey] = set()
        for table in tables:
            found |= self._neighbors.get(table.key, set())
        return found

    @staticmethod
    def foreign_keys_between(a: Table, b: Table) -> list[tuple[ForeignKey, Table]]:
        """Foreign keys linking ``a`` and ``b``, each with the table that holds it."""
        links = [(fk, a) for fk in a.foreign_keys if fk.target == b.key]
        links += [(fk, b) for fk in b.foreign_keys if fk.target == a.key and a is not b]
        return links
