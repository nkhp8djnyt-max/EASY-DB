"""SQLite: pragma table-valued functions joined to ``sqlite_master`` (one query per kind)."""

from __future__ import annotations

import time
from typing import Any

from ...db import DatabaseClient, DbError
from ...dialects import SQLITE, DialectId
from ..model import Column, DatabaseSchema, Index, TableKind
from .builder import Draft, Drafts, ForeignKeyParts, text

_USER_OBJECTS = "m.type IN ('table', 'view') AND m.name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"

_COLUMNS = (
    'SELECT m.name, p.name, p.type, p."notnull", p.dflt_value, p.pk '
    f"FROM sqlite_master m JOIN pragma_table_xinfo(m.name) p WHERE {_USER_OBJECTS} "
    "AND p.hidden IN (0, 2, 3) ORDER BY m.name, p.cid"
)
_FOREIGN_KEYS = (
    'SELECT m.name, f.id, f."table", f."from", f."to", f.on_update, f.on_delete '
    "FROM sqlite_master m JOIN pragma_foreign_key_list(m.name) f "
    "WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
    "ORDER BY m.name, f.id, f.seq"
)
_INDEXES = (
    'SELECT m.name, il.name, il."unique", il.origin, il.partial, ic.name '
    "FROM sqlite_master m JOIN pragma_index_list(m.name) il JOIN pragma_index_info(il.name) ic "
    "WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite\\_%' ESCAPE '\\' "
    "ORDER BY m.name, il.seq, ic.seqno"
)


def load(client: DatabaseClient) -> DatabaseSchema:
    started = time.perf_counter()
    drafts = Drafts()
    for name, kind in client.execute(
        f"SELECT m.name, m.type FROM sqlite_master m WHERE {_USER_OBJECTS} ORDER BY m.name"
    ).rows:
        drafts.add(Draft("main", name, TableKind.VIEW if kind == "view" else TableKind.TABLE))

    _columns(client, drafts)
    _foreign_keys(client, drafts)
    _indexes(client, drafts)
    return DatabaseSchema(
        dialect=DialectId.SQLITE,
        schemas=("main",),
        default_schema="main",
        tables=drafts.built(),
        duration=time.perf_counter() - started,
    )


def _columns(client: DatabaseClient, drafts: Drafts) -> None:
    rows: list[tuple[Any, ...]]
    try:
        rows = list(client.execute(_COLUMNS).rows)
    except DbError:
        # One broken view (it names a table that is gone) fails the whole joined query.
        rows = []
        for key in list(drafts):
            try:
                rows += [
                    (key.name, *row)
                    for row in client.execute(
                        f'SELECT name, type, "notnull", dflt_value, pk FROM '
                        f"pragma_table_xinfo({SQLITE.render_literal(key.name)}) "
                        "WHERE hidden IN (0, 2, 3) ORDER BY cid"
                    ).rows
                ]
            except DbError:
                continue
    pk: dict[str, list[tuple[int, str]]] = {}
    for table, column, type_, not_null, default, pk_position in rows:
        draft = drafts.at("main", table)
        if draft is None:
            continue
        draft.columns.append(Column(column, text(type_) or "", not not_null, text(default)))
        if pk_position:
            pk.setdefault(table, []).append((int(pk_position), column))
    for table, parts in pk.items():
        draft = drafts.at("main", table)
        if draft is not None:
            draft.primary_key = [name for _, name in sorted(parts)]
            # INTEGER PRIMARY KEY (rowid alias) can never be NULL whatever the declaration says.


def _foreign_keys(client: DatabaseClient, drafts: Drafts) -> None:
    grouped: dict[tuple[str, int], ForeignKeyParts] = {}
    for table, fk_id, ref_table, column, ref_column, on_update, on_delete in client.execute(
        _FOREIGN_KEYS
    ).rows:
        parts = grouped.setdefault(
            (table, fk_id),
            ForeignKeyParts(
                None,
                "main",
                ref_table,
                on_delete if on_delete != "NO ACTION" else None,
                on_update if on_update != "NO ACTION" else None,
            ),
        )
        parts.columns.append(column)
        parts.ref_columns.append(ref_column)
    for (table, _), parts in grouped.items():
        draft = drafts.at("main", table)
        if draft is None:
            continue
        if any(c is None for c in parts.ref_columns):  # "REFERENCES parent" = the parent's PK
            parent = drafts.at("main", parts.ref_table)
            parts.ref_columns = list(parent.primary_key) if parent is not None else []
        draft.foreign_keys.append(parts.build())


def _indexes(client: DatabaseClient, drafts: Drafts) -> None:
    grouped: dict[tuple[str, str], tuple[bool, bool, str, list[str | None]]] = {}
    for table, name, unique, origin, partial, column in client.execute(_INDEXES).rows:
        _, _, _, columns = grouped.setdefault(
            (table, name), (bool(unique), bool(partial), origin, [])
        )
        columns.append(column)
    for (table, name), (unique, partial, origin, columns) in grouped.items():
        draft = drafts.at("main", table)
        if draft is None or origin == "pk":
            continue
        plain = tuple(c for c in columns if c is not None)
        draft.indexes.append(
            Index(
                name,
                plain,
                unique=unique,
                covers_all_rows=not partial and len(plain) == len(columns),
            )
        )
