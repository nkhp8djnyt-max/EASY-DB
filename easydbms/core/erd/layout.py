"""Automatic placement of table cards: layered layout with component packing.

Referenced (parent) tables go to the left, the tables that reference them to the right. An edge
that spans several layers is split by invisible *dummy* nodes, one per layer it crosses, so that the
cards make room for it: the layout then also reports where such an edge can pass between the cards
(``Layout.channels``). The order inside each layer is improved by barycentre sweeps and the
vertical positions by pulling cards towards their neighbours. Unconnected pieces are packed into
rows, so a schema of hundreds of tables comes out roughly screen-shaped instead of one long strip.

It is a heuristic, not an optimal drawing; positions the user dragged always win (see
:mod:`.store`), and edges of moved cards fall back to the simple routes of :mod:`.routing`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x, y, width, height

#: Height reserved in a layer for one edge passing through it.
_DUMMY_HEIGHT = 6.0


@dataclass(frozen=True, slots=True)
class LayoutOptions:
    gap_x: float = 80.0
    gap_y: float = 28.0
    pack_gap: float = 64.0
    sweeps: int = 8
    #: Width / height the packed result aims for.
    aspect: float = 1.7
    group_padding: float = 24.0
    group_header: float = 34.0


@dataclass(frozen=True)
class Layout[K: Hashable]:
    positions: dict[K, Point] = field(default_factory=dict)
    #: Bounding boxes of schema groups, by group name (empty unless groups were requested).
    groups: dict[str, Box] = field(default_factory=dict)
    #: For edges that skip layers: the vertices of an orthogonal path through the gaps and
    #: between the cards, ordered left to right. Keyed by ``(child, parent)`` and ``(parent,
    #: child)`` alike. Valid only while both cards stay where the layout put them.
    channels: dict[tuple[K, K], tuple[Point, ...]] = field(default_factory=dict)


@dataclass
class _Piece[K: Hashable]:
    positions: dict[K, Point]
    width: float
    height: float
    channels: dict[tuple[K, K], tuple[Point, ...]]


def layout[K: Hashable](
    sizes: Mapping[K, tuple[float, float]],
    edges: Iterable[tuple[K, K]],
    *,
    groups: Mapping[K, str] | None = None,
    options: LayoutOptions | None = None,
) -> Layout[K]:
    """Place every node of ``sizes`` (``key -> (width, height)``).

    ``edges`` are ``(child, parent)`` pairs. With ``groups`` (``key -> group name``) each group is
    laid out on its own, using only the edges inside it, and the groups are packed side by side.
    """
    opts = options or LayoutOptions()
    edge_list = [(c, p) for c, p in edges if c in sizes and p in sizes]
    if groups is None:
        piece = _layout_nodes(list(sizes), edge_list, sizes, opts)
        return Layout(piece.positions, {}, piece.channels)

    members: dict[str, list[K]] = defaultdict(list)
    for key in sizes:
        members[groups.get(key, "")].append(key)
    blocks: list[tuple[str, _Piece[K]]] = []
    for name in sorted(members):
        nodes = members[name]
        inside = set(nodes)
        local = [(c, p) for c, p in edge_list if c in inside and p in inside]
        blocks.append((name, _layout_nodes(nodes, local, sizes, opts)))
    pad, head = opts.group_padding, opts.group_header
    outer = [(piece.width + 2 * pad, piece.height + head + pad) for _, piece in blocks]
    origins = _shelf(outer, opts)
    positions: dict[K, Point] = {}
    channels: dict[tuple[K, K], tuple[Point, ...]] = {}
    boxes: dict[str, Box] = {}
    for (name, piece), (ox, oy), (bw, bh) in zip(blocks, origins, outer, strict=True):
        boxes[name] = (ox, oy, bw, bh)
        dx, dy = ox + pad, oy + head
        for key, (x, y) in piece.positions.items():
            positions[key] = (dx + x, dy + y)
        for pair, points in piece.channels.items():
            channels[pair] = tuple((dx + x, dy + y) for x, y in points)
    return Layout(positions, boxes, channels)


# ---------------------------------------------------------------------- packing


def _shelf(boxes: Sequence[tuple[float, float]], opts: LayoutOptions) -> list[Point]:
    """Top-left corners placing ``boxes`` (in the given order) on rows of about equal width."""
    if not boxes:
        return []
    area = sum((w + opts.pack_gap) * (h + opts.pack_gap) for w, h in boxes)
    limit = max(max(w for w, _ in boxes), math.sqrt(area * opts.aspect))
    origins: list[Point] = []
    x = y = row_height = 0.0
    for w, h in boxes:
        if x > 0 and x + w > limit:
            x = 0.0
            y += row_height + opts.pack_gap
            row_height = 0.0
        origins.append((x, y))
        x += w + opts.pack_gap
        row_height = max(row_height, h)
    return origins


def _layout_nodes[K: Hashable](
    nodes: list[K],
    edges: list[tuple[K, K]],
    sizes: Mapping[K, tuple[float, float]],
    opts: LayoutOptions,
) -> _Piece[K]:
    """Lay out one set of nodes; positions are relative to (0, 0)."""
    if not nodes:
        return _Piece({}, 0.0, 0.0, {})
    components = _components(nodes, edges)
    linked = [c for c in components if len(c) > 1]
    loose = [c[0] for c in components if len(c) == 1]
    by_node = {c: i for i, comp in enumerate(linked) for c in comp}
    comp_edges: list[list[tuple[K, K]]] = [[] for _ in linked]
    for child, parent in edges:
        if child != parent and child in by_node:
            comp_edges[by_node[child]].append((child, parent))
    pieces = [
        _layout_component(comp, inner, sizes, opts)
        for comp, inner in zip(linked, comp_edges, strict=True)
    ]
    pieces.sort(key=lambda piece: -piece.height)  # tall pieces first (sort is stable)
    for key in loose:
        width, height = sizes[key]
        pieces.append(_Piece({key: (0.0, 0.0)}, width, height, {}))

    origins = _shelf([(p.width, p.height) for p in pieces], opts)
    positions: dict[K, Point] = {}
    channels: dict[tuple[K, K], tuple[Point, ...]] = {}
    width = height = 0.0
    for piece, (ox, oy) in zip(pieces, origins, strict=True):
        for key, (x, y) in piece.positions.items():
            positions[key] = (ox + x, oy + y)
        for pair, points in piece.channels.items():
            channels[pair] = tuple((ox + x, oy + y) for x, y in points)
        width = max(width, ox + piece.width)
        height = max(height, oy + piece.height)
    return _Piece(positions, width, height, channels)


def _components[K: Hashable](nodes: list[K], edges: list[tuple[K, K]]) -> list[list[K]]:
    neighbors: dict[K, list[K]] = defaultdict(list)
    for child, parent in edges:
        if child != parent:
            neighbors[child].append(parent)
            neighbors[parent].append(child)
    order = {n: i for i, n in enumerate(nodes)}
    seen: set[K] = set()
    result: list[list[K]] = []
    for start in nodes:
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        comp = []
        while stack:
            node = stack.pop()
            comp.append(node)
            for other in neighbors[node]:
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        comp.sort(key=order.__getitem__)
        result.append(comp)
    return result


# ---------------------------------------------------------------------- one component


def _layout_component[K: Hashable](
    nodes: list[K],
    edges: list[tuple[K, K]],
    sizes: Mapping[K, tuple[float, float]],
    opts: LayoutOptions,
) -> _Piece[K]:
    """Layered layout of one connected component, on integer ids (dummies get ids after ``n``)."""
    n = len(nodes)
    ident = {key: i for i, key in enumerate(nodes)}
    width = [sizes[key][0] for key in nodes]
    height = [sizes[key][1] for key in nodes]
    pairs = _dedupe([(ident[c], ident[p]) for c, p in edges])  # (parent, child), parent on the left
    directed = _break_cycles(n, pairs)
    layer = _assign_layers(n, directed)

    # split edges that skip layers: one dummy node in every layer they cross
    chains: dict[tuple[int, int], list[int]] = {}
    adjacent: list[tuple[int, int]] = []
    for parent, child in directed:
        if layer[child] - layer[parent] <= 1:
            adjacent.append((parent, child))
            continue
        previous = parent
        chain = []
        for crossed in range(layer[parent] + 1, layer[child]):
            dummy = len(layer)
            layer.append(crossed)
            width.append(0.0)
            height.append(_DUMMY_HEIGHT)
            adjacent.append((previous, dummy))
            chain.append(dummy)
            previous = dummy
        adjacent.append((previous, child))
        chains[(parent, child)] = chain
    total = len(layer)

    layers: list[list[int]] = [[] for _ in range(max(layer) + 1)]
    for node in range(total):
        layers[layer[node]].append(node)
    up: dict[int, list[int]] = defaultdict(list)  # neighbours in the previous layer
    down: dict[int, list[int]] = defaultdict(list)  # neighbours in the next layer
    for parent, child in adjacent:
        up[child].append(parent)
        down[parent].append(child)
    _sweep(layers, up, down, opts.sweeps)

    # x: layers side by side, with more room where many edges pass between two layers
    spanning = [0] * (len(layers) + 1)
    for parent, child in directed:
        for gap in range(layer[parent], layer[child]):
            spanning[gap] += 1
    left: list[float] = []
    right: list[float] = []
    x = 0.0
    for i, members in enumerate(layers):
        left.append(x)
        right.append(x + max(width[k] for k in members))
        x = right[-1] + opts.gap_x + 3.0 * min(spanning[i], 24)
    extent = right[-1]

    # y: stack, then pull each layer towards its neighbours a few times
    y_of: dict[int, float] = {}
    for members in layers:
        y = 0.0
        for key in members:
            y_of[key] = y
            y += height[key] + opts.gap_y
    _centre_layers(layers, y_of, height)
    for _ in range(3):
        for i in range(1, len(layers)):
            _pull(layers[i], up, y_of, height, opts.gap_y)
        for i in range(len(layers) - 2, -1, -1):
            _pull(layers[i], down, y_of, height, opts.gap_y)
    top = min(y_of.values())

    positions = {key: (left[layer[i]], y_of[i] - top) for i, key in enumerate(nodes)}
    bottom = max(y_of[i] - top + height[i] for i in range(total))

    channels: dict[tuple[K, K], tuple[Point, ...]] = {}
    for (parent, child), chain in chains.items():
        gaps = [(right[k - 1] + left[k]) / 2 for k in range(len(layers))[1:]]
        points: list[Point] = []
        # gap k lies between layers k-1 and k; walk the layers from the parent's side
        for step, dummy in enumerate(chain):
            crossed = layer[parent] + 1 + step
            cy = y_of[dummy] - top + _DUMMY_HEIGHT / 2
            points.append((gaps[crossed - 1], cy))
            points.append((gaps[crossed], cy))
        channels[(nodes[child], nodes[parent])] = tuple(points)
        channels[(nodes[parent], nodes[child])] = tuple(points)
    return _Piece(positions, max(extent, max(width[:n])), bottom, channels)


def _dedupe(edges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """``(child, parent)`` pairs as unique ``(parent, child)`` pairs, self references dropped."""
    seen: set[tuple[int, int]] = set()
    out = []
    for child, parent in edges:
        if child != parent and (parent, child) not in seen:
            seen.add((parent, child))
            out.append((parent, child))
    return out


def _break_cycles(n: int, edges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Reverse the edges that close a cycle (found by depth-first search) so the graph is a DAG."""
    successors: dict[int, list[int]] = defaultdict(list)
    for parent, child in edges:
        successors[parent].append(child)
    state: dict[int, int] = {}  # 1 = on the stack, 2 = finished
    reversed_edges: set[tuple[int, int]] = set()
    for root in range(n):
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(successors[root]))]
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if state.get(child) == 1:
                    reversed_edges.add((node, child))
                elif child not in state:
                    state[child] = 1
                    stack.append((child, iter(successors[child])))
                    advanced = True
                    break
            if not advanced:
                state[node] = 2
                stack.pop()
    result = []
    seen: set[tuple[int, int]] = set()
    for parent, child in edges:
        pair = (child, parent) if (parent, child) in reversed_edges else (parent, child)
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def _assign_layers(n: int, directed: list[tuple[int, int]]) -> list[int]:
    """Longest-path layering, then sources are moved next to their nearest child."""
    preds: dict[int, list[int]] = defaultdict(list)
    succs: dict[int, list[int]] = defaultdict(list)
    indegree = [0] * n
    for parent, child in directed:
        preds[child].append(parent)
        succs[parent].append(child)
        indegree[child] += 1
    layer = [0] * n
    queue = [i for i in range(n) if indegree[i] == 0]
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        for child in succs[node]:
            layer[child] = max(layer[child], layer[node] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    for node in range(n):  # sources with only far-away children: bring them closer
        if not preds[node] and succs[node]:
            layer[node] = max(layer[node], min(layer[c] for c in succs[node]) - 1)
    return layer


def _sweep(
    layers: list[list[int]], up: dict[int, list[int]], down: dict[int, list[int]], sweeps: int
) -> None:
    """Barycentre ordering, alternating downwards and upwards."""
    position = {key: i for members in layers for i, key in enumerate(members)}
    for sweep in range(sweeps):
        indexes = range(1, len(layers)) if sweep % 2 == 0 else range(len(layers) - 2, -1, -1)
        neighbors = up if sweep % 2 == 0 else down
        for i in indexes:
            members = layers[i]

            def barycentre(key: int, neighbors: dict[int, list[int]] = neighbors) -> float:
                others = neighbors.get(key)
                if not others:
                    return float(position[key])
                return sum(position[o] for o in others) / len(others)

            members.sort(key=barycentre)  # stable: ties keep their order
            for index, key in enumerate(members):
                position[key] = index


def _centre_layers(layers: list[list[int]], y_of: dict[int, float], height: list[float]) -> None:
    extents = [y_of[members[-1]] + height[members[-1]] for members in layers]
    tallest = max(extents)
    for members, extent in zip(layers, extents, strict=True):
        shift = (tallest - extent) / 2
        for key in members:
            y_of[key] += shift


def _pull(
    members: list[int],
    neighbors: dict[int, list[int]],
    y_of: dict[int, float],
    height: list[float],
    gap: float,
) -> None:
    """Move a layer's cards towards the centres of their neighbours, keeping order and gaps."""
    desired: dict[int, float] = {}
    for key in members:
        others = neighbors.get(key)
        if others:
            centre = sum(y_of[o] + height[o] / 2 for o in others) / len(others)
            desired[key] = centre - height[key] / 2
        else:
            desired[key] = y_of[key]
    placed: dict[int, float] = {}
    bottom = -math.inf
    for key in members:
        placed[key] = desired[key] if bottom == -math.inf else max(desired[key], bottom + gap)
        bottom = placed[key] + height[key]
    error = sum(placed[k] - desired[k] for k in members) / len(members)
    for key in members:
        y_of[key] = placed[key] - error
