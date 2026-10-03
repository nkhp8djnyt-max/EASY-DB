"""Micro-benchmarks of the acceleration layer, with the Python reference next to each result.

python -m benchmarks.simd_micro            # prints a Markdown table
"""

from __future__ import annotations

import random
import re
import statistics
import time
from collections.abc import Callable

from sql_erd_studio.core import simd
from sql_erd_studio.core.columnar import ColumnStore
from sql_erd_studio.core.dialects import POSTGRESQL
from sql_erd_studio.core.dialects import splitter as splitter_module


def best_of(fn: Callable[[], object], repeat: int = 7, inner: int = 1) -> float:
    """Median seconds per call over ``repeat`` timings of ``inner`` calls."""
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        for _ in range(inner):
            fn()
        times.append((time.perf_counter() - start) / inner)
    return statistics.median(times)


def fmt(seconds: float) -> str:
    return f"{seconds * 1e3:.3f} ms" if seconds >= 1e-3 else f"{seconds * 1e6:.1f} µs"


def script(statements: int, body: int) -> str:
    """A realistic migration-like script: DDL with comments, inserts with long string literals."""
    rng = random.Random(1)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
    parts = []
    for i in range(statements):
        text = " ".join(rng.choice(words) for _ in range(body))
        parts.append(
            f"-- statement {i}; see ticket\n"
            f"INSERT INTO events (id, kind, payload) VALUES ({i}, 'k{i % 7}', '{text}');\n"
            f"/* note {i}: {text[: body // 2]} */\n"
            f"UPDATE events SET payload = 'x;{i}' WHERE id = {i};\n"
        )
    return "".join(parts)


def bench_find() -> list[tuple[str, str, str, str]]:
    rows = []
    for size in (64, 1_000, 100_000, 10_000_000):
        text = "a" * size + "'"
        pattern = re.compile(r"['\\]")
        results = {}
        for name, enabled in (("scalar C", False), ("SIMD", True)):
            simd.set_vector_enabled(enabled)
            inner = max(1, 2_000_000 // size)
            results[name] = best_of(
                lambda text=text: simd.find_first_of(text, 0, "'\\"), inner=inner
            )
        simd.set_vector_enabled(True)
        python = best_of(
            lambda text=text, pattern=pattern: pattern.search(text), inner=max(1, 2_000_000 // size)
        )
        rows.append(
            (
                f"find first of `'` or `\\` in {size:,} chars",
                fmt(python),
                f"{fmt(results['scalar C'])}",
                f"{fmt(results['SIMD'])}  ({python / results['SIMD']:.1f}× vs re)",
            )
        )
    return rows


def bench_split() -> list[tuple[str, str, str, str]]:
    rows = []
    for statements, body in ((10, 20), (200, 40), (2_000, 40), (2_000, 400)):
        sql = script(statements, body)
        python = best_of(lambda sql=sql: splitter_module._split_python(sql, POSTGRESQL), repeat=5)
        simd.set_vector_enabled(False)
        scalar = best_of(lambda sql=sql: splitter_module._split_native(sql, POSTGRESQL), repeat=5)
        simd.set_vector_enabled(True)
        vector = best_of(lambda sql=sql: splitter_module._split_native(sql, POSTGRESQL), repeat=5)
        rows.append(
            (
                f"split {statements:,} statements ({len(sql) / 1e3:.0f} kB)",
                fmt(python),
                fmt(scalar),
                f"{fmt(vector)}  ({python / vector:.1f}× vs Python)",
            )
        )
    return rows


def bench_columnar() -> list[tuple[str, str, str, str]]:
    rng = random.Random(2)
    rows_n = 1_000_000
    data = [
        (rng.randrange(10**9), f"name-{rng.randrange(10**6)}", rng.random()) for _ in range(rows_n)
    ]
    store = ColumnStore(("id", "name", "score"), data)
    out = []
    for column, label in ((0, "int"), (1, "text"), (2, "float")):
        store.arrow(column)  # conversion is cached after the first use; time the sort itself
        arrow = best_of(lambda column=column: store.sort_order(column), repeat=3)
        python = best_of(
            lambda column=column: sorted(range(rows_n), key=lambda i: data[i][column]), repeat=3
        )
        out.append(
            (
                f"sort 1M rows by {label}",
                fmt(python),
                "—",
                f"{fmt(arrow)}  ({python / arrow:.1f}× vs Python)",
            )
        )
    python = best_of(lambda: [i for i, r in enumerate(data) if "name-42" in r[1].lower()], repeat=3)
    arrow = best_of(lambda: store.search("name-42", [1]), repeat=3)
    out.append(
        (
            "filter 1M rows (substring, ignore case)",
            fmt(python),
            "—",
            f"{fmt(arrow)}  ({python / arrow:.1f}× vs Python)",
        )
    )
    return out


def main() -> None:
    status = simd.status()
    print(
        f"native scanner: {status['implementation']}; NumPy features: {', '.join(status['numpy'])}\n"
    )
    print("| Operation | Python reference | native, scalar | native, SIMD |")
    print("|---|---|---|---|")
    for row in bench_find() + bench_split() + bench_columnar():
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main()
