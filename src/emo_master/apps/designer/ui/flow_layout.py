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


def flowPositions(nodes: Mapping[str, LayoutNode], edges: Iterable[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    connections = [(source, target) for source, target in edges if source in nodes and target in nodes]
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
            for sweep, adjacent in ((levels, backward), (list(reversed(levels)), forward)):
                for level in sweep:
                    def score(node: str) -> tuple[float, str]:
                        neighbours = [order[n] for n in adjacent[node] if rank[n] != level]
                        return (sum(neighbours) / len(neighbours) if neighbours else order[node], node)
                    layers[level].sort(key=score)
                    order.update({node: float(i) for i, node in enumerate(layers[level])})
        heights = {level: sum(nodes[n].height for n in layers[level]) + 64 * (len(layers[level]) - 1)
                   for level in levels}
        height = max(heights.values())
        left = 20.0
        for level in levels:
            y = top + (height - heights[level]) / 2
            for node in layers[level]:
                positions[node] = (left, y)
                y += nodes[node].height + 64
            members = set(component)
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
