"""Editing result rows: change sets, SQL generation, value parsing (no Qt, no database access)."""

from .changeset import ChangeSet, Delete, Insert, Update
from .keys import EditKeyStore
from .sql_builder import PlannedStatement, bind_value, build_statements, preview_script, row_label
from .target import (
    EditTarget,
    KeySource,
    ReadOnly,
    ReadOnlyReason,
    RowKey,
    TargetColumn,
    choose_key,
    target_for_query,
    target_for_table,
)
from .values import ValueParseError, edit_text, is_editable, parse_value, same_value

__all__ = [
    "ChangeSet",
    "Delete",
    "EditKeyStore",
    "EditTarget",
    "Insert",
    "KeySource",
    "PlannedStatement",
    "ReadOnly",
    "ReadOnlyReason",
    "RowKey",
    "TargetColumn",
    "Update",
    "ValueParseError",
    "bind_value",
    "build_statements",
    "choose_key",
    "edit_text",
    "is_editable",
    "parse_value",
    "preview_script",
    "row_label",
    "same_value",
    "target_for_query",
    "target_for_table",
]
