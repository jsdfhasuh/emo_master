from __future__ import annotations

import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Circle2D,
    ColorStatistics,
    CoordinateSpace2D,
    Line2D,
    Point2D,
    Polygon2D,
)
from emo_master.plugins.builtins.rgb_statistics.operator import RgbStatisticsOperator


def testRgbStatisticsConvertsBgrChannelsAndReturnsTypedPayload() -> None:
    image = np.asarray([[[10, 20, 30], [50, 60, 70]]], dtype=np.uint8)

    result = RgbStatisticsOperator().executeNode({"image": image}, {}, {})

    assert result["status"] == "ok"
    payload = result["outputs"]["statistics"]
    statistics = ColorStatistics.fromPayload(payload)
    assert statistics.pixelCount == 2
    assert statistics.channels["r"].minimum == 30.0
    assert statistics.channels["r"].maximum == 70.0
    assert statistics.channels["r"].mean == 50.0
    assert statistics.channels["g"].mean == 40.0
    assert statistics.channels["b"].mean == 30.0
    assert isinstance(statistics.region, BBox2D)


def testRgbStatisticsUsesHalfOpenBBoxAndCanEmitMask() -> None:
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    image[:, :, 2] = np.arange(16, dtype=np.uint8).reshape(4, 4)
    space = CoordinateSpace2D(imageWidth=4, imageHeight=4)
    roi = BBox2D(1, 1, 2, 2, space).toPayload()

    result = RgbStatisticsOperator().executeNode(
        {"image": image, "roi": roi},
        {"emitMask": True},
        {},
    )

    statistics = ColorStatistics.fromPayload(result["outputs"]["statistics"])
    assert statistics.pixelCount == 4
    assert statistics.channels["r"].mean == 7.5
    mask = result["outputs"]["mask"]
    assert isinstance(mask, np.ndarray)
    assert int(np.count_nonzero(mask)) == 4


def testRgbStatisticsSupportsPolygonAndPointRois() -> None:
    image = np.zeros((5, 5, 3), dtype=np.uint8)
    image[2, 2] = (10, 20, 30)
    space = CoordinateSpace2D(imageWidth=5, imageHeight=5)
    polygon = Polygon2D(
        (
            Point2D(1, 1, space),
            Point2D(3, 1, space),
            Point2D(2, 3, space),
        )
    )
    operator = RgbStatisticsOperator()

    polygonResult = operator.executeNode(
        {"image": image, "roi": polygon.toPayload()}, {}, {}
    )
    pointResult = operator.executeNode(
        {"image": image, "roi": Point2D(2.2, 2.7, space).toPayload()}, {}, {}
    )

    polygonStats = ColorStatistics.fromPayload(
        polygonResult["outputs"]["statistics"]
    )
    pointStats = ColorStatistics.fromPayload(pointResult["outputs"]["statistics"])
    assert polygonStats.pixelCount > 1
    assert pointStats.pixelCount == 1
    assert pointStats.channels["r"].mean == 30.0


def testRgbStatisticsRasterizesLineAndCircleRois() -> None:
    image = np.zeros((9, 9, 3), dtype=np.uint8)
    image[:, :, 2] = 100
    space = CoordinateSpace2D(imageWidth=9, imageHeight=9)
    line = Line2D(Point2D(1, 1, space), Point2D(5, 1, space))
    circle = Circle2D(Point2D(4, 4, space), 2)
    operator = RgbStatisticsOperator()

    lineResult = operator.executeNode(
        {"image": image, "roi": line}, {"emitMask": True}, {}
    )
    circleResult = operator.executeNode(
        {"image": image, "roi": circle.toPayload()}, {"emitMask": True}, {}
    )

    lineStats = ColorStatistics.fromPayload(lineResult["outputs"]["statistics"])
    circleStats = ColorStatistics.fromPayload(circleResult["outputs"]["statistics"])
    assert lineStats.pixelCount == 5
    assert circleStats.pixelCount == int(
        np.count_nonzero(circleResult["outputs"]["mask"])
    )
    assert circleStats.pixelCount > lineStats.pixelCount


def testRgbStatisticsSupportsSampleStandardDeviation() -> None:
    image = np.asarray([[[0, 0, 0], [0, 0, 2]]], dtype=np.uint8)
    result = RgbStatisticsOperator().executeNode(
        {"image": image}, {"ddof": 1}, {}
    )
    statistics = ColorStatistics.fromPayload(result["outputs"]["statistics"])

    assert np.isclose(statistics.channels["r"].standardDeviation, np.sqrt(2.0))


def testRgbStatisticsRejectsInvalidImagesAndRois() -> None:
    operator = RgbStatisticsOperator()
    grayscale = operator.executeNode(
        {"image": np.zeros((2, 2), dtype=np.uint8)}, {}, {}
    )
    mismatchedSpace = CoordinateSpace2D(imageWidth=3, imageHeight=3)
    badRoi = operator.executeNode(
        {
            "image": np.zeros((2, 2, 3), dtype=np.uint8),
            "roi": BBox2D(0, 0, 1, 1, mismatchedSpace).toPayload(),
        },
        {},
        {},
    )

    assert grayscale["error"]["code"] == "E_INPUT_SHAPE"
    assert badRoi["error"]["code"] == "E_INPUT_SHAPE"
