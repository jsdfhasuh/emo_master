"""Obstacle-aware orthogonal visibility graph, with no Qt or project dependency.

Routing is derived state. The additional 8px corner reserve ensures rounding never
cuts into the required 12px clearance. Costs discourage crossings, shared trunks,
bends and length, in that order of weight; obstacle avoidance is a hard constraint.
"""
from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from collections.abc import Mapping, Sequence

Point = tuple[float, float]
EdgeKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    def expanded(self, amount: float) -> Rect:
        return Rect(self.left - amount, self.top - amount, self.right + amount, self.bottom + amount)

    def contains(self, point: Point) -> bool:
        return self.left < point[0] < self.right and self.top < point[1] < self.bottom


@dataclass(frozen=True)
class RouteRequest:
    key: EdgeKey
    start: Point
    end: Point


@dataclass(frozen=True)
class Route:
    points: tuple[Point, ...]
    blocked: bool = False
    reason: str = ""


def intersects(a: Point, b: Point, rect: Rect) -> bool:
    """Open interior intersection; travelling along a reserved boundary is safe."""
    if a[1] == b[1]:
        return rect.top < a[1] < rect.bottom and max(a[0], b[0]) > rect.left and min(a[0], b[0]) < rect.right
    if a[0] == b[0]:
        return rect.left < a[0] < rect.right and max(a[1], b[1]) > rect.top and min(a[1], b[1]) < rect.bottom
    raise ValueError("orthogonal segments required")


def simplify(points: Sequence[Point]) -> tuple[Point, ...]:
    result: list[Point] = []
    for point in points:
        if result and point == result[-1]:
            continue
        while len(result) >= 2:
            a, b = result[-2:]
            if (a[0] == b[0] == point[0] and (b[1] - a[1]) * (point[1] - b[1]) >= 0
                    or a[1] == b[1] == point[1] and (b[0] - a[0]) * (point[0] - b[0]) >= 0):
                result.pop()
            else:
                break
        result.append(point)
    return tuple(result)


def _trafficCost(a: Point, b: Point, occupied: Sequence[tuple[Point, Point]]) -> float:
    cost = 0.0
    horizontal = a[1] == b[1]
    for c, d in occupied:
        if horizontal == (c[1] == d[1]):
            axis = 0 if horizontal else 1
            other = 1 - axis
            if a[other] == c[other]:
                overlap = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(min(a[axis], b[axis]), min(c[axis], d[axis]))
                cost += max(0, overlap) * 5
        else:
            h1, h2, v1, v2 = (a, b, c, d) if horizontal else (c, d, a, b)
            if min(h1[0], h2[0]) < v1[0] <= max(h1[0], h2[0]) and min(v1[1], v2[1]) < h1[1] <= max(v1[1], v2[1]):
                cost += 240
    return cost


class OrthogonalRouter:
    """One shared visibility grid per scene snapshot, reused by all edges.

    Consecutive visible intersections on the same row/column are connected. A*
    includes incoming direction in its state so bend penalties remain correct.
    """
    def __init__(self, nodes: Mapping[str, Rect], requests: Sequence[RouteRequest]) -> None:
        self.nodes = nodes
        self.obstacles = {key: rect.expanded(20) for key, rect in nodes.items()}
        self.requests = sorted(requests, key=lambda request: request.key)
        self.stubs: dict[EdgeKey, tuple[Point, Point]] = {}
        xs: set[float] = set()
        ys: set[float] = set()
        for rect in self.obstacles.values():
            xs.update((rect.left, rect.right))
            ys.update((rect.top, rect.bottom))
        outgoing: dict[str, int] = {}
        incoming: dict[EdgeKey, int] = {}
        # Target lanes follow actual port order, never alphabetical port names.
        # This matters when e.g. frame sorts before image but is drawn below it.
        targets: dict[str, list[RouteRequest]] = {}
        for request in self.requests:
            targets.setdefault(request.key[2], []).append(request)
        for group in targets.values():
            downward = sum(r.end[1] - r.start[1] for r in group) >= 0
            ordered = sorted(group, key=lambda r: (r.end[1], r.key), reverse=not downward)
            incoming.update({request.key: lane for lane, request in enumerate(ordered)})
        for request in self.requests:
            source, _, target, _ = request.key
            outLane, inLane = outgoing.get(source, 0), incoming[request.key]
            outgoing[source] = outLane + 1
            start = (self.obstacles[source].right + 10 * outLane, request.start[1])
            end = (self.obstacles[target].left - 10 * inLane, request.end[1])
            self.stubs[request.key] = (start, end)
            xs.update((start[0], end[0]))
            ys.update((start[1], end[1]))
        if xs:
            xs.update((min(xs) - 32, max(xs) + 32))
            ys.update((min(ys) - 32, max(ys) + 32))
        self.points: list[Point] = []
        self.indices: dict[Point, int] = {}
        self.links: list[list[tuple[int, int, float]]] = []
        # Row/column interval tests are shared rather than repeated per edge.
        previousColumn: dict[float, int] = {}
        for y in sorted(ys):
            rowRects = [rect for rect in self.obstacles.values() if rect.top < y < rect.bottom]
            previous: int | None = None
            for x in sorted(xs):
                point = (x, y)
                if any(rect.left < x < rect.right for rect in rowRects):
                    previous = None
                    previousColumn.pop(x, None)
                    continue
                index = len(self.points)
                self.points.append(point)
                self.indices[point] = index
                self.links.append([])
                for neighbour, direction in ((previous, 0), (previousColumn.get(x), 1)):
                    if neighbour is None:
                        continue
                    other = self.points[neighbour]
                    if any(intersects(point, other, rect) for rect in self.obstacles.values()):
                        continue
                    length = abs(x - other[0]) + abs(y - other[1])
                    self.links[index].append((neighbour, direction, length))
                    self.links[neighbour].append((index, direction, length))
                previous = index
                previousColumn[x] = index

    def routeAll(self) -> dict[EdgeKey, Route]:
        occupied: list[tuple[Point, Point]] = []
        routes: dict[EdgeKey, Route] = {}
        for request in self.requests:
            route = self.route(request, occupied)
            routes[request.key] = route
            if not route.blocked:
                occupied.extend(zip(route.points, route.points[1:]))
        return routes

    def route(self, request: RouteRequest, occupied: Sequence[tuple[Point, Point]] = ()) -> Route:
        start, end = self.stubs[request.key]
        source, _, target, _ = request.key
        blocked = (start not in self.indices or end not in self.indices
                   or any(intersects(request.start, start, rect.expanded(12)) for key, rect in self.nodes.items() if key != source)
                   or any(intersects(end, request.end, rect.expanded(12)) for key, rect in self.nodes.items() if key != target))
        if not blocked:
            points = self._search(start, end, occupied)
            if points:
                return Route(simplify([request.start, *points, request.end]))
        middle = (start[0], end[1])
        return Route(simplify([request.start, start, middle, end, request.end]), True,
                     "连线受阻：节点重叠或端口出口没有安全通道，请移动节点或执行自动布局")

    def _search(self, start: Point, end: Point, occupied: Sequence[tuple[Point, Point]]) -> list[Point]:
        initial = (self.indices[start], 0)
        target = self.indices[end]
        distance = {initial: 0.0}
        parents: dict[tuple[int, int], tuple[int, int]] = {}
        queue = [(0.0, 0.0, initial)]
        traffic: dict[tuple[int, int], float] = {}
        while queue:
            _, cost, state = heappop(queue)
            if cost != distance[state]:
                continue
            index, incoming = state
            if index == target:
                result = [self.points[index]]
                while state in parents:
                    state = parents[state]
                    result.append(self.points[state[0]])
                return list(reversed(result))
            for neighbour, direction, length in self.links[index]:
                # Never reverse along an owner's stub. In particular a target
                # approached from its right would draw a tiny U-turn over the
                # final horizontal ingress, obscuring neighbouring port lines.
                if index == initial[0] and self.points[neighbour][0] < start[0]:
                    continue
                if neighbour == target and self.points[index][0] > end[0]:
                    continue
                segment = (min(index, neighbour), max(index, neighbour))
                if segment not in traffic:
                    traffic[segment] = _trafficCost(self.points[index], self.points[neighbour], occupied)
                extra = 32 if direction != incoming else 0
                if neighbour == target and direction != 0:
                    extra += 32
                nextCost = cost + length + extra + traffic[segment]
                nextState = (neighbour, direction)
                if nextCost < distance.get(nextState, float("inf")):
                    distance[nextState] = nextCost
                    parents[nextState] = state
                    point = self.points[neighbour]
                    heuristic = abs(point[0] - end[0]) + abs(point[1] - end[1])
                    heappush(queue, (nextCost + heuristic, nextCost, nextState))
        return []
