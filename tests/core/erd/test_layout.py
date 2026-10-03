from __future__ import annotations

import itertools
import random
import time
from collections.abc import Mapping

import pytest

from easydbms.core.erd import LayoutOptions, layout

Size = tuple[float, float]


def sizes_of(names: list[str], w: float = 200, h: float = 120) -> dict[str, Size]:
    return dict.fromkeys(names, (w, h))


def overlaps(
    positions: Mapping[str, tuple[float, float]], sizes: Mapping[str, Size]
) -> list[tuple[str, str]]:
    bad = []
    for a, b in itertools.combinations(positions, 2):
        ax, ay = positions[a]
        bx, by = positions[b]
        aw, ah = sizes[a]
        bw, bh = sizes[b]
        if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
            bad.append((a, b))
    return bad


def test_empty_and_single() -> None:
    assert layout({}, []).positions == {}
    result = layout({"a": (100, 50)}, [])
    assert result.positions == {"a": (0.0, 0.0)}


def test_parents_are_left_of_children() -> None:
    names = ["order", "customer", "item", "product", "category"]
    edges = [("order", "customer"), ("item", "order"), ("item", "product"), ("product", "category")]
    result = layout(sizes_of(names), edges)
    for child, parent in edges:
        assert result.positions[parent][0] < result.positions[child][0], (child, parent)
    assert not overlaps(result.positions, sizes_of(names))


def test_chain_gets_increasing_x() -> None:
    names = list("abcde")
    edges = [("b", "a"), ("c", "b"), ("d", "c"), ("e", "d")]
    xs = [layout(sizes_of(names), edges).positions[n][0] for n in names]
    assert xs == sorted(xs)
    assert len(set(xs)) == 5


def test_cycles_and_self_loops_are_handled() -> None:
    names = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("d", "d"), ("d", "a")]
    result = layout(sizes_of(names), edges)
    assert set(result.positions) == set(names)
    assert not overlaps(result.positions, sizes_of(names))


def test_mutual_references_do_not_stack_on_top_of_each_other() -> None:
    result = layout(sizes_of(["a", "b"]), [("a", "b"), ("b", "a")])
    assert result.positions["a"] != result.positions["b"]


def test_deterministic() -> None:
    rng = random.Random(7)
    names = [f"t{i}" for i in range(60)]
    edges = [(rng.choice(names), rng.choice(names)) for _ in range(90)]
    first = layout(sizes_of(names), edges).positions
    assert layout(sizes_of(names), edges).positions == first


def test_mixed_sizes_never_overlap() -> None:
    rng = random.Random(3)
    names = [f"t{i}" for i in range(80)]
    sizes = {n: (rng.randint(120, 320), rng.randint(60, 600)) for n in names}
    edges = [(rng.choice(names), rng.choice(names)) for _ in range(120)]
    result = layout(sizes, edges)
    assert not overlaps(result.positions, sizes)


def test_unconnected_tables_are_packed_into_rows_not_one_strip() -> None:
    names = [f"t{i:02d}" for i in range(40)]
    sizes = sizes_of(names)
    result = layout(sizes, [])
    xs = [p[0] for p in result.positions.values()]
    ys = [p[1] for p in result.positions.values()]
    width = max(xs) + 200
    height = max(ys) + 120
    assert 0.5 < width / height < 4  # roughly screen-shaped
    assert not overlaps(result.positions, sizes)


def test_components_are_separated() -> None:
    names = ["a", "b", "c", "d"]
    result = layout(sizes_of(names), [("b", "a"), ("d", "c")])
    assert not overlaps(result.positions, sizes_of(names))


def test_groups_get_boxes_that_contain_their_members() -> None:
    names = [f"s1.t{i}" for i in range(6)] + [f"s2.t{i}" for i in range(5)]
    sizes = sizes_of(names)
    groups = {n: n.split(".")[0] for n in names}
    edges = [("s1.t1", "s1.t0"), ("s1.t2", "s1.t1"), ("s2.t1", "s2.t0"), ("s2.t1", "s1.t0")]
    result = layout(sizes, edges, groups=groups)
    assert set(result.groups) == {"s1", "s2"}
    for name, (x, y) in result.positions.items():
        gx, gy, gw, gh = result.groups[groups[name]]
        w, h = sizes[name]
        assert gx <= x
        assert gy < y
        assert x + w <= gx + gw
        assert y + h <= gy + gh
    boxes = {g: (b[0], b[1], b[2], b[3]) for g, b in result.groups.items()}
    (ax, ay, aw, ah), (bx, by, bw, bh) = boxes["s1"], boxes["s2"]
    assert not (ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah)
    assert not overlaps(result.positions, sizes)


def test_edges_to_unknown_nodes_are_ignored() -> None:
    result = layout(sizes_of(["a", "b"]), [("a", "ghost"), ("b", "a")])
    assert set(result.positions) == {"a", "b"}


@pytest.mark.parametrize("count", [200, 1000])
def test_large_schema_is_laid_out_quickly_and_without_overlap(count: int) -> None:
    rng = random.Random(count)
    names = [f"t{i}" for i in range(count)]
    sizes = {n: (rng.randint(160, 300), rng.randint(60, 300)) for n in names}
    edges = [(names[i], names[rng.randrange(i)]) for i in range(1, count) if rng.random() < 0.85]
    started = time.perf_counter()
    result = layout(sizes, edges)
    elapsed = time.perf_counter() - started
    assert elapsed < 8.0, elapsed
    assert len(result.positions) == count
    if count <= 200:
        assert not overlaps(result.positions, sizes)
    xs = [p[0] + sizes[n][0] for n, p in result.positions.items()]
    ys = [p[1] + sizes[n][1] for n, p in result.positions.items()]
    assert 0.2 < max(xs) / max(ys) < 8  # not a thin strip


def test_options_change_spacing() -> None:
    names = ["a", "b"]
    tight = layout(sizes_of(names), [("b", "a")], options=LayoutOptions(gap_x=10)).positions
    wide = layout(sizes_of(names), [("b", "a")], options=LayoutOptions(gap_x=300)).positions
    assert wide["b"][0] - wide["a"][0] > tight["b"][0] - tight["a"][0]
