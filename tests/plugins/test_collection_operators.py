import math

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Blob2D,
    BlobCollection,
    Circle2D,
    CircleCollection,
    CircleItem,
    ContourCollection,
    ContourItem,
    CoordinateSpace2D,
    Detection2D,
    DetectionCollection,
    Line2D,
    LineCollection,
    LineItem,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    ShapeMeasurement,
    ShapeMeasurementCollection,
    TemplateMatch,
    TemplateMatchCollection,
)
from emo_master.plugins.builtins.collection_count.operator import CollectionCountOperator
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins.collection_sort.operator import CollectionSortOperator


def _space(sourceId: str = "source") -> CoordinateSpace2D:
    return CoordinateSpace2D(sourceId=sourceId, imageWidth=100, imageHeight=80)


def _blobs() -> BlobCollection:
    space = _space()
    contour = Polygon2D(
        (
            Point2D(45, 45, space),
            Point2D(65, 45, space),
            Point2D(65, 65, space),
            Point2D(45, 65, space),
        )
    )
    return BlobCollection(
        (
            Blob2D(
                "small",
                10.0,
                Point2D(10, 10, space),
                BBox2D(5, 5, 10, 10, space),
                label=1,
                attributes={"circularity": 0.5},
            ),
            Blob2D(
                "large",
                30.0,
                Point2D(55, 55, space),
                BBox2D(45, 45, 20, 20, space),
                label=2,
                contour=contour,
                attributes={"circularity": 0.8},
            ),
            Blob2D(
                "missing-circularity",
                20.0,
                Point2D(30, 30, space),
                BBox2D(25, 25, 10, 10, space),
                label=None,
            ),
        ),
        space,
    )


def _detections() -> DetectionCollection:
    space = _space()
    return DetectionCollection(
        (
            Detection2D.fromGeometry("d1", 0, "ok", 0.4, BBox2D(5, 5, 10, 10, space)),
            Detection2D.fromGeometry("d2", 1, "bad", 0.9, BBox2D(50, 50, 10, 10, space)),
        ),
        space,
    )


def _newCollections() -> dict[str, object]:
    space = _space()
    largePolygon = Polygon2D(
        (
            Point2D(10, 10, space),
            Point2D(20, 10, space),
            Point2D(20, 20, space),
            Point2D(10, 20, space),
        )
    )
    smallPolygon = Polygon2D(
        (
            Point2D(12, 12, space),
            Point2D(14, 12, space),
            Point2D(14, 14, space),
            Point2D(12, 14, space),
        )
    )
    contours = ContourCollection(
        (
            ContourItem(
                "outer",
                largePolygon,
                firstChildId="hole",
                depth=0,
                isHole=False,
            ),
            ContourItem(
                "hole",
                smallPolygon,
                parentId="outer",
                depth=1,
                isHole=True,
            ),
        ),
        space,
    )
    measurements = ShapeMeasurementCollection(
        (
            ShapeMeasurement(
                "m-low",
                "outer",
                "contour",
                25,
                20,
                Point2D(15, 15, space),
                BBox2D(10, 10, 10, 10, space),
                RotatedBox2D(15, 15, 10, 10, 0, space),
                0.4,
            ),
            ShapeMeasurement(
                "m-high",
                "blob-1",
                "blob",
                50,
                30,
                Point2D(35, 25, space),
                BBox2D(30, 20, 10, 10, space),
                RotatedBox2D(35, 25, 10, 10, 20, space),
                0.9,
            ),
        ),
        space,
    )
    lines = LineCollection(
        (
            LineItem("short", Line2D(Point2D(1, 1, space), Point2D(4, 5, space))),
            LineItem("long", Line2D(Point2D(5, 5, space), Point2D(15, 5, space))),
        ),
        space,
    )
    circles = CircleCollection(
        (
            CircleItem("small-circle", Circle2D(Point2D(20, 20, space), 2)),
            CircleItem("large-circle", Circle2D(Point2D(50, 30, space), 5)),
        ),
        space,
    )
    matches = TemplateMatchCollection(
        "ccoeffNormed",
        "part",
        5,
        4,
        (
            TemplateMatch("weak", BBox2D(2, 2, 5, 4, space), 0.2, 0.4),
            TemplateMatch("strong", BBox2D(30, 30, 5, 4, space), 0.9, 0.95),
        ),
        space,
    )
    return {
        "contours": contours,
        "measurements": measurements,
        "lines": lines,
        "circles": circles,
        "matches": matches,
    }


def testFilterBlobAttributesAndReturnsRejectedCollection() -> None:
    result = CollectionFilterOperator().executeNode(
        {"blobs": _blobs().toPayload()},
        {
            "minArea": 15,
            "circularityEnabled": True,
            "minCircularity": 0.7,
            "contourMode": "present",
        },
        {},
    )

    assert result["status"] == "ok"
    kept = BlobCollection.fromPayload(result["outputs"]["keptBlobs"])
    rejected = BlobCollection.fromPayload(result["outputs"]["rejectedBlobs"])
    assert [item.blobId for item in kept.items] == ["large"]
    assert len(rejected.items) == 2
    assert result["outputs"]["keptCount"] == 1
    assert kept.coordinateSpace == _blobs().coordinateSpace


def testFilterDetectionByConfidenceClassAndRoi() -> None:
    roi = BBox2D(0, 0, 20, 20, _space())
    result = CollectionFilterOperator().executeNode(
        {"detections": _detections().toPayload(), "roi": roi.toPayload()},
        {
            "minConfidence": 0.4,
            "classIds": [0, 1],
            "spatialMode": "centerInside",
        },
        {},
    )

    kept = DetectionCollection.fromPayload(result["outputs"]["keptDetections"])
    assert [item.detectionId for item in kept.items] == ["d1"]
    assert result["outputs"]["rejectedCount"] == 1


def testFilterRejectsMismatchedRoiSpaceAndCollectionInputConflict() -> None:
    mismatched = BBox2D(0, 0, 20, 20, _space("other"))
    operator = CollectionFilterOperator()
    spaceError = operator.executeNode(
        {"blobs": _blobs().toPayload(), "roi": mismatched.toPayload()},
        {"spatialMode": "bboxIntersects"},
        {},
    )
    conflict = operator.executeNode(
        {"blobs": _blobs().toPayload(), "detections": _detections().toPayload()},
        {},
        {},
    )

    assert spaceError["error"]["code"] == "E_INPUT_SHAPE"
    assert conflict["error"]["code"] == "E_INPUT_SHAPE"


def testCountHandlesBothKindsAndEmptyCollection() -> None:
    operator = CollectionCountOperator()
    blobs = operator.executeNode({"blobs": _blobs().toPayload()}, {}, {})
    empty = DetectionCollection((), _space())
    detections = operator.executeNode({"detections": empty.toPayload()}, {}, {})

    assert blobs["outputs"]["count"] == 3
    assert detections["outputs"]["count"] == 0


def testSortIsStableAndAlwaysPlacesMissingValuesLast() -> None:
    operator = CollectionSortOperator()
    descending = operator.executeNode(
        {"blobs": _blobs().toPayload()},
        {"key": "area", "direction": "descending"},
        {},
    )
    circularity = operator.executeNode(
        {"blobs": _blobs().toPayload()},
        {"key": "circularity", "direction": "ascending"},
        {},
    )

    sortedArea = BlobCollection.fromPayload(descending["outputs"]["sortedBlobs"])
    sortedCircularity = BlobCollection.fromPayload(
        circularity["outputs"]["sortedBlobs"]
    )
    assert [item.blobId for item in sortedArea.items] == [
        "large",
        "missing-circularity",
        "small",
    ]
    assert [item.blobId for item in sortedCircularity.items] == [
        "small",
        "large",
        "missing-circularity",
    ]


def testSelectSupportsIndexTopKAndOutOfRangePolicies() -> None:
    operator = CollectionSelectOperator()
    indexed = operator.executeNode(
        {"detections": _detections().toPayload()}, {"mode": "index", "index": 1}, {}
    )
    top = operator.executeNode(
        {"blobs": _blobs().toPayload()}, {"mode": "topK", "count": 2}, {}
    )
    empty = operator.executeNode(
        {"blobs": _blobs().toPayload()}, {"mode": "index", "index": 20}, {}
    )
    error = operator.executeNode(
        {"blobs": _blobs().toPayload()},
        {"mode": "index", "index": 20, "onOutOfRange": "error"},
        {},
    )

    selected = DetectionCollection.fromPayload(indexed["outputs"]["selectedDetections"])
    selectedTop = BlobCollection.fromPayload(top["outputs"]["selectedBlobs"])
    assert [item.detectionId for item in selected.items] == ["d2"]
    assert len(selectedTop.items) == 2
    assert empty["outputs"]["selectedCount"] == 0
    assert error["error"]["code"] == "E_PARAM_INVALID"


def testCountSupportsAllFiveNewStrongCollectionKinds() -> None:
    operator = CollectionCountOperator()
    for portName, collection in _newCollections().items():
        result = operator.executeNode(
            {portName: collection.toPayload()},  # type: ignore[attr-defined]
            {},
            {},
        )
        assert result["status"] == "ok"
        assert result["outputs"]["count"] == 2


def testFilterSupportsNewTypeSpecificRangesAndMetadata() -> None:
    collections = _newCollections()
    cases = (
        ("contours", {"holeMode": "hole"}, "keptContours", "hole"),
        (
            "measurements",
            {
                "circularityEnabled": True,
                "minCircularity": 0.8,
                "sourceTypes": ["blob"],
            },
            "keptMeasurements",
            "m-high",
        ),
        ("lines", {"minLength": 8}, "keptLines", "long"),
        ("circles", {"maxRadius": 3}, "keptCircles", "small-circle"),
        (
            "matches",
            {"minQuality": 0.8, "templateMethods": ["ccoeffNormed"]},
            "keptMatches",
            "strong",
        ),
    )
    idFields = {
        "contours": "contourId",
        "measurements": "measurementId",
        "lines": "lineId",
        "circles": "circleId",
        "matches": "matchId",
    }
    parsers = {
        "contours": ContourCollection,
        "measurements": ShapeMeasurementCollection,
        "lines": LineCollection,
        "circles": CircleCollection,
        "matches": TemplateMatchCollection,
    }

    for portName, params, outputName, expectedId in cases:
        source = collections[portName]
        result = CollectionFilterOperator().executeNode(
            {portName: source.toPayload()},  # type: ignore[attr-defined]
            params,
            {},
        )
        parsed = parsers[portName].fromPayload(result["outputs"][outputName])
        assert len(parsed.items) == 1
        assert getattr(parsed.items[0], idFields[portName]) == expectedId
        assert parsed.coordinateSpace == source.coordinateSpace  # type: ignore[attr-defined]
        if isinstance(parsed, TemplateMatchCollection):
            assert (parsed.method, parsed.label) == ("ccoeffNormed", "part")


def testSortAndSelectNewCollectionsPreserveIdsRelationsAndStableMetadata() -> None:
    collections = _newCollections()
    sortCases = (
        ("contours", "area", "sortedContours", ContourCollection, ["outer", "hole"], "contourId"),
        (
            "measurements",
            "circularity",
            "sortedMeasurements",
            ShapeMeasurementCollection,
            ["m-high", "m-low"],
            "measurementId",
        ),
        ("lines", "length", "sortedLines", LineCollection, ["long", "short"], "lineId"),
        (
            "circles",
            "radius",
            "sortedCircles",
            CircleCollection,
            ["large-circle", "small-circle"],
            "circleId",
        ),
        (
            "matches",
            "quality",
            "sortedMatches",
            TemplateMatchCollection,
            ["strong", "weak"],
            "matchId",
        ),
    )
    for portName, key, outputName, parser, expectedIds, idField in sortCases:
        result = CollectionSortOperator().executeNode(
            {portName: collections[portName].toPayload()},  # type: ignore[attr-defined]
            {"key": key, "direction": "descending"},
            {},
        )
        parsed = parser.fromPayload(result["outputs"][outputName])
        assert [getattr(item, idField) for item in parsed.items] == expectedIds

    selectedResult = CollectionSelectOperator().executeNode(
        {"contours": collections["contours"].toPayload()},  # type: ignore[attr-defined]
        {"mode": "last"},
        {},
    )
    selected = ContourCollection.fromPayload(
        selectedResult["outputs"]["selectedContours"]
    )
    assert [item.contourId for item in selected.items] == ["hole"]
    assert selected.items[0].parentId == "outer"

    matchesResult = CollectionSelectOperator().executeNode(
        {"matches": collections["matches"].toPayload()},  # type: ignore[attr-defined]
        {"mode": "topK", "count": 1},
        {},
    )
    selectedMatches = TemplateMatchCollection.fromPayload(
        matchesResult["outputs"]["selectedMatches"]
    )
    assert selectedMatches.method == "ccoeffNormed"
    assert selectedMatches.label == "part"
    assert len(selectedMatches.items) == 1


def testLineAndCircleSpatialFilteringUseMidpointCenterAndAxisAlignedBounds() -> None:
    collections = _newCollections()
    roi = BBox2D(0, 0, 25, 25, _space())
    lineResult = CollectionFilterOperator().executeNode(
        {"lines": collections["lines"].toPayload(), "roi": roi.toPayload()},  # type: ignore[attr-defined]
        {"spatialMode": "centerInside"},
        {},
    )
    circleResult = CollectionFilterOperator().executeNode(
        {"circles": collections["circles"].toPayload(), "roi": roi.toPayload()},  # type: ignore[attr-defined]
        {"spatialMode": "bboxContained"},
        {},
    )
    lines = LineCollection.fromPayload(lineResult["outputs"]["keptLines"])
    circles = CircleCollection.fromPayload(circleResult["outputs"]["keptCircles"])
    assert {item.lineId for item in lines.items} == {"short", "long"}
    assert [item.circleId for item in circles.items] == ["small-circle"]
    assert math.isclose(circles.items[0].circle.radius, 2)
    Circle2D,
    CircleCollection,
    CircleItem,
    ContourCollection,
    ContourItem,
    Line2D,
    LineCollection,
    LineItem,
    RotatedBox2D,
    ShapeMeasurement,
    ShapeMeasurementCollection,
    TemplateMatch,
    TemplateMatchCollection,
