"""The diagram as text another tool can read: Mermaid ``erDiagram`` and DBML.

Both are generated from an :class:`ErdModel`, so what is in the file is what the diagram shows
(the chosen schema, views included, only foreign keys whose both ends are drawn).
"""

from __future__ import annotations

import re
from enum import StrEnum

from ..schema import Column, Table, TableKey
from .model import Cardinality, ErdModel, Relation


class TextFormat(StrEnum):
    MERMAID = "mermaid"
    DBML = "dbml"

    @property
    def suffix(self) -> str:
        return {"mermaid": ".mmd", "dbml": ".dbml"}[self.value]


def export_text(model: ErdModel, fmt: TextFormat, *, qualify: bool | None = None) -> str:
    """The model in ``fmt``. ``qualify``: write ``schema.table`` (default: only if the diagram
    holds more than one schema)."""
    qualified = len({t.schema for t in model.tables}) > 1 if qualify is None else qualify
    if fmt is TextFormat.MERMAID:
        return to_mermaid(model, qualified)
    return to_dbml(model, qualified)


# ---------------------------------------------------------------------- Mermaid

_WORD = re.compile(r"[^A-Za-z0-9_]")
_TYPE_JUNK = re.compile(r"[^A-Za-z0-9_()\[\],-]")


def _mermaid_name(key: TableKey, qualified: bool) -> str:
    name = f"{key.schema}_{key.name}" if qualified else key.name
    cleaned = _WORD.sub("_", name)
    return cleaned if cleaned and not cleaned[0].isdigit() else f"t_{cleaned}"


def _mermaid_type(column: Column) -> str:
    text = _TYPE_JUNK.sub("_", column.type.strip().replace(" ", "_"))
    return text or "unknown"


def _mermaid_label(text: str) -> str:
    return text.replace('"', "'").replace("\n", " ")


def to_mermaid(model: ErdModel, qualified: bool = False) -> str:
    lines = ["erDiagram"]
    names: dict[TableKey, str] = {}
    used: set[str] = set()
    for table in model.tables:  # two schemas may collapse onto one name: keep them apart
        base = _mermaid_name(table.key, qualified)
        candidate, number = base, 2
        while candidate in used:
            candidate = f"{base}_{number}"
            number += 1
        used.add(candidate)
        names[table.key] = candidate
    for table in model.tables:
        lines.append(f"    {names[table.key]} {{")
        foreign = table.foreign_key_columns
        for column in table.columns:
            flags = []
            if column.primary_key or column.name in table.primary_key:
                flags.append("PK")
            if column.name in foreign:
                flags.append("FK")
            comment = f' "{_mermaid_label(column.comment)}"' if column.comment else ""
            parts = [_mermaid_type(column), _WORD.sub("_", column.name) or "col", *flags]
            lines.append("        " + " ".join(parts) + comment)
        lines.append("    }")
    for relation in model.relations:
        parent_end = "|o" if relation.optional else "||"
        child_end = "o|" if relation.cardinality is Cardinality.ONE_TO_ONE else "o{"
        label = relation.name or ", ".join(relation.columns)
        lines.append(
            f"    {names[relation.parent]} {parent_end}--{child_end} {names[relation.child]}"
            f' : "{_mermaid_label(label)}"'
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- DBML

_DBML_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dbml_ident(name: str) -> str:
    return name if _DBML_PLAIN.match(name) else '"' + name.replace('"', '\\"') + '"'


def _dbml_table(key: TableKey, qualified: bool) -> str:
    return (
        f"{_dbml_ident(key.schema)}.{_dbml_ident(key.name)}" if qualified else _dbml_ident(key.name)
    )


def _dbml_type(column: Column) -> str:
    text = column.type.strip() or "unknown"
    return text if _DBML_PLAIN.match(text) else '"' + text.replace('"', '\\"') + '"'


def _dbml_string(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _dbml_column(table: Table, column: Column) -> str:
    settings: list[str] = []
    if column.primary_key or column.name in table.primary_key:
        settings.append("pk")
    if not column.nullable and "pk" not in settings:
        settings.append("not null")
    if column.default is not None:
        settings.append("default: `" + column.default.replace("`", "'") + "`")
    if column.comment:
        settings.append("note: " + _dbml_string(column.comment))
    suffix = f" [{', '.join(settings)}]" if settings else ""
    return f"  {_dbml_ident(column.name)} {_dbml_type(column)}{suffix}"


def _dbml_columns(names: tuple[str, ...]) -> str:
    idents = [_dbml_ident(n) for n in names]
    return idents[0] if len(idents) == 1 else "(" + ", ".join(idents) + ")"


def _dbml_ref(relation: Relation, qualified: bool) -> str:
    arrow = "-" if relation.cardinality is Cardinality.ONE_TO_ONE else ">"
    child = f"{_dbml_table(relation.child, qualified)}.{_dbml_columns(relation.columns)}"
    parent = f"{_dbml_table(relation.parent, qualified)}.{_dbml_columns(relation.ref_columns)}"
    name = f" {_dbml_ident(relation.name)}" if relation.name else ""
    return f"Ref{name}: {child} {arrow} {parent}"


def to_dbml(model: ErdModel, qualified: bool = False) -> str:
    blocks: list[str] = []
    for table in model.tables:
        lines = [f"Table {_dbml_table(table.key, qualified)} {{"]
        lines += [_dbml_column(table, column) for column in table.columns]
        indexes = [i for i in table.indexes if not i.primary and i.columns]
        if indexes:
            lines += ["", "  indexes {"]
            for index in indexes:
                columns = _dbml_columns(index.columns)
                setting = (
                    f" [unique, name: {_dbml_string(index.name)}]"
                    if index.unique
                    else (f" [name: {_dbml_string(index.name)}]")
                )
                lines.append(f"    {columns}{setting}")
            lines.append("  }")
        note = []
        if table.is_view:
            note.append(f"{table.kind.value.replace('_', ' ')}")
        if table.comment:
            note.append(table.comment)
        if note:
            lines += ["", "  Note: " + _dbml_string(" — ".join(note))]
        lines.append("}")
        blocks.append("\n".join(lines))
    blocks += [_dbml_ref(relation, qualified) for relation in model.relations]
    return "\n\n".join(blocks) + "\n"
