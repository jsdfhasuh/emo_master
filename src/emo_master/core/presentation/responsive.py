"""Width-dependent layout geometry, independent of Qt and project sessions."""
from dataclasses import dataclass
from typing import Callable

from .models import ResponsiveItem, ResponsiveLayout


@dataclass(frozen=True)
class Measurement:
    key: str
    responsive: ResponsiveItem
    hintWidth: float = 240
    hintHeight: float = 64
    minimumWidth: float = 0
    minimumHeight: float = 0
    heightForWidth: Callable[[float], float] | None = None


@dataclass(frozen=True)
class LayoutBox:
    x: float
    y: float
    width: float
    height: float


def _height(item, width):
    measured = item.heightForWidth(width) if item.heightForWidth is not None else item.hintHeight
    return max(0, measured, item.minimumHeight, item.responsive.minHeight, item.responsive.preferredHeight)


def _preferred(item):
    return max(item.responsive.minWidth, item.responsive.preferredWidth or item.hintWidth)


def _alignment(item, layout):
    return layout.alignment if item.responsive.alignSelf == 'auto' else item.responsive.alignSelf


def _offset(alignment, space):
    return max(0, space) * {'center': .5, 'end': 1}.get(alignment, 0)


def _justify(layout, count, space):
    gap = layout.spacing
    if layout.justify == 'space-between' and count > 1:
        gap += max(0, space) / (count - 1)
    return _offset(layout.justify, space), gap


def solveResponsive(layout: ResponsiveLayout, items: list[Measurement], width: float,
                    height: float | None = None) -> tuple[list[LayoutBox], float]:
    """Return boxes in input order and required content height.

    minWidth is a wrapping threshold, not a forced overflowing QWidget minimum.
    A card narrower than that threshold occupies the available width. Content
    height and height-for-width remain authoritative, including wrapped titles.
    """
    width = max(0, width)
    if not items:
        return [], 0
    if layout.direction == 'vertical':
        widths = [width if item.responsive.width == 'fill' or _alignment(item, layout) == 'stretch'
                  else min(width, _preferred(item)) for item in items]
        heights = [_height(item, w) for item, w in zip(items, widths)]
        total = sum(heights) + layout.spacing * (len(items) - 1)
        y, gap = _justify(layout, len(items), max(0, (height or total) - total))
        boxes = []
        for item, w, h in zip(items, widths, heights):
            boxes.append(LayoutBox(_offset(_alignment(item, layout), width - w), y, w, h))
            y += h + gap
        return boxes, total

    # Horizontal rows wrap using minimum widths; wrap rows use preferred widths.
    bases = [min(width, max(0, item.responsive.minWidth) if layout.direction == 'horizontal'
                 and item.responsive.width == 'fill' else _preferred(item)) for item in items]
    if layout.equalWidth:
        base = max(bases, default=0)
        bases = [base] * len(items)
    lines, start, used = [], 0, 0.0
    for index, base in enumerate(bases):
        added = base + (layout.spacing if index > start else 0)
        if index > start and used + added > width + .001:
            lines.append((start, index))
            start, used = index, base
        else:
            used += added
    lines.append((start, len(items)))
    boxes, y = [], 0
    for start, stop in lines:
        row = items[start:stop]
        widths = list(bases[start:stop])
        remaining = max(0, width - sum(widths) - layout.spacing * (len(row) - 1))
        weights = [1 if layout.equalWidth else item.responsive.weight
                   if item.responsive.width == 'fill' else 0 for item in row]
        totalWeight = sum(weights)
        if totalWeight:
            widths = [w + remaining * weight / totalWeight for w, weight in zip(widths, weights)]
            remaining = 0
        heights = [_height(item, w) for item, w in zip(row, widths)]
        rowHeight = max(heights, default=0)
        x, gap = _justify(layout, len(row), remaining)
        for item, w, h in zip(row, widths, heights):
            alignment = _alignment(item, layout)
            actualHeight = rowHeight if alignment == 'stretch' else h
            boxes.append(LayoutBox(x, y + _offset(alignment, rowHeight - h), w, actualHeight))
            x += w + gap
        y += rowHeight + layout.spacing
    return boxes, max(0, y - layout.spacing)
