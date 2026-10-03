"""PostgreSQL: three bulk ``pg_catalog`` queries (columns, constraints, indexes) for all tables."""

from __future__ import annotations

import time

from ...db import DatabaseClient
from ...dialects import DialectId
from ..model import Column, DatabaseSchema, ForeignKey, Index, TableKind
from .builder import Draft, Drafts, names, text

_SYSTEM = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'"
)

_KINDS = {
    "r": TableKind.TABLE,
    "p": TableKind.TABLE,
    "f": TableKind.TABLE,
    "v": TableKind.VIEW,
    "m": TableKind.MATERIALIZED_VIEW,
}
_ACTIONS = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}

_COLUMNS = f"""
SELECT n.nspname, c.relname, c.relkind, obj_description(c.oid, 'pg_class'), c.reltuples::bigint,
       a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid), col_description(c.oid, a.attnum)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE c.relkind IN ('r', 'p', 'f', 'v', 'm') AND NOT c.relispartition AND {_SYSTEM}
ORDER BY n.nspname, c.relname, a.attnum
"""

_CONSTRAINTS = f"""
SELECT n.nspname, c.relname, con.conname, con.contype::text,
       (SELECT array_agg(a.attname ORDER BY k.ord)
          FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum),
       fn.nspname, fc.relname,
       (SELECT array_agg(a.attname ORDER BY k.ord)
          FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum),
       con.confdeltype::text, con.confupdtype::text
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_class fc ON fc.oid = con.confrelid
LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace
WHERE con.contype IN ('p', 'f', 'u') AND NOT c.relispartition AND {_SYSTEM}
ORDER BY n.nspname, c.relname, con.conname
"""

_INDEXES = f"""
SELECT n.nspname, t.relname, i.relname, ix.indisunique, ix.indisprimary,
       (SELECT array_agg(a.attname ORDER BY k.ord)
          FROM unnest(ix.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
         WHERE k.attnum > 0 AND k.ord <= ix.indnkeyatts),
       ix.indnkeyatts, ix.indpred IS NOT NULL
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE ix.indisvalid AND NOT t.relispartition AND {_SYSTEM}
ORDER BY n.nspname, t.relname, i.relname
"""


def load(client: DatabaseClient) -> DatabaseSchema:
    started = time.perf_counter()
    drafts = Drafts()
    for row in client.execute(_COLUMNS).rows:
        schema, name, relkind, comment, estimate, column, type_, nullable, default, remark = row
        draft = drafts.at(schema, name)
        if draft is None:
            draft = Draft(
                schema=schema,
                name=name,
                kind=_KINDS.get(relkind, TableKind.TABLE),
                comment=text(comment),
                row_estimate=_estimate(relkind, estimate),
            )
            drafts.add(draft)
        if column is not None:  # a table without columns comes back as one row of NULLs
            draft.columns.append(Column(column, type_, bool(nullable), text(default), text(remark)))

    for (
        schema,
        table,
        name,
        kind,
        columns,
        ref_schema,
        ref_table,
        ref_columns,
        on_delete,
        on_update,
    ) in client.execute(_CONSTRAINTS).rows:
        draft = drafts.at(schema, table)
        if draft is None:
            continue
        if kind == "p":
            draft.primary_key = list(names(columns))
        elif kind == "u":
            draft.indexes.append(Index(name, names(columns), unique=True))
        elif kind == "f":
            draft.foreign_keys.append(
                ForeignKey(
                    name,
                    names(columns),
                    ref_schema,
                    ref_table,
                    names(ref_columns),
                    _ACTIONS.get(on_delete or ""),
                    _ACTIONS.get(on_update or ""),
                )
            )

    for schema, table, name, unique, primary, columns, key_count, partial in client.execute(
        _INDEXES
    ).rows:
        draft = drafts.at(schema, table)
        if draft is None:
            continue
        columns = names(columns)
        if not primary and not any(index.name == name for index in draft.indexes):
            # A unique constraint already appears as an index named like the constraint.
            draft.indexes.append(
                Index(
                    name,
                    columns,
                    unique=bool(unique),
                    covers_all_rows=not partial and len(columns) == key_count,
                )
            )

    schemas = sorted({key.schema for key in drafts})
    default = _default_schema(client, schemas)
    if default not in schemas:
        schemas.insert(0, default)
    return DatabaseSchema(
        dialect=DialectId.POSTGRESQL,
        schemas=tuple(schemas),
        default_schema=default,
        tables=drafts.built(),
        duration=time.perf_counter() - started,
    )


def _estimate(relkind: str, value: object) -> int | None:
    """The planner's row count, or ``None`` when unknown (views, never analysed: 0 or -1)."""
    if relkind == "v" or not isinstance(value, int) or value <= 0:
        return None
    return value


def _default_schema(client: DatabaseClient, schemas: list[str]) -> str:
    rows = client.execute("SELECT current_schema()").rows
    current = rows[0][0] if rows and rows[0][0] else None
    if current:
        return str(current)
    return "public" if "public" in schemas or not schemas else schemas[0]
