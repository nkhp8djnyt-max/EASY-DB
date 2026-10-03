from __future__ import annotations

from itertools import pairwise

from easydbms.core.erd import Rect, assign_lanes, route


def test_card_to_the_left_leaves_from_its_right_side() -> None:
    child, parent = Rect(0, 0, 100, 80), Rect(300, 40, 100, 80)
    r = route(child, parent, 20, 60)
    assert r.start_side == "right"
    assert r.end_side == "left"
    assert r.points[0] == (100, 20)
    assert r.points[-1] == (300, 60)
    xs = [p[0] for p in r.points]
    assert xs[1] == xs[2]  # a single vertical segment in the gap
    assert 100 < xs[1] < 300


def test_card_to_the_right_enters_from_the_other_side() -> None:
    child, parent = Rect(300, 0, 100, 80), Rect(0, 0, 100, 80)
    r = route(child, parent, 10, 50)
    assert (r.start_side, r.end_side) == ("left", "right")
    assert r.points[0] == (300, 10)
    assert r.points[-1] == (100, 50)


def test_orthogonal_segments_only() -> None:
    for child, parent in (
        (Rect(0, 0, 100, 80), Rect(300, 100, 100, 80)),
        (Rect(300, 0, 100, 80), Rect(0, 100, 100, 80)),
        (Rect(0, 0, 100, 80), Rect(20, 200, 100, 80)),
    ):
        pts = route(child, parent, 10, 210).points
        for a, b in pairwise(pts):
            assert a[0] == b[0] or a[1] == b[1]


def test_overlapping_columns_loop_around_the_right_side() -> None:
    child, parent = Rect(0, 0, 100, 80), Rect(10, 200, 140, 80)
    r = route(child, parent, 20, 230)
    assert (r.start_side, r.end_side) == ("right", "right")
    assert max(p[0] for p in r.points) > 150  # outside both cards


def test_self_reference_loops_out_and_back() -> None:
    card = Rect(0, 0, 100, 120)
    r = route(card, card, 30, 90)
    assert r.points[0] == (100, 30)
    assert r.points[-1] == (100, 90)
    assert r.points[1][0] > 100


def test_lane_shifts_the_vertical_segment() -> None:
    child, parent = Rect(0, 0, 100, 80), Rect(300, 0, 100, 80)
    base = route(child, parent, 10, 50, 0).points[1][0]
    assert route(child, parent, 10, 50, 2).points[1][0] > base
    assert route(child, parent, 10, 50, -2).points[1][0] < base


def test_assign_lanes_fans_out_edges_through_one_gap() -> None:
    lanes = assign_lanes([(100, 300)] * 9 + [(500, 700)])
    assert lanes[:7] == [-3, -2, -1, 0, 1, 2, 3]
    assert lanes[7:9] == [-3, -2]  # cycles
    assert lanes[9] == -3  # another gap starts over
