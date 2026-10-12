"""Deterministic graph layout in logical pixels; deliberately independent of Qt.

SCCs make invalid cyclic drafts drawable without changing their validation or edges.
Only explicit editor commands apply these positions.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from collections.abc import Iterable, Mapping


@dataclass(frozen=True)
class LayoutNode:
    width: float
    height: float
    kind: str = "operator"


@dataclass(frozen=True)
class LayoutEdge:
    """Port centres in node-local logical pixels, never saved scene positions."""
    source: str
    target: str
    sourceY: float
    targetY: float


def _packLayer(layer: list[str], nodes: Mapping[str, LayoutNode],
               desired: Mapping[str, tuple[float, float]]) -> dict[str, float]:
    """Weighted isotonic packing: nearest port alignment without node overlap."""
    offsets: dict[str, float] = {}
    offset = 0.0
    blocks: list[tuple[list[str], float, float]] = []
    for node in layer:
        offsets[node] = offset
        y, weight = desired[node]
        blocks.append(([node], (y - offset) * weight, weight))
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append((left[0] + right[0], left[1] + right[1], left[2] + right[2]))
        offset += nodes[node].height + 64
    return {node: value / weight + offsets[node]
            for members, value, weight in blocks for node in members}


def _weightedMedian(values: list[tuple[float, float]]) -> tuple[float, float]:
    weight = sum(w for _, w in values)
    remaining = weight / 2
    for value, w in sorted(values):
        remaining -= w
        if remaining <= 0:
            return value, weight
    return values[-1][0], weight


def flowPositions(nodes: Mapping[str, LayoutNode], edges: Iterable[tuple[str, str] | LayoutEdge]) -> dict[str, tuple[float, float]]:
    links = []
    for edge in edges:
        if isinstance(edge, LayoutEdge):
            link = edge
        else:
            source, target = edge
            if source not in nodes or target not in nodes:
                continue
            link = LayoutEdge(source, target, nodes[source].height / 2, nodes[target].height / 2)
        if link.source in nodes and link.target in nodes:
            links.append(link)
    links.sort(key=lambda edge: (edge.source, edge.target, edge.sourceY, edge.targetY))
    connections = [(edge.source, edge.target) for edge in links]
    incoming: dict[str, list[LayoutEdge]] = defaultdict(list)
    outgoing: dict[str, list[LayoutEdge]] = defaultdict(list)
    for edge in links:
        incoming[edge.target].append(edge)
        outgoing[edge.source].append(edge)
    forward: dict[str, set[str]] = {key: set() for key in nodes}
    backward: dict[str, set[str]] = {key: set() for key in nodes}
    for source, target in connections:
        if source in nodes and target in nodes:
            forward[source].add(target)
            backward[target].add(source)
    # Iterative Kosaraju avoids Python's recursion limit on long workflows.
    visited: set[str] = set()
    finish: list[str] = []
    for root in sorted(nodes):
        stack = [(root, False)]
        while stack:
            node, done = stack.pop()
            if done:
                finish.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((other, False) for other in sorted(forward[node], reverse=True) if other not in visited)
    groups: list[list[str]] = []
    owner: dict[str, int] = {}
    for root in reversed(finish):
        if root in owner:
            continue
        group: list[str] = []
        todo = [root]
        while todo:
            node = todo.pop()
            if node in owner:
                continue
            owner[node] = len(groups)
            group.append(node)
            todo.extend(sorted(backward[node], reverse=True))
        groups.append(sorted(group))
    rank: dict[str, int] = {}
    for index, group in enumerate(groups):
        start = max((rank[parent] + 1 for node in group for parent in backward[node]
                     if owner[parent] != index), default=0)
        for offset, node in enumerate(group):
            rank[node] = start + offset

    remaining = set(nodes)
    components: list[list[str]] = []
    isolated: list[str] = []
    while remaining:
        root = min(remaining)
        if not forward[root] and not backward[root]:
            isolated.append(root)
            remaining.remove(root)
            continue
        component: list[str] = []
        todo = [root]
        while todo:
            node = todo.pop()
            if node not in remaining:
                continue
            remaining.remove(node)
            component.append(node)
            todo.extend(sorted(forward[node] | backward[node], reverse=True))
        components.append(sorted(component))

    positions: dict[str, tuple[float, float]] = {}
    top = 20.0
    for component in sorted(components, key=lambda group: (-len(group), group)):
        last = max(rank[node] for node in component)
        # Valid connected boundaries occupy the group's entrance/exit, not the
        # last row of the editor's insertion-ordered node dictionary.
        for node in component:
            if nodes[node].kind == "workflow_input" and not backward[node]:
                rank[node] = 0
            if nodes[node].kind == "workflow_output" and not forward[node]:
                rank[node] = last
        layers: dict[int, list[str]] = defaultdict(list)
        for node in component:
            layers[rank[node]].append(node)
        levels = sorted(layers)
        order = {node: float(i) for layer in layers.values() for i, node in enumerate(layer)}
        for _ in range(8):
            for sweep, toRight in ((levels, True), (list(reversed(levels)), False)):
                for level in sweep:
                    def score(node: str) -> tuple[float, float, str]:
                        neighbours = []
                        for edge in (incoming[node] if toRight else outgoing[node]):
                            other = edge.source if toRight else edge.target
                            if rank[other] == level:
                                continue
                            port = edge.sourceY if toRight else edge.targetY
                            ownPort = edge.targetY if toRight else edge.sourceY
                            weight = 1 / abs(rank[other] - level) ** 2
                            neighbours.append((order[other] + (port - ownPort) /
                                               max(1, nodes[other].height, nodes[node].height), weight))
                        value = (sum(value * weight for value, weight in neighbours) / sum(weight for _, weight in neighbours)
                                 if neighbours else order[node])
                        return (value, order[node], node)
                    layers[level].sort(key=score)
                    order.update({node: float(i) for i, node in enumerate(layers[level])})
        heights = {level: sum(nodes[n].height for n in layers[level]) + 64 * (len(layers[level]) - 1)
                   for level in levels}
        height = max(heights.values())
        ys = {}
        for level in levels:
            y = (height - heights[level]) / 2
            for node in layers[level]:
                ys[node] = y
                y += nodes[node].height + 64
        # Short links dominate alignment. A distant workflow-output/bypass
        # must not pull the whole main chain away from its connected ports.
        for _ in range(8):
            for sweep, toRight in ((levels, True), (list(reversed(levels)), False)):
                for level in sweep:
                    desired = {}
                    for node in layers[level]:
                        neighbours = []
                        for edge in (incoming[node] if toRight else outgoing[node]):
                            other = edge.source if toRight else edge.target
                            if rank[other] == level:
                                continue
                            delta = edge.sourceY - edge.targetY
                            y = ys[other] + (delta if toRight else -delta)
                            neighbours.append((y, 1 / abs(rank[other] - level) ** 2))
                        desired[node] = (_weightedMedian(neighbours) if neighbours else (ys[node], 1.0))
                    ys.update(_packLayer(layers[level], nodes, desired))
        minY = min(ys.values())
        height = max(ys[node] + nodes[node].height for node in component) - minY
        left = 20.0
        members = set(component)
        for level in levels:
            for node in layers[level]:
                positions[node] = (left, top + ys[node] - minY)
            demand = sum(1 for source, target in connections
                         if source in members and rank[source] <= level < rank[target])
            left += max(nodes[n].width for n in layers[level]) + 120 + max(0, demand - 3) * 10
        top += height + 128
    # Unconnected nodes remain visible below all connected components.
    for offset in range(0, len(isolated), 4):
        row = isolated[offset:offset + 4]
        left = 20.0
        for node in row:
            positions[node] = (left, top)
            left += nodes[node].width + 120
        top += max(nodes[n].height for n in row) + 64
    return positions
