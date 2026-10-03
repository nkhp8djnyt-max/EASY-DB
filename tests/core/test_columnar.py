from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from easydbms.core.columnar import ColumnStore


def store(*rows: tuple[object, ...]) -> ColumnStore:
    return ColumnStore([f"c{i}" for i in range(len(rows[0]))], list(rows))


def order(s: ColumnStore, column: int = 0, desc: bool = False) -> list[int]:
    return [int(i) for i in s.sort_order(column, desc)]


def test_integers_sort_with_nulls_last_and_stable_ties() -> None:
    s = store((3,), (1,), (None,), (2,), (1,))
    assert order(s) == [1, 4, 3, 0, 2]
    assert order(s, desc=True) == [0, 3, 1, 4, 2]  # nulls still last, ties keep original order


@pytest.mark.parametrize(
    "values",
    [
        ["b", "a", "c"],
        [1.5, -2.0, 0.0],
        [Decimal("2.5"), Decimal("-1"), Decimal("10")],
        [date(2024, 3, 1), date(2023, 1, 1), date(2025, 1, 1)],
        [datetime(2024, 1, 2, 3), datetime(2020, 1, 1), datetime(2030, 1, 1)],
        [True, False, True],
        [b"b", b"a", b"c"],
    ],
)
def test_every_common_type_sorts_ascending(values: list[Any]) -> None:
    s = store(*[(v,) for v in values])
    expected = sorted(range(len(values)), key=lambda i: values[i])
    assert order(s) == expected
    assert order(s, desc=True) == sorted(range(len(values)), key=lambda i: values[i], reverse=True)


def test_mixed_types_fall_back_to_type_then_value_order() -> None:
    s = store((10,), ("b",), (None,), (2,), ("a",))
    assert s.arrow(0) is None
    assert order(s) == [3, 0, 4, 1, 2]  # ints, then strings, nulls last


def test_sorting_a_subset_returns_only_those_rows() -> None:
    s = store((5,), (4,), (3,), (2,), (1,))
    subset = np.array([0, 2, 4])
    assert s.sort_order(0, False, subset).tolist() == [4, 2, 0]
    assert s.sort_order(0, True, subset).tolist() == [0, 2, 4]


def test_python_fallback_respects_subsets_and_direction() -> None:
    s = store((10,), ("b",), (None,), (2,), ("a",))
    assert s.sort_order(0, True, np.array([0, 1, 3, 4])).tolist() == [1, 4, 0, 3]


def test_search_matches_any_column_ignoring_case() -> None:
    s = store(("Alice", 1), ("bob", 22), ("CAROL", 3), (None, 4))
    assert s.search("al").tolist() == [0]
    assert s.search("OB").tolist() == [1]
    assert s.search("2").tolist() == [1]  # numbers are searched by their text
    assert s.search("").tolist() == [0, 1, 2, 3]
    assert s.search("zzz").tolist() == []
    assert s.search("o", columns=[0]).tolist() == [1, 2]
    assert s.search("o", columns=[1]).tolist() == []


def test_search_in_temporal_decimal_binary_and_mixed_columns() -> None:
    s = store(
        (date(2024, 5, 6), Decimal("12.50"), b"\xde\xad", "x"),
        (date(2023, 1, 1), Decimal("7"), b"beef", 5),
    )
    assert s.search("2024").tolist() == [0]
    assert s.search("12.5").tolist() == [0]
    assert s.search("beef").tolist() == [1]
    assert s.search("x").tolist() == [0]


def test_columns_are_converted_once_and_cached() -> None:
    s = store((1,), (2,))
    assert s.arrow(0) is s.arrow(0)
    assert len(s) == 2


def test_large_result_sorts_correctly() -> None:
    rng = np.random.default_rng(7)
    values = rng.integers(0, 1000, 50_000).tolist()
    s = store(*[(v,) for v in values])
    result = [values[i] for i in s.sort_order(0)]
    assert result == sorted(values)
