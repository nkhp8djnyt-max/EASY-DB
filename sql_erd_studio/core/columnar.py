"""Vectorised sorting and searching of a result set.

Rows arrive as Python tuples; the grid needs to sort and filter them on demand. Columns are
converted to Arrow arrays once (a C loop) and cached; sorting and substring search then run in
Arrow's / NumPy's compiled, SIMD-accelerated kernels instead of Python loops. Columns that Arrow
cannot represent as one type (e.g. a SQLite column mixing numbers and text) fall back to a Python
sort that orders by type first, then value, with NULLs last.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc


class ColumnStore:
    def __init__(self, columns: Sequence[str], rows: Sequence[tuple[Any, ...]]) -> None:
        self.columns = tuple(columns)
        self._rows = rows
        self._arrays: dict[int, pa.Array | None] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def arrow(self, column: int) -> pa.Array | None:
        """The column as an Arrow array, or ``None`` if its values do not fit one type."""
        if column not in self._arrays:
            values = [row[column] for row in self._rows]
            try:
                self._arrays[column] = pa.array(values)
            except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError, OverflowError):
                self._arrays[column] = None
        return self._arrays[column]

    # ------------------------------------------------------------------ sorting

    def sort_order(
        self, column: int, descending: bool = False, rows: np.ndarray | None = None
    ) -> np.ndarray:
        """Row indices in sorted order (NULLs last, ties keep their original order).

        ``rows`` restricts the sort to a subset (a filtered view); the result then contains only
        those indices.
        """
        array = self.arrow(column)
        if array is None:
            return self._python_sort(column, descending, rows)
        if rows is not None:
            array = array.take(pa.array(rows))
        order = pc.array_sort_indices(
            array, order="descending" if descending else "ascending", null_placement="at_end"
        ).to_numpy()
        return np.asarray(order if rows is None else rows[order])

    def _python_sort(self, column: int, descending: bool, rows: np.ndarray | None) -> np.ndarray:
        indices = range(len(self._rows)) if rows is None else rows.tolist()
        present = [i for i in indices if self._rows[i][column] is not None]
        missing = [i for i in indices if self._rows[i][column] is None]

        def key(i: int) -> tuple[str, Any]:
            value = self._rows[i][column]
            try:
                return (type(value).__name__, value)
            except TypeError:  # pragma: no cover - unorderable values are compared by text
                return (type(value).__name__, str(value))

        try:
            present.sort(key=key, reverse=descending)
        except TypeError:
            present.sort(key=lambda i: str(self._rows[i][column]), reverse=descending)
        return np.array(present + missing, dtype=np.int64)

    # ------------------------------------------------------------------ search

    def search(self, text: str, columns: Sequence[int] | None = None) -> np.ndarray:
        """Indices of rows where any (selected) column's text contains ``text``, ignoring case."""
        if not text:
            return np.arange(len(self._rows))
        mask = np.zeros(len(self._rows), dtype=bool)
        for column in columns if columns is not None else range(len(self.columns)):
            mask |= self._column_matches(column, text)
        return np.flatnonzero(mask)

    def _column_matches(self, column: int, text: str) -> np.ndarray:
        array = self.arrow(column)
        if array is not None:
            try:
                if not (pa.types.is_string(array.type) or pa.types.is_large_string(array.type)):
                    array = pc.cast(array, pa.string())
                matches = pc.match_substring(array, text, ignore_case=True)
                return np.asarray(
                    pc.fill_null(matches, False).to_numpy(zero_copy_only=False), dtype=bool
                )
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError):
                pass  # e.g. binary values: search their text form below
        needle = text.lower()
        return np.fromiter(
            (
                needle in str(row[column]).lower() if row[column] is not None else False
                for row in self._rows
            ),
            dtype=bool,
            count=len(self._rows),
        )
