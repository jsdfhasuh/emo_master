"""Small, Qt-free geometry helpers shared by the two design canvases."""
from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self):
        return self.x + self.width

    @property
    def bottom(self):
        return self.y + self.height

    def translated(self, dx, dy):
        return Box(self.x + dx, self.y + dy, self.width, self.height)


def enclosingBox(boxes: Iterable[Box]) -> Box:
    boxes = tuple(boxes)
    left, top = min(b.x for b in boxes), min(b.y for b in boxes)
    return Box(left, top, max(b.right for b in boxes) - left,
               max(b.bottom for b in boxes) - top)


def alignedOffsets(boxes: dict[str, Box], mode: str) -> dict[str, tuple[float, float]]:
    axes = {'left': (0, 0), 'hcenter': (0, .5), 'right': (0, 1),
            'top': (1, 0), 'vcenter': (1, .5), 'bottom': (1, 1)}
    axis, fraction = axes[mode]
    if len(boxes) < 2:
        return {}
    bounds = enclosingBox(boxes.values())
    def anchor(box):
        return box.y + box.height * fraction if axis else box.x + box.width * fraction
    target = anchor(bounds)
    return {key: ((0., target - anchor(box)) if axis else (target - anchor(box), 0.))
            for key, box in boxes.items()}


def distributedOffsets(boxes: dict[str, Box], axis: str) -> dict[str, tuple[float, float]]:
    if axis not in {'horizontal', 'vertical'}:
        raise ValueError('未知分布方向')
    if len(boxes) < 3:
        return {}
    vertical = axis == 'vertical'
    start = (lambda box: box.y) if vertical else (lambda box: box.x)
    size = (lambda box: box.height) if vertical else (lambda box: box.width)
    ordered = sorted(boxes, key=lambda key: (start(boxes[key]), key))
    first, last = boxes[ordered[0]], boxes[ordered[-1]]
    available = start(last) - start(first) - sum(size(boxes[key]) for key in ordered[:-1])
    if available < -1e-6:
        raise ValueError('等距分布空间不足，请先拉开两端对象后重试')
    gap = max(0., available) / (len(ordered) - 1)
    position = start(first) + size(first) + gap
    offsets = {}
    for key in ordered[1:-1]:
        delta = position - start(boxes[key])
        offsets[key] = (0., delta) if vertical else (delta, 0.)
        position += size(boxes[key]) + gap
    return offsets


def snapOffset(moving: Box, references: Iterable[Box], tolerance: float, reach: float, *, preferred=(None, None)):
    """Nearest edge/center per axis; callers convert screen pixels to their units.

    Geometry alone is derived here. The caller decides whether to apply the
    offset, draw guides, or only preview a legal responsive placement.
    """
    references = tuple(references)
    matches = []
    for axis in (0, 1):
        def anchors(box):
            return ((box.y, box.y + box.height / 2, box.bottom) if axis else
                    (box.x, box.x + box.width / 2, box.right))
        candidates = []
        for index, reference in enumerate(references):
            distance = (max(0., moving.x - reference.right, reference.x - moving.right) if axis else
                        max(0., moving.y - reference.bottom, reference.y - moving.bottom))
            if distance > reach:
                continue
            for own in anchors(moving):
                for target in anchors(reference):
                    delta = target - own
                    if abs(delta) <= tolerance:
                        # Keep close competing guides stable without extending
                        # the release distance or making the pointer sticky.
                        score = abs(delta) - (tolerance / 4 if target == preferred[axis] else 0)
                        candidates.append((score, distance, index, target, delta, reference))
        matches.append(min(candidates, key=lambda item: item[:4]) if candidates else None)
    dx, dy = (match[4] if match else 0. for match in matches)
    placed = moving.translated(dx, dy)
    guides = []
    for axis, match in enumerate(matches):
        if match is None:
            continue
        target, reference = match[3], match[5]
        if axis:
            guides.append((min(placed.x, reference.x), target, max(placed.right, reference.right), target))
        else:
            guides.append((target, min(placed.y, reference.y), target, max(placed.bottom, reference.bottom)))
    return dx, dy, tuple(guides)
