"""Edits made in a grid, kept until the user applies them (nothing reaches the database before).

Rows are identified by the values of the target's key columns *as loaded* (:data:`RowKey`), not by
their position, so changes survive sorting, filtering and paging. Three kinds of pending change:

* :class:`Update`  — new values of some cells of a loaded row (with the old ones, for the check
  that nobody else changed the row meanwhile);
* :class:`Delete`  — a loaded row marked for deletion;
* :class:`Insert`  — a new row; columns that were never set are left to the database default.

Every mutation is recorded as a ``(kind, id, before, after)`` step so undo / redo restore exactly
the previous state; several steps made together (deleting a selection) undo as one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .target import EditTarget, RowKey
from .values import same_value


@dataclass(frozen=True, slots=True)
class Update:
    key: RowKey
    #: ``column -> (value when loaded, new value)`` for every changed cell.
    changes: Mapping[str, tuple[Any, Any]]


@dataclass(frozen=True, slots=True)
class Delete:
    key: RowKey
    #: The loaded values (for the preview and tooltips).
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Insert:
    id: int
    #: Only the columns that were set (``None`` is an explicit NULL); the others use the default.
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _Step:
    kind: str  # "update" | "delete" | "insert"
    id: Any
    before: Any
    after: Any


@dataclass(slots=True)
class ChangeSet:
    target: EditTarget
    version: int = 0
    _updates: dict[RowKey, Update] = field(default_factory=dict)
    _deletes: dict[RowKey, Delete] = field(default_factory=dict)
    _inserts: dict[int, Insert] = field(default_factory=dict)
    _undo: list[tuple[_Step, ...]] = field(default_factory=list)
    _redo: list[tuple[_Step, ...]] = field(default_factory=list)
    _group: list[_Step] | None = None
    _next_insert: int = 1

    # ------------------------------------------------------------------ reading

    @property
    def updates(self) -> Mapping[RowKey, Update]:
        return self._updates

    @property
    def deletes(self) -> Mapping[RowKey, Delete]:
        return self._deletes

    @property
    def inserts(self) -> Mapping[int, Insert]:
        return self._inserts

    @property
    def count(self) -> int:
        """Number of statements the changes will run: changed rows + new rows + deleted rows."""
        return len(self._updates) + len(self._inserts) + len(self._deletes)

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    def edited_value(self, key: RowKey, column: str) -> tuple[bool, Any]:
        """``(changed, value)`` of a cell of a loaded row: the pending value when it was edited."""
        update = self._updates.get(key)
        if update is not None and column in update.changes:
            return True, update.changes[column][1]
        return False, None

    def is_deleted(self, key: RowKey) -> bool:
        return key in self._deletes

    # ------------------------------------------------------------------ editing loaded rows

    def set_cell(self, key: RowKey, original: Mapping[str, Any], column: str, value: Any) -> bool:
        """Set a cell of a loaded row; ``False`` when nothing changes (or the row is deleted)."""
        spec = self.target.by_name(column)
        if spec is None or not spec.editable or key in self._deletes:
            return False
        before = self._updates.get(key)
        changes = dict(before.changes) if before is not None else {}
        old = original[column]
        if same_value(spec.kind, old, value):
            if column not in changes:
                return False
            del changes[column]
        else:
            if column in changes and same_value(spec.kind, changes[column][1], value):
                return False
            changes[column] = (changes[column][0] if column in changes else old, value)
        after = Update(key, changes) if changes else None
        self._record("update", key, before, after)
        return True

    def delete_row(self, key: RowKey, original: Mapping[str, Any]) -> bool:
        """Mark a loaded row for deletion; its pending edits are dropped (they would be moot)."""
        if key in self._deletes:
            return False
        with self.group():
            update = self._updates.get(key)
            if update is not None:
                self._record("update", key, update, None)
            self._record("delete", key, None, Delete(key, dict(original)))
        return True

    def restore_row(self, key: RowKey) -> bool:
        """Unmark a row marked for deletion."""
        before = self._deletes.get(key)
        if before is None:
            return False
        self._record("delete", key, before, None)
        return True

    def revert_cell(self, key: RowKey, column: str) -> bool:
        before = self._updates.get(key)
        if before is None or column not in before.changes:
            return False
        changes = {name: pair for name, pair in before.changes.items() if name != column}
        self._record("update", key, before, Update(key, changes) if changes else None)
        return True

    def revert_row(self, key: RowKey) -> bool:
        """Drop every pending change of a loaded row (its edits and its deletion mark)."""
        with self.group():
            changed = self.restore_row(key)
            before = self._updates.get(key)
            if before is not None:
                self._record("update", key, before, None)
                changed = True
        return changed

    # ------------------------------------------------------------------ new rows

    def add_row(self, values: Mapping[str, Any] | None = None) -> int:
        """Add a new row (``values`` are the columns that are set); returns its id."""
        new_id = self._next_insert
        self._next_insert += 1
        self._record("insert", new_id, None, Insert(new_id, dict(values or {})))
        return new_id

    def set_new_cell(self, new_id: int, column: str, value: Any) -> bool:
        spec = self.target.by_name(column)
        before = self._inserts.get(new_id)
        if spec is None or not spec.editable or before is None:
            return False
        if column in before.values and same_value(spec.kind, before.values[column], value):
            return False
        self._record("insert", new_id, before, Insert(new_id, {**before.values, column: value}))
        return True

    def unset_new_cell(self, new_id: int, column: str) -> bool:
        """Back to "use the database default" for a cell of a new row."""
        before = self._inserts.get(new_id)
        if before is None or column not in before.values:
            return False
        values = {name: value for name, value in before.values.items() if name != column}
        self._record("insert", new_id, before, Insert(new_id, values))
        return True

    def remove_new(self, new_id: int) -> bool:
        before = self._inserts.get(new_id)
        if before is None:
            return False
        self._record("insert", new_id, before, None)
        return True

    # ------------------------------------------------------------------ history

    @contextmanager
    def group(self) -> Iterator[None]:
        """Steps made inside the block undo and redo together."""
        if self._group is not None:  # nested: belongs to the outer group
            yield
            return
        self._group = []
        try:
            yield
        finally:
            steps, self._group = tuple(self._group), None
            if steps:
                self._undo.append(steps)
                self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        steps = self._undo.pop()
        for step in reversed(steps):
            self._put(step.kind, step.id, step.before)
        self._redo.append(steps)
        self.version += 1
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        steps = self._redo.pop()
        for step in steps:
            self._put(step.kind, step.id, step.after)
        self._undo.append(steps)
        self.version += 1
        return True

    def discard_all(self) -> bool:
        """Drop every pending change (can be undone)."""
        if self.is_empty:
            return False
        with self.group():
            for key, update in list(self._updates.items()):
                self._record("update", key, update, None)
            for key, delete in list(self._deletes.items()):
                self._record("delete", key, delete, None)
            for new_id, insert in list(self._inserts.items()):
                self._record("insert", new_id, insert, None)
        return True

    def reset(self) -> None:
        """Forget everything, history included (after the changes were applied)."""
        self._updates.clear()
        self._deletes.clear()
        self._inserts.clear()
        self._undo.clear()
        self._redo.clear()
        self.version += 1

    def rebase(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Take freshly loaded rows as the new "original" of the pending edits.

        After a reload the old values of an edited row may have changed on the server; this makes
        them the baseline (so a conflict is resolved in favour of the user's edit, which is what
        pressing *reload* and applying anyway means). Edits that now equal the server's value
        disappear. Returns how many rows were rebased. The history is dropped.
        """
        fresh = {key: row for row in rows if (key := self._key_of(row)) is not None}
        rebased = 0
        for key, update in list(self._updates.items()):
            row = fresh.get(key)
            if row is None:
                continue
            changes: dict[str, tuple[Any, Any]] = {}
            for column, (_old, new) in update.changes.items():
                spec = self.target.by_name(column)
                if spec is not None and same_value(spec.kind, row[column], new):
                    continue
                changes[column] = (row[column], new)
            if changes:
                self._updates[key] = Update(key, changes)
            else:
                del self._updates[key]
            rebased += 1
        self._undo.clear()
        self._redo.clear()
        self.version += 1
        return rebased

    def _key_of(self, row: Mapping[str, Any]) -> RowKey | None:
        values = tuple(row.get(name) for name in self.target.key)
        return None if any(v is None for v in values) else values

    # ------------------------------------------------------------------ internals

    def _record(self, kind: str, id_: Any, before: Any, after: Any) -> None:
        self._put(kind, id_, after)
        step = _Step(kind, id_, before, after)
        if self._group is not None:
            self._group.append(step)
        else:
            self._undo.append((step,))
            self._redo.clear()
        self.version += 1

    def _put(self, kind: str, id_: Any, entry: Any) -> None:
        store: dict[Any, Any]
        if kind == "update":
            store = self._updates
        elif kind == "delete":
            store = self._deletes
        else:
            store = self._inserts
        if entry is None:
            store.pop(id_, None)
        else:
            store[id_] = entry
