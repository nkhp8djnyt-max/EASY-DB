from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from easydbms.core.editing import ChangeSet

from .conftest import books_table, composite_table, target_of

ORIGINAL = {
    "id": 1,
    "title": "Dune",
    "pages": 412,
    "price": Decimal("9.90"),
    "rating": 4.5,
    "published": date(1965, 8, 1),
    "added": None,
    "in_print": True,
    "meta": None,
    "cover": None,
}
KEY = (1,)


@pytest.fixture
def changes() -> ChangeSet:
    return ChangeSet(target_of())


def test_a_new_change_set_is_empty(changes: ChangeSet) -> None:
    assert changes.is_empty
    assert changes.count == 0
    assert not changes.can_undo
    assert not changes.can_redo


def test_editing_a_cell_records_old_and_new_value(changes: ChangeSet) -> None:
    assert changes.set_cell(KEY, ORIGINAL, "title", "Dune Messiah")
    update = changes.updates[KEY]
    assert update.changes == {"title": ("Dune", "Dune Messiah")}
    assert changes.edited_value(KEY, "title") == (True, "Dune Messiah")
    assert changes.edited_value(KEY, "pages") == (False, None)
    assert changes.count == 1


def test_editing_a_cell_twice_keeps_the_first_old_value(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.set_cell(KEY, ORIGINAL, "title", "B")
    assert changes.updates[KEY].changes["title"] == ("Dune", "B")
    assert changes.count == 1


def test_editing_back_to_the_original_removes_the_change(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    assert changes.set_cell(KEY, ORIGINAL, "title", "Dune")
    assert changes.is_empty


def test_setting_the_same_value_changes_nothing(changes: ChangeSet) -> None:
    assert not changes.set_cell(KEY, ORIGINAL, "title", "Dune")
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    assert not changes.set_cell(KEY, ORIGINAL, "title", "A")
    assert changes.is_empty is False
    assert len(changes._undo) == 1  # no history entry for the no-ops


def test_equal_numbers_in_another_representation_are_not_a_change(changes: ChangeSet) -> None:
    assert not changes.set_cell(KEY, ORIGINAL, "price", Decimal("9.9"))
    assert not changes.set_cell(KEY, ORIGINAL, "pages", Decimal("412"))
    assert changes.is_empty


def test_null_can_be_set_and_cleared(changes: ChangeSet) -> None:
    assert changes.set_cell(KEY, ORIGINAL, "pages", None)
    assert changes.updates[KEY].changes["pages"] == (412, None)
    assert changes.set_cell(KEY, ORIGINAL, "added", None) is False  # already NULL
    assert not changes.set_cell(KEY, ORIGINAL, "cover", b"x")  # binary columns are read-only


def test_unknown_columns_are_ignored(changes: ChangeSet) -> None:
    assert not changes.set_cell(KEY, {**ORIGINAL, "ghost": 1}, "ghost", 2)


def test_each_row_is_tracked_by_its_own_key(changes: ChangeSet) -> None:
    changes.set_cell((1,), ORIGINAL, "title", "A")
    changes.set_cell((2,), {**ORIGINAL, "id": 2}, "title", "B")
    assert set(changes.updates) == {(1,), (2,)}
    assert changes.count == 2


# ---------------------------------------------------------------------------- delete


def test_deleting_and_restoring_a_row(changes: ChangeSet) -> None:
    assert changes.delete_row(KEY, ORIGINAL)
    assert changes.is_deleted(KEY)
    assert changes.deletes[KEY].values["title"] == "Dune"
    assert not changes.delete_row(KEY, ORIGINAL)
    assert changes.restore_row(KEY)
    assert changes.is_empty
    assert not changes.restore_row(KEY)


def test_deleting_a_row_drops_its_edits_and_undo_brings_them_back(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.delete_row(KEY, ORIGINAL)
    assert KEY not in changes.updates
    assert changes.count == 1
    changes.undo()
    assert changes.updates[KEY].changes["title"] == ("Dune", "A")
    assert not changes.is_deleted(KEY)


def test_a_deleted_row_cannot_be_edited(changes: ChangeSet) -> None:
    changes.delete_row(KEY, ORIGINAL)
    assert not changes.set_cell(KEY, ORIGINAL, "title", "x")


def test_reverting_a_row_drops_its_edits_and_its_deletion(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.set_cell(KEY, ORIGINAL, "pages", 1)
    assert changes.revert_row(KEY)
    assert changes.is_empty
    changes.delete_row(KEY, ORIGINAL)
    assert changes.revert_row(KEY)
    assert changes.is_empty
    assert not changes.revert_row(KEY)


def test_reverting_one_cell_keeps_the_others(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.set_cell(KEY, ORIGINAL, "pages", 1)
    assert changes.revert_cell(KEY, "title")
    assert set(changes.updates[KEY].changes) == {"pages"}
    assert changes.revert_cell(KEY, "pages")
    assert changes.is_empty
    assert not changes.revert_cell(KEY, "pages")


# ---------------------------------------------------------------------------- new rows


def test_new_rows_hold_only_the_columns_that_were_set(changes: ChangeSet) -> None:
    new_id = changes.add_row({"title": "Emma"})
    assert changes.inserts[new_id].values == {"title": "Emma"}
    assert changes.set_new_cell(new_id, "pages", 300)
    assert changes.set_new_cell(new_id, "price", None)  # an explicit NULL is a value
    assert changes.inserts[new_id].values == {"title": "Emma", "pages": 300, "price": None}
    assert changes.unset_new_cell(new_id, "price")
    assert "price" not in changes.inserts[new_id].values
    assert not changes.unset_new_cell(new_id, "price")
    assert changes.count == 1


def test_new_rows_get_distinct_ids(changes: ChangeSet) -> None:
    first, second = changes.add_row(), changes.add_row()
    assert first != second
    assert changes.count == 2


def test_removing_a_new_row_forgets_it(changes: ChangeSet) -> None:
    new_id = changes.add_row({"title": "x"})
    assert changes.remove_new(new_id)
    assert changes.is_empty
    assert not changes.remove_new(new_id)
    assert not changes.set_new_cell(new_id, "title", "y")


def test_new_cells_follow_the_same_rules_as_loaded_ones(changes: ChangeSet) -> None:
    new_id = changes.add_row()
    assert not changes.set_new_cell(new_id, "cover", b"x")
    assert not changes.set_new_cell(new_id, "ghost", 1)
    assert changes.set_new_cell(new_id, "title", "a")
    assert not changes.set_new_cell(new_id, "title", "a")


# ---------------------------------------------------------------------------- undo / redo


def test_undo_and_redo_walk_through_the_edits(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.set_cell(KEY, ORIGINAL, "title", "B")
    assert changes.can_undo
    assert changes.undo()
    assert changes.updates[KEY].changes["title"] == ("Dune", "A")
    assert changes.undo()
    assert changes.is_empty
    assert not changes.undo()
    assert changes.redo()
    assert changes.redo()
    assert changes.updates[KEY].changes["title"] == ("Dune", "B")
    assert not changes.redo()


def test_a_new_edit_clears_the_redo_history(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.undo()
    assert changes.can_redo
    changes.set_cell(KEY, ORIGINAL, "title", "C")
    assert not changes.can_redo


def test_undo_restores_deletes_and_inserts(changes: ChangeSet) -> None:
    changes.delete_row(KEY, ORIGINAL)
    new_id = changes.add_row({"title": "x"})
    changes.undo()
    assert new_id not in changes.inserts
    changes.undo()
    assert not changes.is_deleted(KEY)
    changes.redo()
    assert changes.is_deleted(KEY)
    changes.redo()
    assert new_id in changes.inserts


def test_grouped_steps_undo_together(changes: ChangeSet) -> None:
    with changes.group():
        for number in (1, 2, 3):
            changes.delete_row((number,), {**ORIGINAL, "id": number})
    assert changes.count == 3
    changes.undo()
    assert changes.is_empty
    changes.redo()
    assert changes.count == 3


def test_nested_groups_belong_to_the_outer_one(changes: ChangeSet) -> None:
    with changes.group():
        changes.set_cell(KEY, ORIGINAL, "title", "A")
        with changes.group():
            changes.set_cell(KEY, ORIGINAL, "pages", 1)
    assert len(changes._undo) == 1
    changes.undo()
    assert changes.is_empty


def test_an_empty_group_leaves_no_history(changes: ChangeSet) -> None:
    with changes.group():
        pass
    assert not changes.can_undo


def test_discard_all_can_be_undone(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.delete_row((2,), {**ORIGINAL, "id": 2})
    changes.add_row({"title": "n"})
    assert changes.discard_all()
    assert changes.is_empty
    assert not changes.discard_all()
    assert changes.undo()
    assert changes.count == 3


def test_the_version_changes_with_every_modification(changes: ChangeSet) -> None:
    seen = {changes.version}
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    seen.add(changes.version)
    changes.undo()
    seen.add(changes.version)
    changes.redo()
    seen.add(changes.version)
    assert len(seen) == 4


def test_reset_forgets_everything_including_history(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "A")
    changes.reset()
    assert changes.is_empty
    assert not changes.can_undo


# ---------------------------------------------------------------------------- rebase


def test_rebase_takes_the_fresh_rows_as_the_new_baseline(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "Mine")
    changes.set_cell(KEY, ORIGINAL, "pages", 1)
    fresh = {
        **ORIGINAL,
        "title": "Theirs",
        "pages": 1,
    }  # somebody else changed the title; pages too
    assert changes.rebase([fresh]) == 1
    assert changes.updates[KEY].changes == {
        "title": ("Theirs", "Mine")
    }  # pages now equals the server
    assert not changes.can_undo


def test_rebase_drops_edits_the_server_already_has(changes: ChangeSet) -> None:
    changes.set_cell(KEY, ORIGINAL, "title", "Same")
    changes.rebase([{**ORIGINAL, "title": "Same"}])
    assert changes.is_empty


def test_rebase_ignores_rows_that_are_not_in_the_fresh_page(changes: ChangeSet) -> None:
    changes.set_cell((7,), {**ORIGINAL, "id": 7}, "title", "x")
    assert changes.rebase([ORIGINAL]) == 0
    assert (7,) in changes.updates


# ------------------------------------------------------------------ composite keys, key edits


def test_composite_keys_identify_rows() -> None:
    target = target_of(composite_table())
    changes = ChangeSet(target)
    original = {"order_id": 1, "line_no": 2, "qty": 3, "note": None}
    changes.set_cell((1, 2), original, "qty", 4)
    assert changes.updates[(1, 2)].changes == {"qty": (3, 4)}


def test_key_columns_can_be_edited_and_the_old_key_stays_the_identity() -> None:
    changes = ChangeSet(target_of(books_table()))
    changes.set_cell(KEY, ORIGINAL, "id", 100)
    assert changes.updates[KEY].changes == {"id": (1, 100)}
    assert KEY in changes.updates
