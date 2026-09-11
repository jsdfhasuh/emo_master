import numpy as np

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
from emo_master.plugins.builtins.annotate.operator import AnnotateOperator
from emo_master.plugins.builtins._image_frame import defaultFrame


def _inputs(sourceId: str = "source") -> dict[str, object]:
    space = CoordinateSpace2D(sourceId=sourceId, imageWidth=30, imageHeight=20)
    blob = Blob2D(
        "blob",
        25,
        Point2D(7, 7, space),
        BBox2D(5, 5, 5, 5, space),
    )
    detection = Detection2D.fromGeometry(
        "det",
        1,
        "part",
        0.9,
        BBox2D(15, 5, 8, 8, space),
    )
    return {
        "blobs": BlobCollection((blob,), space).toPayload(),
        "detections": DetectionCollection((detection,), space).toPayload(),
        "roi": BBox2D(2, 2, 25, 15, space).toPayload(),
        "frame": BBox2D(0, 0, 30, 20, space).toPayload(),
    }


def testAnnotateDrawsMultipleTypedInputsWithoutMutatingImage() -> None:
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    original = image.copy()
    inputs = {"image": image, **_inputs()}

    result = AnnotateOperator().executeNode(
        inputs,
        {"drawLabels": False, "thickness": 1},
        {},
    )

    assert result["status"] == "ok"
    assert result["outputs"]["drawnCount"] == 3
    assert np.any(result["outputs"]["overlay"] != 0)
    np.testing.assert_array_equal(image, original)


def testAnnotateRejectsMismatchedCoordinateSpace() -> None:
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    inputs = _inputs("other")
    inputs["image"] = image
    inputs["frame"] = defaultFrame(30, 20, sourceId="source").toPayload()

    result = AnnotateOperator().executeNode(inputs, {}, {})

    assert result["error"]["code"] == "E_INPUT_SHAPE"


def testAnnotateReportsInvalidFrameBoundsAsInputShape() -> None:
    image = np.zeros((20, 30, 3), dtype=np.uint8)
    frame = defaultFrame(30, 20, sourceId="source")
    invalidFrame = BBox2D(
        0,
        0,
        31,
        20,
        frame.coordinateSpace,
    ).toPayload()

    result = AnnotateOperator().executeNode(
        {"image": image, "frame": invalidFrame}, {}, {}
    )

    assert result["error"]["code"] == "E_INPUT_SHAPE"


def testAnnotateDrawsAllClassicCollectionsAndLineCircleGeometry() -> None:
    image = np.zeros((60, 80, 3), dtype=np.uint8)
    space = CoordinateSpace2D(sourceId="source", imageWidth=80, imageHeight=60)
    polygon = Polygon2D(
        (
            Point2D(5, 5, space),
            Point2D(20, 5, space),
            Point2D(20, 20, space),
            Point2D(5, 20, space),
        )
    )
    contours = ContourCollection(
        (ContourItem("contour-1", polygon, depth=0, isHole=False),), space
    )
    measurements = ShapeMeasurementCollection(
        (
            ShapeMeasurement(
                "measurement-1",
                "contour-1",
                "contour",
                225,
                60,
                Point2D(12.5, 12.5, space),
                BBox2D(5, 5, 15, 15, space),
                RotatedBox2D(12.5, 12.5, 15, 15, 15, space),
                0.78,
            ),
        ),
        space,
    )
    lines = LineCollection(
        (LineItem("line-1", Line2D(Point2D(25, 5, space), Point2D(45, 20, space))),),
        space,
    )
    circles = CircleCollection(
        (CircleItem("circle-1", Circle2D(Point2D(55, 20, space), 8)),),
        space,
    )
    matches = TemplateMatchCollection(
        "ccoeffNormed",
        "零件",
        10,
        8,
        (TemplateMatch("match-1", BBox2D(30, 35, 10, 8, space), 0.9, 0.95),),
        space,
    )

    result = AnnotateOperator().executeNode(
        {
            "image": image,
            "frame": BBox2D(0, 0, 80, 60, space).toPayload(),
            "contours": contours.toPayload(),
            "measurements": measurements,
            "lines": lines.toPayload(),
            "circles": circles,
            "matches": matches.toPayload(),
            "roi": Line2D(Point2D(2, 50, space), Point2D(70, 50, space)),
        },
        {"drawLabels": False, "thickness": 1},
        {},
    )

    assert result["status"] == "ok"
    assert result["outputs"]["drawnCount"] == 6
    assert np.count_nonzero(result["outputs"]["overlay"]) > 0
    assert np.count_nonzero(image) == 0
