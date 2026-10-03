from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, cast

import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    CoordinateSpace2D,
    IDENTITY_TRANSFORM_TO_SOURCE,
    affineToHomography,
    normalizeHomography,
)


AffineTransform2D = tuple[float, float, float, float, float, float]
HomographyTransform2D = tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
]
Transform2D = AffineTransform2D | HomographyTransform2D


def frameForInput(
    inputs: Mapping[str, object],
    width: int,
    height: int,
    *,
    portName: str = "frame",
    fallback: BBox2D | None = None,
) -> BBox2D:
    if portName not in inputs:
        return fallback if fallback is not None else defaultFrame(width, height)
    frame = BBox2D.fromPayload(inputs[portName])
    space = _spaceForImage(frame.coordinateSpace, width, height)
    normalized = BBox2D(frame.x, frame.y, frame.width, frame.height, space)
    _validateContentBounds(normalized, width, height)
    return normalized


def defaultFrame(
    width: int,
    height: int,
    *,
    reference: str = "sourceImage",
    sourceId: str = "sourceImage",
) -> BBox2D:
    space = CoordinateSpace2D(
        reference=reference,
        sourceId=sourceId,
        imageWidth=width,
        imageHeight=height,
    )
    return BBox2D(0.0, 0.0, float(width), float(height), space)


def transformedFrame(
    inputFrame: BBox2D,
    outputWidth: int,
    outputHeight: int,
    outputToInput: Transform2D,
    contentRect: tuple[float, float, float, float],
    *,
    reference: str,
) -> BBox2D:
    inputSpace = inputFrame.coordinateSpace
    inputTransform = cast(
        Transform2D,
        inputSpace.homographyToSource
        if inputSpace.homographyToSource is not None
        else inputSpace.transformToSource or IDENTITY_TRANSFORM_TO_SOURCE,
    )
    transformToSource = composeTransform(inputTransform, outputToInput)
    if len(transformToSource) == 9:
        outputSpace = CoordinateSpace2D(
            origin=inputSpace.origin,
            xAxis=inputSpace.xAxis,
            yAxis=inputSpace.yAxis,
            unit=inputSpace.unit,
            reference=reference,
            sourceId=inputSpace.sourceId,
            imageWidth=outputWidth,
            imageHeight=outputHeight,
            transformToSource=None,
            homographyToSource=transformToSource,
        )
    else:
        outputSpace = CoordinateSpace2D(
            origin=inputSpace.origin,
            xAxis=inputSpace.xAxis,
            yAxis=inputSpace.yAxis,
            unit=inputSpace.unit,
            reference=reference,
            sourceId=inputSpace.sourceId,
            imageWidth=outputWidth,
            imageHeight=outputHeight,
            transformToSource=transformToSource,
        )
    frame = BBox2D(*contentRect, outputSpace)
    _validateContentBounds(frame, outputWidth, outputHeight)
    return frame


def composeAffine(
    outer: tuple[float, ...],
    inner: tuple[float, ...],
) -> AffineTransform2D:
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def composeTransform(outer: Transform2D, inner: Transform2D) -> Transform2D:
    if len(outer) == 6 and len(inner) == 6:
        return composeAffine(outer, inner)
    outerMatrix = affineToHomography(outer) if len(outer) == 6 else tuple(outer)
    innerMatrix = affineToHomography(inner) if len(inner) == 6 else tuple(inner)
    values = tuple(
        sum(outerMatrix[row * 3 + k] * innerMatrix[k * 3 + column] for k in range(3))
        for row in range(3)
        for column in range(3)
    )
    return normalizeHomography(values, "composed homography")  # type: ignore[return-value]


def frameMask(
    frame: BBox2D,
    width: int,
    height: int,
) -> np.ndarray[Any, Any]:
    _validateContentBounds(frame, width, height)
    left, top, right, bottom = framePixelBounds(frame, width, height)
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[top:bottom, left:right] = 255
    return mask


def framePixelBounds(
    frame: BBox2D,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left = max(0, min(width, math.floor(frame.x)))
    top = max(0, min(height, math.floor(frame.y)))
    right = max(0, min(width, math.ceil(frame.x + frame.width)))
    bottom = max(0, min(height, math.ceil(frame.y + frame.height)))
    return left, top, right, bottom


def intersectFrames(first: BBox2D, second: BBox2D) -> BBox2D:
    if not coordinateSpacesEquivalent(
        first.coordinateSpace,
        second.coordinateSpace,
    ):
        raise ValueError("frame coordinate spaces do not match")
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    return BBox2D(
        left,
        top,
        max(0.0, right - left),
        max(0.0, bottom - top),
        first.coordinateSpace,
    )


def coordinateSpacesEquivalent(
    first: CoordinateSpace2D,
    second: CoordinateSpace2D,
) -> bool:
    firstMatrix = normalizeHomography(first.matrixToSource(), "first coordinate transform")
    secondMatrix = normalizeHomography(
        second.matrixToSource(), "second coordinate transform"
    )
    return (
        first.origin == second.origin
        and first.xAxis == second.xAxis
        and first.yAxis == second.yAxis
        and first.unit == second.unit
        and first.sourceId == second.sourceId
        and first.imageWidth == second.imageWidth
        and first.imageHeight == second.imageHeight
        and all(
            math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
            for left, right in zip(
                firstMatrix,
                secondMatrix,
                strict=True,
            )
        )
    )


def frameReference(runtimeContext: Mapping[str, object], operatorId: str) -> str:
    nodeId = runtimeContext.get("nodeId")
    if isinstance(nodeId, str) and nodeId != "":
        return f"node:{nodeId}"
    return operatorId


def _spaceForImage(
    space: CoordinateSpace2D,
    width: int,
    height: int,
) -> CoordinateSpace2D:
    if space.imageWidth is not None and (
        space.imageWidth != width or space.imageHeight != height
    ):
        raise ValueError("frame coordinateSpace dimensions do not match the image")
    return CoordinateSpace2D(
        origin=space.origin,
        xAxis=space.xAxis,
        yAxis=space.yAxis,
        unit=space.unit,
        reference=space.reference,
        sourceId=space.sourceId,
        imageWidth=width,
        imageHeight=height,
        transformToSource=space.transformToSource,
        homographyToSource=space.homographyToSource,
    )


def _validateContentBounds(frame: BBox2D, width: int, height: int) -> None:
    epsilon = 1e-9
    if (
        frame.x < -epsilon
        or frame.y < -epsilon
        or frame.x + frame.width > width + epsilon
        or frame.y + frame.height > height + epsilon
    ):
        raise ValueError("frame content region must lie inside the image")
