"""MySQL / MariaDB: bulk ``information_schema`` queries for the current database."""

from __future__ import annotations

import time

from ...db import DatabaseClient
from ...dialects import MYSQL, DialectId
from ..model import Column, DatabaseSchema, Index, TableKind
from .builder import Draft, Drafts, ForeignKeyParts, text

_SYSTEM_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")


def load(client: DatabaseClient) -> DatabaseSchema:
    started = time.perf_counter()
    current = client.execute("SELECT DATABASE()").rows[0][0]
    if current:
        schemas = [str(current)]
    else:  # no database selected: show every user database
        listed = client.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA").rows
        schemas = sorted(str(r[0]) for r in listed if str(r[0]).lower() not in _SYSTEM_SCHEMAS)
    if not schemas:
        return DatabaseSchema(DialectId.MYSQL, (), "", (), time.perf_counter() - started)
    scope = ", ".join(MYSQL.render_literal(s) for s in schemas)

    drafts = Drafts()
    for schema, name, kind, comment, rows in client.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT, TABLE_ROWS "
        f"FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ({scope}) "
        "AND TABLE_TYPE IN ('BASE TABLE', 'VIEW', 'SYSTEM VERSIONED')"
    ).rows:
        drafts.add(
            Draft(
                schema=str(schema),
                name=str(name),
                kind=TableKind.VIEW if kind == "VIEW" else TableKind.TABLE,
                comment=text(comment),
                row_estimate=int(rows)
                if rows is not None and kind != "VIEW" and int(rows) > 0
                else None,
            )
        )

    for schema, table, column, type_, nullable, default, comment in client.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
        "COLUMN_COMMENT FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA IN ({scope}) ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
    ).rows:
        draft = drafts.at(str(schema), str(table))
        if draft is None:
            continue
        draft.columns.append(
            Column(
                str(column),
                text(type_) or "",
                str(nullable).upper() == "YES",
                text(default),
                text(comment) or None,
            )
        )

    # Joining KEY_COLUMN_USAGE to REFERENTIAL_CONSTRAINTS inside the server is ~40x slower than
    # reading both and joining here (information_schema has no useful indexes).
    rules: dict[tuple[str, str, str], tuple[str | None, str | None]] = {}
    for schema, table, name, on_delete, on_update in client.execute(
        "SELECT CONSTRAINT_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, DELETE_RULE, UPDATE_RULE "
        f"FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA IN ({scope})"
    ).rows:
        rules[(str(schema), str(table), str(name))] = (text(on_delete), text(on_update))
    foreign: dict[tuple[str, str, str], ForeignKeyParts] = {}
    for schema, table, name, column, ref_schema, ref_table, ref_column in client.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, "
        "REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        f"WHERE TABLE_SCHEMA IN ({scope}) AND REFERENCED_TABLE_NAME IS NOT NULL "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION"
    ).rows:
        entry_key = (str(schema), str(table), str(name))
        on_delete, on_update = rules.get(entry_key, (None, None))
        parts = foreign.setdefault(
            entry_key,
            ForeignKeyParts(str(name), str(ref_schema), str(ref_table), on_delete, on_update),
        )
        parts.columns.append(str(column))
        parts.ref_columns.append(text(ref_column))
    for (schema, table, _), parts in foreign.items():
        draft = drafts.at(schema, table)
        if draft is not None:
            draft.foreign_keys.append(parts.build())

    index_parts: dict[tuple[str, str, str], tuple[bool, list[str | None]]] = {}
    for schema, table, name, non_unique, column in client.execute(
        "SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME "
        f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA IN ({scope}) "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
    ).rows:
        entry_key = (str(schema), str(table), str(name))
        unique, columns = index_parts.setdefault(entry_key, (not int(non_unique), []))
        columns.append(text(column))  # a NULL column is a functional (expression) key part
    for (schema, table, name), (unique, columns) in index_parts.items():
        draft = drafts.at(schema, table)
        if draft is None:
            continue
        plain = tuple(c for c in columns if c is not None)
        if name == "PRIMARY":  # the key's own column order (COLUMN_KEY follows column order)
            draft.primary_key = list(plain)
            continue
        draft.indexes.append(
            Index(name, plain, unique=unique, covers_all_rows=len(plain) == len(columns))
        )

    return DatabaseSchema(
        dialect=DialectId.MYSQL,
        schemas=tuple(schemas),
        default_schema=schemas[0],
        tables=drafts.built(),
        duration=time.perf_counter() - started,
    )
