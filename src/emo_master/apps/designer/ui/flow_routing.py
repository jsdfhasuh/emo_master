"""Obstacle-aware orthogonal visibility graph, with no Qt or project dependency.

Routing is derived state. The additional 8px corner reserve ensures rounding never
cuts into the required 12px clearance. Only fanouts of the exact same source port
may share trunks freely; obstacle avoidance remains a hard constraint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from heapq import heappop, heappush
from collections.abc import Generator, Iterator, Mapping, Sequence

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


@dataclass(frozen=True)
class OccupiedSegment:
    """A routed segment owned by one (node ID, output port) signal."""
    start: Point
    end: Point
    signal: tuple[str, str]


Segment = tuple[Point, Point] | OccupiedSegment


@dataclass
class _Traffic:
    count: int = 0
    costs: dict[tuple[str, str] | None, float] = field(default_factory=dict)


TrafficCache = dict[tuple[int, int], _Traffic]


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


def _trafficCost(
    a: Point, b: Point, occupied: Sequence[Segment],
    entry: _Traffic, signal: tuple[str, str],
) -> float:
    horizontal = a[1] == b[1]
    for segment in occupied[entry.count:]:
        if isinstance(segment, OccupiedSegment):
            c, d = segment.start, segment.end
            owner: tuple[str, str] | None = segment.signal
        else:
            c, d = segment
            owner = None
        cost = 0.0
        if horizontal == (c[1] == d[1]):
            axis = 0 if horizontal else 1
            other = 1 - axis
            if a[other] == c[other]:
                overlap = min(max(a[axis], b[axis]), max(c[axis], d[axis])) - max(min(a[axis], b[axis]), min(c[axis], d[axis]))
                cost += max(0, overlap) * 5
        else:
            h1, h2, v1, v2 = (a, b, c, d) if horizontal else (c, d, a, b)
            if min(h1[0], h2[0]) < v1[0] <= max(h1[0], h2[0]) and min(v1[1], v2[1]) < h1[1] <= max(v1[1], v2[1]):
                cost += 80
        if cost:
            entry.costs[owner] = entry.costs.get(owner, 0.0) + cost
    entry.count = len(occupied)
    return sum(cost for owner, cost in entry.costs.items() if owner != signal)


class OrthogonalRouter:
    """One shared visibility grid per scene snapshot, reused by all edges.

    Consecutive visible intersections on the same row/column are connected. A*
    includes incoming direction in its state so bend penalties remain correct.
    """
    def __init__(self, nodes: Mapping[str, Rect], requests: Sequence[RouteRequest]) -> None:
        self.nodes = nodes
        self.obstacles = {key: rect.expanded(20) for key, rect in nodes.items()}
        # Local links establish the channels before long result/bypass links.
        # Geometry, not arbitrary IDs or port spelling, determines priority.
        self.requests = sorted(requests, key=lambda r: (
            r.end[0] <= r.start[0], abs(r.end[0] - r.start[0]),
            r.start[1], r.end[1], r.key))
        self.stubs: dict[EdgeKey, tuple[Point, Point]] = {}
        xs: set[float] = set()
        ys: set[float] = set()
        for rect in self.obstacles.values():
            xs.update((rect.left, rect.right))
            ys.update((rect.top, rect.bottom))
        outgoing: dict[tuple[str, str], int] = {}
        incoming: dict[EdgeKey, int] = {}
        # Target lanes follow actual port order, never alphabetical port names.
        # This matters when e.g. frame sorts before image but is drawn below it.
        targets: dict[str, list[RouteRequest]] = {}
        sources: dict[str, dict[str, list[RouteRequest]]] = {}
        for request in self.requests:
            targets.setdefault(request.key[2], []).append(request)
            sources.setdefault(request.key[0], {}).setdefault(request.key[1], []).append(request)
        for source, ports in sources.items():
            # A fanout has one exit lane. Downward wires from lower ports
            # turn first; upward wires from upper ports turn first.
            upward: list[tuple[float, str]] = []
            downwardPorts: list[tuple[float, str]] = []
            for port, group in ports.items():
                (upward if sum(r.end[1] - r.start[1] for r in group) < 0
                 else downwardPorts).append((group[0].start[1], port))
            orderedPorts = sorted(upward) + sorted(downwardPorts, reverse=True)
            outgoing.update({(source, port): lane for lane, (_, port) in enumerate(orderedPorts)})
        for group in targets.values():
            downward = sum(r.end[1] - r.start[1] for r in group) >= 0
            ordered = sorted(group, key=lambda r: (r.end[1], r.key), reverse=not downward)
            incoming.update({request.key: lane for lane, request in enumerate(ordered)})
        for request in self.requests:
            source, port, target, _ = request.key
            outLane, inLane = outgoing[(source, port)], incoming[request.key]
            sourceX, targetX = self.obstacles[source].right, self.obstacles[target].left
            # Compress only the lane spacing in a narrow manual layout. A
            # reservation must not itself manufacture a blocked port exit.
            exitSpace = min((rect.left - sourceX for key, rect in self.obstacles.items()
                             if key != source and rect.left >= sourceX
                             and rect.top < request.start[1] < rect.bottom), default=float('inf'))
            entrySpace = min((targetX - rect.right for key, rect in self.obstacles.items()
                              if key != target and rect.right <= targetX
                              and rect.top < request.end[1] < rect.bottom), default=float('inf'))
            exitPitch = min(10.0, exitSpace / (2 * len(sources[source])))
            entryPitch = min(10.0, entrySpace / (2 * len(targets[target])))
            start = (sourceX + exitPitch * outLane, request.start[1])
            end = (targetX - entryPitch * inLane, request.end[1])
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
        return dict(item for item in self.iterRoutes() if item is not None)

    def iterRoutes(self, *, stepNodes: int = 0) -> Iterator[tuple[EdgeKey, Route] | None]:
        """Yield edges and optional search checkpoints for interactive callers."""
        occupied: list[Segment] = []
        # Occupancy only grows. Each grid segment caches contributions by
        # signal so every fanout reuses the cache without charging its own trunk.
        traffic: TrafficCache = {}
        for request in self.requests:
            route = yield from self._routeSteps(request, occupied, traffic, stepNodes)
            if not route.blocked:
                occupied.extend(OccupiedSegment(a, b, request.key[:2])
                                for a, b in zip(route.points, route.points[1:]))
            yield request.key, route

    def route(
        self, request: RouteRequest, occupied: Sequence[Segment] = (), *,
        traffic: TrafficCache | None = None,
    ) -> Route:
        steps = self._routeSteps(request, occupied, traffic, 0)
        while True:
            try:
                next(steps)
            except StopIteration as result:
                return result.value

    def _routeSteps(
        self, request: RouteRequest, occupied: Sequence[Segment],
        traffic: TrafficCache | None, stepNodes: int,
    ) -> Generator[None, None, Route]:
        start, end = self.stubs[request.key]
        source, _, target, _ = request.key
        blocked = (start not in self.indices or end not in self.indices
                   or any(intersects(request.start, start, rect.expanded(12)) for key, rect in self.nodes.items() if key != source)
                   or any(intersects(end, request.end, rect.expanded(12)) for key, rect in self.nodes.items() if key != target))
        if not blocked:
            points = yield from self._searchSteps(start, end, occupied, traffic, stepNodes, request.key[:2])
            if points:
                return Route(simplify([request.start, *points, request.end]))
        middle = (start[0], end[1])
        return Route(simplify([request.start, start, middle, end, request.end]), True,
                     "连线受阻：节点重叠或端口出口没有安全通道，请移动节点或执行自动布局")

    def _searchSteps(
        self, start: Point, end: Point, occupied: Sequence[Segment],
        traffic: TrafficCache | None, stepNodes: int,
        signal: tuple[str, str],
    ) -> Generator[None, None, list[Point]]:
        initial = (self.indices[start], 0)
        target = self.indices[end]
        distance = {initial: 0.0}
        parents: dict[tuple[int, int], tuple[int, int]] = {}
        queue = [(0.0, 0.0, initial)]
        if traffic is None:
            traffic = {}
        visited = 0
        while queue:
            visited += 1
            if stepNodes > 0 and visited % stepNodes == 0:
                # One long A* search must not monopolize an interactive slice.
                yield None
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
                entry = traffic.get(segment)
                if entry is None:
                    entry = traffic[segment] = _Traffic()
                trafficCost = _trafficCost(self.points[index], self.points[neighbour], occupied, entry, signal)
                extra = 32 if direction != incoming else 0
                if neighbour == target and direction != 0:
                    extra += 32
                # A few unavoidable crossings are clearer than a long reverse
                # dogleg. Return edges/self-loops still have a finite cost.
                reverse = max(0.0, self.points[index][0] - self.points[neighbour][0])
                nextCost = cost + length + extra + trafficCost + reverse * 3
                nextState = (neighbour, direction)
                if nextCost < distance.get(nextState, float("inf")):
                    distance[nextState] = nextCost
                    parents[nextState] = state
                    point = self.points[neighbour]
                    heuristic = abs(point[0] - end[0]) + abs(point[1] - end[1])
                    heappush(queue, (nextCost + heuristic, nextCost, nextState))
        return []
