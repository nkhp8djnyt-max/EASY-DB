"""Orthogonal edge routes between table cards (plain coordinates, no Qt)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True, slots=True)
class Route:
    """A polyline from the child card to the parent card."""

    points: tuple[tuple[float, float], ...]
    #: Which side of its card each end leaves from: ``"left"`` or ``"right"`` (decides the
    #: direction in which the crow's foot and the "one" bar are drawn).
    start_side: str
    end_side: str


#: Room left outside a card for the marker at an edge's end.
_STUB = 18.0
_LANE = 7.0


def route(child: Rect, parent: Rect, child_y: float, parent_y: float, lane: int = 0) -> Route:
    """Route an edge from ``child`` (at height ``child_y``) to ``parent`` (at ``parent_y``).

    Cards side by side are joined through the gap between them; cards that overlap horizontally
    (stacked, or the same card for a self reference) are joined around their right-hand sides.
    ``lane`` shifts the vertical segment so parallel edges do not lie on top of each other.
    """
    shift = lane * _LANE
    if child.right + 2 * _STUB <= parent.x:
        sx, ex = child.right, parent.x
        mid = (sx + ex) / 2 + shift
        return Route(
            ((sx, child_y), (mid, child_y), (mid, parent_y), (ex, parent_y)), "right", "left"
        )
    if parent.right + 2 * _STUB <= child.x:
        sx, ex = child.x, parent.right
        mid = (sx + ex) / 2 + shift
        return Route(
            ((sx, child_y), (mid, child_y), (mid, parent_y), (ex, parent_y)), "left", "right"
        )
    sx, ex = child.right, parent.right
    mid = max(sx, ex) + 2 * _STUB + shift
    return Route(((sx, child_y), (mid, child_y), (mid, parent_y), (ex, parent_y)), "right", "right")


def assign_lanes(spans: Sequence[tuple[float, float]], cell: float = 48.0) -> list[int]:
    """A lane in ``-3..3`` for each edge so edges through the same gap fan out.

    ``spans`` is ``(x_start, x_end)`` of each edge; edges whose spans fall in the same coarse cell
    share a gap and get consecutive lanes.
    """
    seen: dict[tuple[int, int], int] = {}
    lanes = []
    for start, end in spans:
        key = (round(min(start, end) / cell), round(max(start, end) / cell))
        count = seen.get(key, 0)
        seen[key] = count + 1
        lanes.append(count % 7 - 3)
    return lanes


def route_through(
    child: Rect,
    parent: Rect,
    child_y: float,
    parent_y: float,
    channel: Sequence[tuple[float, float]],
    lane: int = 0,
) -> Route | None:
    """Route an edge that skips layers along ``channel`` (vertices ordered left to right).

    Returns ``None`` when the cards no longer stand left and right of each other with the channel
    between them (one was dragged away), so the caller can use :func:`route` instead.
    """
    if not channel:
        return None
    shift = lane * _LANE
    first_x, last_x = channel[0][0], channel[-1][0]
    if child.right <= first_x and last_x <= parent.x:  # the child is on the left
        sx, ex = child.right, parent.x
        ordered = list(channel)
        sides = ("right", "left")
    elif parent.right <= first_x and last_x <= child.x:  # the child is on the right
        sx, ex = child.x, parent.right
        ordered = list(reversed(channel))
        sides = ("left", "right")
    else:
        return None
    points = [(sx, child_y), (ordered[0][0] + shift, child_y)]
    points.extend((x + shift, y) for x, y in ordered)
    points.append((ordered[-1][0] + shift, parent_y))
    points.append((ex, parent_y))
    return Route(tuple(points), sides[0], sides[1])
