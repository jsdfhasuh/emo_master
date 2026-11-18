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


def testAnnotateChineseClassStylesAndDashedLineOnGrayscaleCopy() -> None:
    image = np.full((108, 186), 28, dtype=np.uint8)
    space = CoordinateSpace2D(sourceId="simulation", imageWidth=186, imageHeight=108)
    frame = BBox2D(0, 0, 186, 108, space)
    detections = DetectionCollection(tuple(
        Detection2D.fromGeometry(str(index), index, label, 0.92,
                                 BBox2D(x, 36, 35, 22, space))
        for index, (label, x) in enumerate((("upper", 8), ("lower", 65), ("centre", 127)))
    ), space).toPayload()
    lines = LineCollection((LineItem("ref", Line2D(Point2D(8, 92, space), Point2D(170, 92, space))),), space)
    inputs = {"image": image, "frame": frame.toPayload(), "detections": detections, "lines": lines.toPayload()}
    colors = [[42, 209, 78], [82, 109, 241], [43, 199, 250]]
    styles = {name: {"displayLabel": label, "color": color} for name, label, color in
              zip(("upper", "lower", "centre"), ("上端", "下端", "定位点"), colors)}
    params = {"classStyles": styles, "colorMode": "bgr", "labelBackground": True, "showConfidence": False,
              "lineStyle": "dashed", "endpointMarker": "dot", "lineColor": [255, 255, 255]}
    result = AnnotateOperator().executeNode(inputs, params, {})
    assert result["status"] == "ok" and result["outputs"]["drawnCount"] == 4
    overlay = result["outputs"]["overlay"]
    assert overlay.shape == (108, 186, 3)
    for x, color in zip((8, 65, 127), colors):
        assert overlay[58, x + 15].tolist() == color
        labelArea = overlay[10:35, x:x+26, :]
        assert np.any(np.all(labelArea == color, axis=2))
        assert np.any(np.all(labelArea == [255, 255, 255], axis=2) | np.all(labelArea == [0, 0, 0], axis=2))
    assert np.all(overlay[90, [8, 170]] == 255)
    line = overlay[92, 16:162]
    assert np.any(np.all(line == [28, 28, 28], axis=1))
    assert np.any(np.all(line == [255, 255, 255], axis=1))
    np.testing.assert_array_equal(image, np.full((108, 186), 28, dtype=np.uint8))
    assert result["outputs"]["frame"] == frame.toPayload()
    assert DetectionCollection.fromPayload(detections).items[0].label == "upper"

    error = AnnotateOperator().validateParams({"classStyles": {"upper": {"color": [True, 10, 250]}}})
    assert error is not None and error["code"] == "E_PARAM_INVALID"
