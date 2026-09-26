import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import (
    BBox2D,
    ContourCollection,
    CoordinateSpace2D,
    Histogram,
)
from emo_master.plugins.builtins._arithmetic_operators import (
    AbsDiffOperator,
    AddWeightedOperator,
    ApplyMaskOperator,
)
from emo_master.plugins.builtins._intensity_operators import (
    ClaheOperator,
    EqualizeOperator,
    HistogramOperator,
)
from emo_master.plugins.builtins._shape_operators import ContourExtractionOperator
from emo_master.plugins.builtins.threshold.operator import ThresholdOperator


def _frame(
    width: int,
    height: int,
    *,
    sourceId: str = "source",
    rect: tuple[float, float, float, float] | None = None,
) -> BBox2D:
    space = CoordinateSpace2D(
        sourceId=sourceId,
        imageWidth=width,
        imageHeight=height,
    )
    return BBox2D(*(rect or (0, 0, width, height)), space)


def testAbsDiffAndAddWeightedUseStrictShapesAndIntersectFrames() -> None:
    imageA = np.asarray([[0, 250], [100, 200]], dtype=np.uint8)
    imageB = np.asarray([[10, 20], [50, 100]], dtype=np.uint8)
    inputs = {
        "imageA": imageA,
        "imageB": imageB,
        "frameA": _frame(2, 2, rect=(0, 0, 2, 2)).toPayload(),
        "frameB": _frame(2, 2, rect=(1, 0, 1, 2)).toPayload(),
    }

    difference = AbsDiffOperator().executeNode(inputs, {}, {})
    weighted = AddWeightedOperator().executeNode(
        inputs, {"alpha": 1.0, "beta": 1.0, "gamma": 20.0}, {}
    )

    np.testing.assert_array_equal(
        difference["outputs"]["image"], cv2.absdiff(imageA, imageB)
    )
    np.testing.assert_array_equal(
        weighted["outputs"]["image"],
        np.asarray([[30, 255], [170, 255]], dtype=np.uint8),
    )
    frame = BBox2D.fromPayload(difference["outputs"]["frame"])
    assert (frame.x, frame.y, frame.width, frame.height) == (1, 0, 1, 2)

    mismatch = AbsDiffOperator().executeNode(
        {"imageA": imageA, "imageB": np.zeros((3, 2), dtype=np.uint8)}, {}, {}
    )
    assert mismatch["error"]["code"] == "E_INPUT_SHAPE"


def testApplyMaskSupportsFillInvertAndRequiredMaskError() -> None:
    image = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    mask = np.asarray([[0, 255, 0], [255, 0, 255]], dtype=np.uint8)
    operator = ApplyMaskOperator()

    normal = operator.executeNode(
        {"image": image, "mask": mask}, {"fillValue": 7}, {}
    )
    inverted = operator.executeNode(
        {"image": image, "mask": mask}, {"fillValue": 7, "invert": True}, {}
    )
    missing = operator.executeNode({"image": image}, {}, {})
    mismatchedFrame = operator.executeNode(
        {
            "image": image,
            "mask": mask,
            "frame": _frame(3, 2, sourceId="image").toPayload(),
            "maskFrame": _frame(3, 2, sourceId="mask").toPayload(),
        },
        {},
        {},
    )

    assert np.all(normal["outputs"]["image"][mask == 0] == 7)
    np.testing.assert_array_equal(normal["outputs"]["image"][mask != 0], image[mask != 0])
    assert np.all(inverted["outputs"]["image"][mask != 0] == 7)
    assert missing["error"]["code"] == "E_INPUT_MISSING"
    assert mismatchedFrame["error"]["code"] == "E_INPUT_SHAPE"


def testHistogramGrayBgrProbabilityMaskAndEmptySelection() -> None:
    gray = np.asarray([[0, 1, 254, 255]], dtype=np.uint8)
    counts = HistogramOperator().executeNode(
        {"image": gray}, {"bins": 2, "normalization": "counts"}, {}
    )
    histogram = Histogram.fromPayload(counts["outputs"]["histogram"])
    assert histogram.pixelCount == 4
    assert histogram.binEdges == (0, 128, 256)
    assert histogram.channels[0].values == (2, 2)

    bgr = np.dstack((gray, np.full_like(gray, 128), np.full_like(gray, 255)))
    mask = np.asarray([[255, 0, 255, 0]], dtype=np.uint8)
    probability = HistogramOperator().executeNode(
        {"image": bgr, "mask": mask},
        {"bins": 4, "normalization": "probability"},
        {},
    )
    bgrHistogram = Histogram.fromPayload(probability["outputs"]["histogram"])
    assert bgrHistogram.colorSpace == "BGR"
    assert bgrHistogram.pixelCount == 2
    assert [channel.name for channel in bgrHistogram.channels] == ["B", "G", "R"]
    assert all(abs(sum(channel.values) - 1.0) < 1e-9 for channel in bgrHistogram.channels)

    empty = HistogramOperator().executeNode(
        {"image": gray, "mask": np.zeros_like(gray)},
        {"bins": 4, "normalization": "probability"},
        {},
    )
    emptyHistogram = Histogram.fromPayload(empty["outputs"]["histogram"])
    assert emptyHistogram.pixelCount == 0
    assert emptyHistogram.channels[0].values == (0, 0, 0, 0)

    orphanFrame = HistogramOperator().executeNode(
        {"image": gray, "maskFrame": _frame(4, 1).toPayload()}, {}, {}
    )
    assert orphanFrame["error"]["code"] == "E_INPUT_SHAPE"


def testEqualizeAndClaheUseGrayOrLabLightnessWithoutChangingInputs() -> None:
    gray = np.asarray([[0, 0, 100, 255], [5, 20, 100, 200]], dtype=np.uint8)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    original = bgr.copy()

    equalized = EqualizeOperator().executeNode({"image": bgr}, {}, {})
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lightness, channelA, channelB = cv2.split(lab)
    expected = cv2.cvtColor(
        cv2.merge((cv2.equalizeHist(lightness), channelA, channelB)),
        cv2.COLOR_LAB2BGR,
    )
    np.testing.assert_array_equal(equalized["outputs"]["image"], expected)

    claheParams = {"clipLimit": 2.0, "tileGridWidth": 2, "tileGridHeight": 2}
    claheResult = ClaheOperator().executeNode({"image": gray}, claheParams, {})
    expectedClahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(2, 2)).apply(gray)
    np.testing.assert_array_equal(claheResult["outputs"]["image"], expectedClahe)
    np.testing.assert_array_equal(bgr, original)

    invalid = ClaheOperator().executeNode({"image": gray}, {"clipLimit": 0}, {})
    assert invalid["error"]["code"] == "E_PARAM_INVALID"


def testAbsDiffThresholdContourChainFindsChangedRegion() -> None:
    background = np.zeros((12, 12), dtype=np.uint8)
    current = background.copy()
    current[3:9, 4:10] = 180
    difference = AbsDiffOperator().executeNode(
        {"imageA": current, "imageB": background}, {}, {}
    )
    threshold = ThresholdOperator().executeNode(
        {
            "image": difference["outputs"]["image"],
            "frame": difference["outputs"]["frame"],
        },
        {"mode": "fixed", "threshold": 100},
        {},
    )
    contours = ContourExtractionOperator().executeNode(
        {
            "mask": threshold["outputs"]["mask"],
            "frame": threshold["outputs"]["frame"],
        },
        {"retrievalMode": "external"},
        {},
    )

    collection = ContourCollection.fromPayload(contours["outputs"]["contours"])
    assert len(collection.items) == 1
    xs = [point.x for point in collection.items[0].polygon.points]
    ys = [point.y for point in collection.items[0].polygon.points]
    assert (min(xs), min(ys), max(xs), max(ys)) == (4, 3, 9, 8)
