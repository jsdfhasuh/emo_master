from __future__ import annotations

import json
from pathlib import Path

import pytest

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Point2D,
    Polygon2D,
    Vector2D,
)
from emo_master.plugins.builtins._coordinate_operators import (
    CoordinateCalculatorOperator,
    CoordinateReaderOperator,
)
from emo_master.plugins.builtins._image_frame import defaultFrame


def testCoordinateReaderLoadsCsvWithFrameAndPolygon(tmp_path: Path) -> None:
    coordinateFile = tmp_path / "square.csv"
    coordinateFile.write_text("x,y\n0,0\n10,0\n10,10\n0,10\n0,0\n", encoding="utf-8")
    frame = defaultFrame(20, 20, sourceId="image-a").toPayload()

    result = CoordinateReaderOperator().executeNode(
        {"frame": frame},
        {
            "filePath": "square.csv",
            "headerMode": "auto",
            "asPolygon": True,
        },
        {"workspacePath": str(tmp_path)},
    )

    assert result["status"] == "ok"
    assert result["outputs"]["pointCount"] == 5
    points = [Point2D.fromPayload(value) for value in result["outputs"]["points"]]
    assert [(point.x, point.y) for point in points] == [
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 10.0),
        (0.0, 10.0),
        (0.0, 0.0),
    ]
    assert all(point.coordinateSpace.sourceId == "image-a" for point in points)
    polygon = Polygon2D.fromPayload(result["outputs"]["polygon"])
    assert len(polygon.points) == 4


def testCoordinateReaderLoadsWhitespaceTxtAndCanSkipInvalidRows(
    tmp_path: Path,
) -> None:
    coordinateFile = tmp_path / "points.txt"
    coordinateFile.write_text(
        "# comment\n1 2\ninvalid row\n3 4\n",
        encoding="utf-8",
    )

    result = CoordinateReaderOperator().executeNode(
        {},
        {
            "filePath": str(coordinateFile),
            "headerMode": "absent",
            "onInvalidRow": "skip",
            "sourceId": "fixture-points",
            "imageWidth": 100,
            "imageHeight": 80,
        },
        {},
    )

    assert result["status"] == "ok"
    assert result["metrics"]["skippedRows"] == 1
    points = [Point2D.fromPayload(value) for value in result["outputs"]["points"]]
    assert [(point.x, point.y) for point in points] == [(1.0, 2.0), (3.0, 4.0)]
    assert points[0].coordinateSpace.imageWidth == 100
    assert points[0].coordinateSpace.imageHeight == 80
    assert "polygon" not in result["outputs"]


def testCoordinateReaderRejectsMissingMalformedAndOversizedInput(
    tmp_path: Path,
) -> None:
    missing = CoordinateReaderOperator().executeNode(
        {}, {"filePath": str(tmp_path / "missing.csv")}, {}
    )
    assert missing["status"] == "error"
    assert missing["error"]["code"] == "E_INPUT_MISSING"

    malformedFile = tmp_path / "malformed.csv"
    malformedFile.write_text("x,y\n1,not-a-number\n", encoding="utf-8")
    malformed = CoordinateReaderOperator().executeNode(
        {},
        {"filePath": str(malformedFile), "headerMode": "present"},
        {},
    )
    assert malformed["status"] == "error"
    assert malformed["error"]["code"] == "E_INPUT_SHAPE"

    oversizedFile = tmp_path / "oversized.txt"
    oversizedFile.write_text("1 2\n", encoding="utf-8")
    oversized = CoordinateReaderOperator().executeNode(
        {},
        {"filePath": str(oversizedFile), "maxFileBytes": 1},
        {},
    )
    assert oversized["status"] == "error"
    assert oversized["error"]["code"] == "E_INPUT_SHAPE"


def testCoordinateCalculatorMeasuresClosedPathAndCanonicalizesClosure() -> None:
    space = defaultFrame(20, 20, sourceId="image-a").coordinateSpace
    points = [
        Point2D(0, 0, space).toPayload(),
        Point2D(10, 0, space).toPayload(),
        Point2D(10, 10, space).toPayload(),
        Point2D(0, 10, space).toPayload(),
        Point2D(0, 0, space).toPayload(),
    ]

    result = CoordinateCalculatorOperator().executeNode(
        {"points": points}, {"closed": True}, {}
    )

    assert result["status"] == "ok"
    outputs = result["outputs"]
    assert len(outputs["points"]) == 4
    assert outputs["segmentLengths"] == [10.0, 10.0, 10.0]
    assert outputs["pathLength"] == pytest.approx(30.0)
    assert outputs["perimeter"] == pytest.approx(40.0)
    assert outputs["signedArea"] == pytest.approx(100.0)
    assert outputs["area"] == pytest.approx(100.0)
    assert outputs["firstToLastDistance"] == pytest.approx(10.0)
    assert outputs["firstToLastAngleDegrees"] == pytest.approx(90.0)
    centroid = Point2D.fromPayload(outputs["centroid"])
    assert (centroid.x, centroid.y) == pytest.approx((5.0, 5.0))
    bounds = BBox2D.fromPayload(outputs["bounds"])
    assert (bounds.x, bounds.y, bounds.width, bounds.height) == pytest.approx(
        (0.0, 0.0, 10.0, 10.0)
    )
    assert len(Polygon2D.fromPayload(outputs["polygon"]).points) == 4


def testCoordinateCalculatorAppliesImageClockwiseRotationScaleAndOffset() -> None:
    space = defaultFrame(100, 100, sourceId="image-a").coordinateSpace
    result = CoordinateCalculatorOperator().executeNode(
        {
            "points": [
                Point2D(1, 0, space).toPayload(),
                Point2D(2, 0, space).toPayload(),
            ]
        },
        {
            "rotationDegrees": 90.0,
            "scaleX": 2.0,
            "offsetX": 10.0,
            "offsetY": 5.0,
        },
        {},
    )

    assert result["status"] == "ok"
    points = [Point2D.fromPayload(value) for value in result["outputs"]["points"]]
    assert [(point.x, point.y) for point in points] == pytest.approx(
        [(10.0, 7.0), (10.0, 9.0)]
    )
    assert result["outputs"]["pathLength"] == pytest.approx(2.0)
    assert result["outputs"]["firstToLastAngleDegrees"] == pytest.approx(90.0)
    assert "polygon" not in result["outputs"]


def testCoordinateCalculatorSubtractsPointPairsIntoVectors() -> None:
    space = defaultFrame(100, 100, sourceId="image-a").coordinateSpace
    result = CoordinateCalculatorOperator().executeNode(
        {
            "points": [
                Point2D(5, 7, space).toPayload(),
                Point2D(1, -2, space).toPayload(),
            ],
            "otherPoints": [
                Point2D(2, 3, space).toPayload(),
                Point2D(-4, 3, space).toPayload(),
            ],
        },
        {"mode": "subtract"},
        {},
    )

    assert result["status"] == "ok"
    outputs = result["outputs"]
    vectors = [Vector2D.fromPayload(value) for value in outputs["vectors"]]
    assert [(vector.dx, vector.dy) for vector in vectors] == pytest.approx(
        [(3.0, 4.0), (5.0, -5.0)]
    )
    assert outputs["magnitudes"] == pytest.approx([5.0, 50.0**0.5])
    assert outputs["resultCount"] == 2
    assert "points" not in outputs


def testCoordinateCalculatorComputesPairwiseDotProductsAndSum() -> None:
    space = defaultFrame(100, 100, sourceId="image-a").coordinateSpace
    result = CoordinateCalculatorOperator().executeNode(
        {
            "points": [
                Point2D(1, 2, space).toPayload(),
                Point2D(3, 4, space).toPayload(),
            ],
            "otherPoints": [
                Point2D(5, 6, space).toPayload(),
                Point2D(7, 8, space).toPayload(),
            ],
        },
        {"mode": "dot"},
        {},
    )

    assert result["status"] == "ok"
    assert result["outputs"] == {
        "values": [17.0, 53.0],
        "dotSum": 70.0,
        "resultCount": 2,
    }


def testCoordinateCalculatorScalarParameterAndInputOverride() -> None:
    space = defaultFrame(100, 100, sourceId="image-a").coordinateSpace
    parameterResult = CoordinateCalculatorOperator().executeNode(
        {"points": [Point2D(2, -3, space).toPayload()]},
        {"mode": "scalarMultiply", "scalarValue": -2.0},
        {},
    )
    result = CoordinateCalculatorOperator().executeNode(
        {
            "points": [
                Point2D(2, -3, space).toPayload(),
                Point2D(-4, 5, space).toPayload(),
            ],
            "scalar": 2.5,
        },
        {"mode": "scalarMultiply", "scalarValue": 99.0},
        {},
    )

    assert parameterResult["status"] == "ok"
    parameterPoint = Point2D.fromPayload(parameterResult["outputs"]["points"][0])
    assert (parameterPoint.x, parameterPoint.y) == pytest.approx((-4.0, 6.0))
    assert result["status"] == "ok"
    points = [Point2D.fromPayload(value) for value in result["outputs"]["points"]]
    assert [(point.x, point.y) for point in points] == pytest.approx(
        [(5.0, -7.5), (-10.0, 12.5)]
    )
    assert result["outputs"]["resultCount"] == 2


def testCoordinateCalculatorSupportsBroadcastPairing() -> None:
    space = defaultFrame(100, 100, sourceId="image-a").coordinateSpace
    operator = CoordinateCalculatorOperator()
    left = [Point2D(10, 10, space).toPayload()]
    right = [
        Point2D(1, 2, space).toPayload(),
        Point2D(3, 4, space).toPayload(),
    ]

    strict = operator.executeNode(
        {"points": left, "otherPoints": right},
        {"mode": "subtract"},
        {},
    )
    broadcast = operator.executeNode(
        {"points": left, "otherPoints": right},
        {"mode": "subtract", "pairing": "broadcast"},
        {},
    )

    assert strict["status"] == "error"
    assert strict["error"]["code"] == "E_INPUT_SHAPE"
    assert broadcast["status"] == "ok"
    vectors = [
        Vector2D.fromPayload(value) for value in broadcast["outputs"]["vectors"]
    ]
    assert [(vector.dx, vector.dy) for vector in vectors] == pytest.approx(
        [(9.0, 8.0), (7.0, 6.0)]
    )


def testCoordinateCalculatorRejectsMismatchedPairSpacesAndUnusedInputs() -> None:
    operator = CoordinateCalculatorOperator()
    firstSpace = defaultFrame(10, 10, sourceId="first").coordinateSpace
    secondSpace = defaultFrame(10, 10, sourceId="second").coordinateSpace
    first = [Point2D(1, 2, firstSpace).toPayload()]
    second = [Point2D(3, 4, secondSpace).toPayload()]

    mismatched = operator.executeNode(
        {"points": first, "otherPoints": second},
        {"mode": "dot"},
        {},
    )
    unusedOther = operator.executeNode(
        {"points": first, "otherPoints": first},
        {"mode": "transform"},
        {},
    )
    unusedScalar = operator.executeNode(
        {"points": first, "scalar": 2.0},
        {"mode": "measure"},
        {},
    )

    assert mismatched["status"] == "error"
    assert mismatched["error"]["code"] == "E_INPUT_SHAPE"
    assert unusedOther["status"] == "error"
    assert unusedOther["error"]["code"] == "E_INPUT_SHAPE"
    assert unusedScalar["status"] == "error"
    assert unusedScalar["error"]["code"] == "E_INPUT_SHAPE"


def testCoordinateCalculatorRejectsEmptyMixedSpaceAndDegenerateClosedInput() -> None:
    operator = CoordinateCalculatorOperator()
    empty = operator.executeNode({"points": []}, {}, {})
    assert empty["status"] == "error"
    assert empty["error"]["code"] == "E_INPUT_SHAPE"

    firstSpace = defaultFrame(10, 10, sourceId="first").coordinateSpace
    secondSpace = defaultFrame(10, 10, sourceId="second").coordinateSpace
    mixed = operator.executeNode(
        {
            "points": [
                Point2D(0, 0, firstSpace).toPayload(),
                Point2D(1, 1, secondSpace).toPayload(),
            ]
        },
        {},
        {},
    )
    assert mixed["status"] == "error"
    assert mixed["error"]["code"] == "E_INPUT_SHAPE"

    degenerate = operator.executeNode(
        {
            "points": [
                Point2D(0, 0, firstSpace).toPayload(),
                Point2D(1, 0, firstSpace).toPayload(),
                Point2D(2, 0, firstSpace).toPayload(),
            ]
        },
        {"closed": True},
        {},
    )
    assert degenerate["status"] == "error"
    assert degenerate["error"]["code"] == "E_INPUT_SHAPE"


def testCoordinateManifestsMatchOperatorMetadata() -> None:
    pluginRoot = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "emo_master"
        / "plugins"
        / "builtins"
    )
    for directory, operator in (
        ("coordinate_reader", CoordinateReaderOperator),
        ("coordinate_calculator", CoordinateCalculatorOperator),
    ):
        manifest = json.loads(
            (pluginRoot / directory / "manifest.json").read_text(encoding="utf-8")
        )
        meta = operator.meta
        assert manifest["operatorId"] == meta.operatorId
        assert manifest["displayName"] == meta.displayName
        assert manifest["version"] == meta.version
        assert manifest["inputPorts"] == meta.inputPorts
        assert manifest["outputPorts"] == meta.outputPorts
        assert manifest["paramSchema"] == meta.paramSchema
