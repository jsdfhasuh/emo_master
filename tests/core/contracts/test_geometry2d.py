from dataclasses import FrozenInstanceError
import json

import pytest

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    Blob2D,
    BlobCollection,
    Circle2D,
    CircleCollection,
    CircleItem,
    ChannelStatistics,
    ColorStatistics,
    ContourCollection,
    ContourItem,
    CoordinateSpace2D,
    Detection2D,
    DetectionCollection,
    Histogram,
    HistogramChannel,
    Line2D,
    LineCollection,
    LineItem,
    PayloadValidationError,
    Point2D,
    Polygon2D,
    RotatedBox2D,
    ShapeMeasurement,
    ShapeMeasurementCollection,
    TemplateMatch,
    TemplateMatchCollection,
    Vector2D,
    parseGeometry2D,
    parseVisionPayload,
)
from emo_master.plugins.builtins._image_frame import (
    composeTransform,
    coordinateSpacesEquivalent,
)
from emo_master.core.contracts.port_compatibility import (
    arePortTypesCompatible,
    isPortTypeAssignable,
)
from emo_master.core.contracts.port_types import matchesPortType, normalizePortType
from emo_master.core.plugin.validator import validateManifestFields


def _space(reference: str = "sourceImage") -> CoordinateSpace2D:
    return CoordinateSpace2D(
        reference=reference,
        imageWidth=640,
        imageHeight=480,
    )


def _polygon(space: CoordinateSpace2D) -> Polygon2D:
    return Polygon2D(
        (
            Point2D(10, 20, space),
            Point2D(40, 20, space),
            Point2D(40, 60, space),
        )
    )


def testConcreteGeometryPayloadsRoundTripWithExplicitTypeTags() -> None:
    space = _space()
    values = (
        Point2D(12.5, 24, space),
        BBox2D(10, 20, 30, 40, space),
        RotatedBox2D(25, 40, 30, 40, -12.5, space),
        _polygon(space),
    )

    for value in values:
        payload = value.toPayload()
        assert payload["schemaVersion"] == "1.1"
        assert parseGeometry2D(payload) == value
        json.dumps(payload, allow_nan=False)

    assert values[0].toPayload()["type"] == "point2d"
    assert values[1].toPayload()["type"] == "bbox2d"
    assert values[2].toPayload()["type"] == "rotatedBox2d"
    assert values[3].toPayload()["type"] == "polygon2d"
    with pytest.raises(FrozenInstanceError):
        values[0].x = 99  # type: ignore[misc]


def testVectorPayloadRoundTripsStrictlyWithoutBecomingGeometry() -> None:
    vector = Vector2D(3.5, -4.25, _space())
    payload = vector.toPayload()

    assert payload["type"] == "vector2d"
    assert payload["schemaVersion"] == "1.1"
    assert Vector2D.fromPayload(payload) == vector
    assert parseVisionPayload(payload) == vector
    assert matchesPortType(payload, "vector2d") is True
    assert matchesPortType(payload, "geometry2d") is False
    assert matchesPortType([payload], "list<vector2d>") is True
    assert normalizePortType("Vector2D") == "vector2d"
    assert isPortTypeAssignable("vector2d", "json") is True
    assert isPortTypeAssignable("vector2d", "geometry2d") is False
    json.dumps(payload, allow_nan=False)

    with pytest.raises(PayloadValidationError, match="unknown fields"):
        Vector2D.fromPayload({**payload, "length": 5.5})
    with pytest.raises(PayloadValidationError, match="finite"):
        Vector2D(float("inf"), 0, _space())


def testBlobDetectionAndColorPayloadsRoundTrip() -> None:
    space = _space()
    bbox = BBox2D(10, 20, 30, 40, space)
    polygon = _polygon(space)
    blobCollection = BlobCollection(
        (
            Blob2D(
                "blob-1",
                900,
                Point2D(25, 40, space),
                bbox,
                label=1,
                contour=polygon,
                attributes={"circularity": 0.8},
            ),
        ),
        space,
    )
    detectionCollection = DetectionCollection(
        (
            Detection2D(
                "det-1",
                0,
                "part",
                0.96,
                bbox,
                polygon=polygon,
                attributes={"model": "yolo"},
            ),
        ),
        space,
    )
    colorStatistics = ColorStatistics(
        channels={
            "r": ChannelStatistics(0, 255, 120.5, 10.2),
            "g": ChannelStatistics(0, 255, 110.5, 9.2),
            "b": ChannelStatistics(0, 255, 100.5, 8.2),
        },
        pixelCount=900,
        region=bbox,
    )

    for value in (blobCollection, detectionCollection, colorStatistics):
        payload = value.toPayload()
        assert parseVisionPayload(payload) == value
        json.dumps(payload, allow_nan=False)


def testGeometryContractsRejectAmbiguousOrInvalidPayloads() -> None:
    pointPayload = Point2D(1, 2, _space()).toPayload()

    with pytest.raises(PayloadValidationError, match="schemaVersion"):
        Point2D.fromPayload({**pointPayload, "schemaVersion": "2.0"})
    v12WithoutTransform = {**pointPayload, "schemaVersion": "1.2"}
    v12Space = dict(v12WithoutTransform["coordinateSpace"])
    v12Space.pop("transformToSource")
    v12WithoutTransform["coordinateSpace"] = v12Space
    with pytest.raises(PayloadValidationError, match="explicit coordinate transform"):
        Point2D.fromPayload(v12WithoutTransform)
    with pytest.raises(PayloadValidationError, match="unknown fields"):
        Point2D.fromPayload({**pointPayload, "z": 3})
    with pytest.raises(PayloadValidationError, match="finite"):
        Point2D(float("nan"), 2)
    with pytest.raises(PayloadValidationError, match="width"):
        BBox2D(0, 0, -1, 10)
    with pytest.raises(PayloadValidationError, match="confidence"):
        Detection2D("det", 0, "part", 1.1, BBox2D(0, 0, 1, 1))
    with pytest.raises(PayloadValidationError, match="collection coordinateSpace"):
        BlobCollection(
            (
                Blob2D(
                    "blob",
                    1,
                    Point2D(0, 0, _space("input:image")),
                    BBox2D(0, 0, 1, 1, _space("input:image")),
                ),
            ),
            _space(),
        )


def testSemanticPortTypesValidatePayloadsAndPreserveLegacyUnknownTypes() -> None:
    bbox = BBox2D(10, 20, 30, 40, _space())
    payload = bbox.toPayload()

    assert normalizePortType("DetectionCollection") == "detectionCollection"
    assert normalizePortType("list<RotatedBox2D>") == "list<rotatedBox2d>"
    assert matchesPortType(payload, "bbox2d") is True
    assert matchesPortType(payload, "geometry2d") is True
    assert matchesPortType(payload, "point2d") is False
    assert matchesPortType(bbox, "bbox2d") is False
    assert matchesPortType({"type": "bbox2d"}, "bbox2d") is False
    assert matchesPortType([payload], "list<geometry2d>") is True
    assert matchesPortType("legacy", "vendor.custom") is True


def testGeometryAndJsonPortAssignabilityIsDirectional() -> None:
    assert arePortTypesCompatible("bbox2d", "geometry2d") is True
    assert arePortTypesCompatible("geometry2d", "bbox2d") is False
    assert isPortTypeAssignable("bbox2d", "geometry2d") is True
    assert isPortTypeAssignable("geometry2d", "bbox2d") is False
    assert isPortTypeAssignable("detectionCollection", "json") is True
    assert isPortTypeAssignable("json", "detectionCollection") is False
    assert arePortTypesCompatible("list<point2d>", "list<geometry2d>") is True


def testOperatorManifestAcceptsSemanticVisionPorts() -> None:
    manifest, issues = validateManifestFields(
        {
            "operatorId": "vision.demo.contract",
            "displayName": "Contract Demo",
            "version": "0.1.0",
            "entry": "demo.operator:DemoOperator",
            "inputPorts": {"image": "image", "roi": "geometry2d"},
            "outputPorts": {
                "centroid": "point2d",
                "detections": "detectionCollection",
                "statistics": "colorStatistics",
            },
            "paramSchema": {"type": "object"},
            "minCoreVersion": "0.2.0",
            "maxCoreVersion": "1.x",
        }
    )

    assert issues == []
    assert manifest is not None
    assert manifest.outputPorts["detections"] == "detectionCollection"


def testCoordinateSpaceTracksAffineTransformAndReadsV10Payloads() -> None:
    space = CoordinateSpace2D(
        reference="modelInput",
        sourceId="camera:0",
        imageWidth=320,
        imageHeight=320,
        transformToSource=(2, 0, 0, 2, 10, 20),
    )
    point = Point2D(3, 4, space)

    assert space.mapPointToSource(point.x, point.y) == (16.0, 28.0)
    assert Point2D.fromPayload(point.toPayload()) == point

    legacyPayload = point.toPayload()
    legacyPayload["schemaVersion"] = "1.0"
    coordinateSpace = dict(legacyPayload["coordinateSpace"])
    coordinateSpace.pop("sourceId")
    coordinateSpace.pop("transformToSource")
    legacyPayload["coordinateSpace"] = coordinateSpace
    parsed = Point2D.fromPayload(legacyPayload)

    assert parsed.coordinateSpace.sourceId == "modelInput"
    assert parsed.coordinateSpace.transformToSource == (1, 0, 0, 1, 0, 0)

    with pytest.raises(PayloadValidationError, match="6 numbers"):
        CoordinateSpace2D(transformToSource=(1, 0, 0, 1))
    with pytest.raises(PayloadValidationError, match="invertible"):
        CoordinateSpace2D(transformToSource=(1, 0, 2, 0, 0, 0))


def testHomographyRoundTripCompositionAndAffineEquivalence() -> None:
    projective = CoordinateSpace2D(
        reference="perspective",
        sourceId="camera:0",
        imageWidth=100,
        imageHeight=80,
        transformToSource=None,
        homographyToSource=(2, 0, 10, 0, 3, 20, 0.01, 0.02, 1),
    )
    point = Point2D(4, 5, projective)
    parsed = Point2D.fromPayload(point.toPayload())

    assert point.toPayload()["schemaVersion"] == "1.2"
    assert parsed == point
    assert projective.mapPointToSource(4, 5) == pytest.approx(
        ((2 * 4 + 10) / 1.14, (3 * 5 + 20) / 1.14)
    )

    affine = CoordinateSpace2D(
        sourceId="source",
        imageWidth=10,
        imageHeight=10,
        transformToSource=(1, 0, 0, 1, 0, 0),
    )
    homogeneousAffine = CoordinateSpace2D(
        sourceId="source",
        imageWidth=10,
        imageHeight=10,
        transformToSource=None,
        homographyToSource=(7, 0, 0, 0, 7, 0, 0, 0, 7),
    )
    assert coordinateSpacesEquivalent(affine, homogeneousAffine)

    composed = composeTransform(
        projective.homographyToSource,
        (2.0, 0.0, 0.0, 2.0, 3.0, 4.0),
    )
    composedSpace = CoordinateSpace2D(
        sourceId="camera:0",
        transformToSource=None,
        homographyToSource=composed,
    )
    innerPoint = (6.0, 8.0)
    viaInput = projective.mapPointToSource(
        2.0 * innerPoint[0] + 3.0,
        2.0 * innerPoint[1] + 4.0,
    )
    assert composedSpace.mapPointToSource(*innerPoint) == pytest.approx(viaInput)


def testHomographyRejectsAmbiguousSingularAndInfiniteMappings() -> None:
    with pytest.raises(PayloadValidationError, match="mutually exclusive"):
        CoordinateSpace2D(
            transformToSource=(1, 0, 0, 1, 0, 0),
            homographyToSource=(1, 0, 0, 0, 1, 0, 0, 0, 1),
        )
    with pytest.raises(PayloadValidationError, match="invertible"):
        CoordinateSpace2D(
            transformToSource=None,
            homographyToSource=(1, 0, 0, 0, 1, 0, 0, 0, 0),
        )

    space = CoordinateSpace2D(
        transformToSource=None,
        homographyToSource=(1, 0, 0, 0, 1, 0, 1, 0, -2),
    )
    with pytest.raises(PayloadValidationError, match="infinity"):
        space.mapPointToSource(2, 0)

    legacy = Point2D(1, 2, _space()).toPayload()
    legacySpace = dict(legacy["coordinateSpace"])
    legacySpace.pop("transformToSource")
    legacySpace["homographyToSource"] = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    legacy["coordinateSpace"] = legacySpace
    with pytest.raises(PayloadValidationError, match="requires schemaVersion '1.2'"):
        Point2D.fromPayload(legacy)


def testNewClassicVisionPayloadsRoundTripAndStayStrict() -> None:
    space = CoordinateSpace2D(sourceId="source", imageWidth=64, imageHeight=48)
    polygon = Polygon2D(
        (
            Point2D(1, 1, space),
            Point2D(6, 1, space),
            Point2D(6, 5, space),
            Point2D(1, 5, space),
        )
    )
    line = Line2D(Point2D(2, 3, space), Point2D(8, 9, space))
    circle = Circle2D(Point2D(12, 13, space), 4)
    contourCollection = ContourCollection(
        (ContourItem("contour-1", polygon, depth=0, isHole=False),), space
    )
    measurementCollection = ShapeMeasurementCollection(
        (
            ShapeMeasurement(
                "measurement-1",
                "contour-1",
                "contour",
                20,
                18,
                Point2D(3.5, 3, space),
                BBox2D(1, 1, 5, 4, space),
                RotatedBox2D(3.5, 3, 5, 4, 0, space),
                0.77,
            ),
        ),
        space,
    )
    histogram = Histogram(
        space,
        "GRAY",
        "counts",
        4,
        (0, 128, 256),
        (HistogramChannel("GRAY", (1, 3)),),
    )
    lineCollection = LineCollection((LineItem("line-1", line),), space)
    circleCollection = CircleCollection((CircleItem("circle-1", circle),), space)
    matches = TemplateMatchCollection(
        "ccoeffNormed",
        "template",
        5,
        4,
        (TemplateMatch("match-1", BBox2D(2, 3, 5, 4, space), 0.9, 0.95),),
        space,
    )

    for value in (
        line,
        circle,
        contourCollection,
        measurementCollection,
        histogram,
        lineCollection,
        circleCollection,
        matches,
    ):
        payload = value.toPayload()
        assert payload["schemaVersion"] == "1.2"
        assert parseVisionPayload(payload) == value
        json.dumps(payload, allow_nan=False)

    invalid = contourCollection.toPayload()
    invalid["unexpected"] = True
    with pytest.raises(PayloadValidationError, match="unknown fields"):
        ContourCollection.fromPayload(invalid)
    with pytest.raises(PayloadValidationError, match="endpoints"):
        Line2D(Point2D(1, 1, space), Point2D(1, 1, space))
    with pytest.raises(PayloadValidationError, match="radius"):
        Circle2D(Point2D(1, 1, space), 0)


def testDetectionGeometrySupportsBoxRotatedBoxPolygonAndLegacyPayload() -> None:
    space = _space()
    box = BBox2D(10, 20, 30, 40, space)
    rotated = RotatedBox2D(50, 60, 30, 20, 25, space)
    polygon = _polygon(space)
    detections = DetectionCollection(
        (
            Detection2D.fromGeometry("box", 0, "box", 0.9, box),
            Detection2D.fromGeometry("obb", 1, "obb", 0.8, rotated),
            Detection2D.fromGeometry("seg", 2, "seg", 0.7, polygon),
        ),
        space,
    )

    payload = detections.toPayload()
    parsed = DetectionCollection.fromPayload(payload)

    assert [item.geometry.toPayload()["type"] for item in parsed.items] == [
        "bbox2d",
        "rotatedBox2d",
        "polygon2d",
    ]
    assert all(item.bbox.width >= 0 for item in parsed.items)
    assert parsed == detections

    legacy = {
        "id": "legacy",
        "classId": 0,
        "label": "part",
        "confidence": 0.5,
        "bbox": box.toPayload(),
    }
    legacyDetection = Detection2D.fromPayload(legacy)
    assert legacyDetection.geometry == box


def testDetectionRejectsBBoxThatDoesNotBoundPrimaryGeometry() -> None:
    space = _space()
    rotated = RotatedBox2D(50, 60, 30, 20, 25, space)
    payload = Detection2D.fromGeometry(
        "obb", 0, "part", 0.9, rotated
    ).toPayload()
    payload["bbox"] = BBox2D(0, 0, 1, 1, space).toPayload()

    with pytest.raises(PayloadValidationError, match="axis-aligned bounds"):
        Detection2D.fromPayload(payload)
