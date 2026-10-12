"""Derived wire geometry and focus; no Qt or project mutations."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .flow_routing import EdgeKey, Point, Route, simplify

NORMAL_COLOR = "#98a3af"
DIM_COLOR = "#c7cdd5"
FOCUS_COLOR = "#247ba8"
SELECTED_COLOR = "#d97706"
ERROR_COLOR = "#dc6b17"


@dataclass(frozen=True)
class WirePart:
    points: tuple[Point, ...]
    owners: frozenset[EdgeKey]


@dataclass(frozen=True)
class WireGeometry:
    parts: tuple[WirePart, ...]
    junctions: tuple[tuple[Point, frozenset[EdgeKey]], ...]


def splitLines(lines: Iterable[tuple[Point, Point, EdgeKey]]) -> tuple[WirePart, ...]:
    """Sweep each axis independently, preserving owners of partial overlaps."""
    groups: dict[tuple[bool, float], dict[float, list[tuple[EdgeKey, int]]]] = {}
    for start, end, owner in lines:
        if start == end:
            continue
        horizontal = start[1] == end[1]
        if not horizontal and start[0] != end[0]:
            raise ValueError("orthogonal wire required")
        axis = 0 if horizontal else 1
        low, high = sorted((start[axis], end[axis]))
        events = groups.setdefault((horizontal, start[1 - axis]), {})
        events.setdefault(low, []).append((owner, 1))
        events.setdefault(high, []).append((owner, -1))
    result = []
    for (horizontal, fixed), events in sorted(groups.items()):
        active: dict[EdgeKey, int] = {}
        previous = None
        for position, changes in sorted(events.items()):
            if active and previous is not None and previous != position:
                a = (previous, fixed) if horizontal else (fixed, previous)
                b = (position, fixed) if horizontal else (fixed, position)
                result.append(WirePart((a, b), frozenset(active)))
            for owner, delta in changes:
                active[owner] = active.get(owner, 0) + delta
                if active[owner] == 0:
                    del active[owner]
            previous = position
    return tuple(result)


def wireGeometry(routes: Mapping[EdgeKey, Route]) -> WireGeometry:
    """Only the same signal and diagnostic state may share painted parts."""
    groups: dict[tuple[str, str, bool, str], dict[EdgeKey, tuple[Point, ...]]] = {}
    for key, route in sorted(routes.items()):
        points = simplify(route.points)
        groups.setdefault((*key[:2], route.blocked, route.reason), {})[key] = points
    parts: list[WirePart] = []
    junctions: list[tuple[Point, frozenset[EdgeKey]]] = []
    for group in groups.values():
        raw = splitLines((a, b, key) for key, points in group.items()
                         for a, b in zip(points, points[1:]))
        directions: dict[Point, set[Point]] = defaultdict(set)
        ownersAt: dict[Point, set[EdgeKey]] = defaultdict(set)
        for part in raw:
            a, b = part.points
            for start, end in ((a, b), (b, a)):
                dx, dy = end[0] - start[0], end[1] - start[1]
                directions[start].add((0 if dx == 0 else dx / abs(dx),
                                       0 if dy == 0 else dy / abs(dy)))
                ownersAt[start].update(part.owners)
        branches = {point for point, ways in directions.items() if len(ways) >= 3}
        junctions.extend((point, frozenset(ownersAt[point])) for point in sorted(branches))
        lines = []
        curves: dict[tuple[Point, ...], set[EdgeKey]] = defaultdict(set)
        for key, points in group.items():
            if len(points) < 2:
                continue
            current = points[0]
            for index in range(1, len(points) - 1):
                before, corner, after = points[index - 1:index + 2]
                incoming = abs(corner[0] - before[0]) + abs(corner[1] - before[1])
                outgoing = abs(after[0] - corner[0]) + abs(after[1] - corner[1])
                turning = (corner[0] - before[0]) * (after[1] - corner[1]) != (
                    corner[1] - before[1]) * (after[0] - corner[0])
                radius = min(8.0, incoming / 2, outgoing / 2) if turning and corner not in branches else 0
                if radius:
                    entry = tuple(corner[i] + (before[i] - corner[i]) * radius / incoming for i in (0, 1))
                    exitPoint = tuple(corner[i] + (after[i] - corner[i]) * radius / outgoing for i in (0, 1))
                    # Explicit coordinates retain the Point type for static checkers.
                    a, b = (entry[0], entry[1]), (exitPoint[0], exitPoint[1])
                    lines.append((current, a, key))
                    curve = (a, corner, b)
                    curves[min(curve, tuple(reversed(curve)))].add(key)
                    current = b
                else:
                    lines.append((current, corner, key))
                    current = corner
            lines.append((current, points[-1], key))
        parts.extend(splitLines(lines))
        parts.extend(WirePart(curve, frozenset(owners)) for curve, owners in sorted(curves.items()))
    return WireGeometry(tuple(parts), tuple(junctions))


def focusEdges(
    keys: Iterable[EdgeKey], selectedNodes: Iterable[str] = (),
    selectedEdges: Iterable[EdgeKey] = (), port: tuple[str, str, str] | None = None,
    preview: Iterable[EdgeKey] | None = None,
) -> frozenset[EdgeKey]:
    edges, nodes = frozenset(keys), frozenset(selectedNodes)
    selected = edges.intersection(selectedEdges)
    if preview is not None:
        return selected.union(edges.intersection(preview))
    if port is not None:
        node, direction, name = port
        side = 0 if direction == "output" else 2
        return selected.union(key for key in edges if key[side:side + 2] == (node, name))
    return selected.union(key for key in edges if key[0] in nodes or key[2] in nodes)


def edgeStyle(*, selected=False, focused=False, dimmed=False, blocked=False):
    if selected:
        return SELECTED_COLOR, 3.2, 4
    if blocked:
        return ERROR_COLOR, 2.0, 3
    if focused:
        return FOCUS_COLOR, 2.2, 2
    if dimmed:
        return DIM_COLOR, 1.2, 0
    return NORMAL_COLOR, 1.6, 1
