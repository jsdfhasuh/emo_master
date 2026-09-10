from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable


@dataclass(frozen=True)
class NodeGeometry:
    width: float
    height: float
    header: float
    rowHeight: float
    inputWidth: float
    outputWidth: float


def measureNode(model, textWidth: Callable[[str], float] | None = None,
                titleWidth: Callable[[str], float] | None = None,
                lineHeight: float = 18.0) -> NodeGeometry:
    measure = textWidth or (lambda value: sum(14 if ord(c) > 127 else 7 for c in value))
    titleMeasure = titleWidth or measure
    left = max((measure(name) for name in model.inputPorts), default=0.0)
    right = max((measure(name) for name in model.outputPorts), default=0.0)
    both = bool(model.inputPorts and model.outputPorts)
    portWidth = left + right + (76 if both else 56)
    width = min(420.0, max(260.0, math.ceil(titleMeasure(model.title)) + 32, portWidth))
    rowHeight = max(26.0, math.ceil(lineHeight) + 8)
    header = max(40.0, math.ceil(lineHeight) + 20)
    operatorId = getattr(model, "operatorId", "")
    implicitBranch = operatorId == "" and set(model.outputPorts) in (
        {"true", "false"}, {"case0", "case1", "case2", "case3", "default"},
    )
    if operatorId in {"vision.flow.if", "vision.flow.switch"} or implicitBranch:
        header += math.ceil(lineHeight) + 4
    available = width - (76 if both else 56)
    if both:
        leftShare = available * left / max(1, left + right)
        inputWidth = min(left, max(available * 0.25, min(available * 0.75, leftShare)))
        outputWidth = available - inputWidth
    else:
        inputWidth = outputWidth = available
    rows = max(len(model.inputPorts), len(model.outputPorts), 1)
    return NodeGeometry(width, header + rows * rowHeight + 12, header, rowHeight,
                        inputWidth, outputWidth)


def gridPositions(geometries: dict[str, NodeGeometry], columns: int = 4):
    columns = max(1, columns)
    keys = list(geometries)
    widths = [max((geometries[key].width for index, key in enumerate(keys)
                   if index % columns == col), default=0) for col in range(columns)]
    result = {}
    y = 20.0
    for start in range(0, len(keys), columns):
        row = keys[start:start + columns]
        x = 20.0
        for col, key in enumerate(row):
            result[key] = (x, y)
            x += widths[col] + 64
        y += max(geometries[key].height for key in row) + 48
    return result
