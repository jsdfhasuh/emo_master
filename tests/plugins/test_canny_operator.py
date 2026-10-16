import numpy as np
import pytest

from emo_master.core.contracts.geometry2d import BBox2D, CoordinateSpace2D
from emo_master.plugins.builtins.canny_edge.operator import CannyEdgeOperator


def testCannyOperatorHasEdgeOutput() -> None:
    image = np.zeros((64, 64), dtype=np.uint8)
    image[20:40, 20:40] = 255
    operatorInstance = CannyEdgeOperator()

    result = operatorInstance.executeNode(
        {"image": image},
        {
            "thresholdLow": 50,
            "thresholdHigh": 150,
            "apertureSize": 3,
            "l2Gradient": False,
        },
        {},
    )

    assert result["status"] == "ok"
    assert "edges" in result["outputs"]
    frame = BBox2D.fromPayload(result["outputs"]["frame"])
    assert (frame.width, frame.height) == (64.0, 64.0)


@pytest.mark.parametrize(
    "color,aperture,l2Gradient",
    [(False, 3, False), (True, 3, True), (False, 5, True), (True, 7, False)],
    ids=["gray-3-l1", "bgr-3-l2", "gray-5-l2", "bgr-7-l1"],
)
def testCannyDetectsRectangleAndOnlyPaintsEdgesWithoutMutatingInput(
    color: bool, aperture: int, l2Gradient: bool
) -> None:
    gray = np.zeros((64, 64), dtype=np.uint8)
    gray[20:44, 18:46] = 255
    image = np.repeat(gray[:, :, None], 3, axis=2) if color else gray
    original = image.copy()
    originalBgr = np.repeat(gray[:, :, None], 3, axis=2)
    space = CoordinateSpace2D(
        sourceId="synthetic-rectangle",
        imageWidth=64,
        imageHeight=64,
        transformToSource=(1, 0, 0, 1, 120, 80),
    )
    frame = BBox2D(0, 0, 64, 64, space)
    framePayload = frame.toPayload()

    result = CannyEdgeOperator().executeNode(
        {"image": image, "frame": framePayload},
        {
            "thresholdLow": 50,
            "thresholdHigh": 150,
            "apertureSize": aperture,
            "l2Gradient": l2Gradient,
        },
        {},
    )

    assert result["status"] == "ok", result
    edges = result["outputs"]["edges"]
    overlay = result["outputs"]["overlay"]
    assert edges.shape == gray.shape and edges.dtype == np.uint8
    assert overlay.shape == (64, 64, 3) and overlay.dtype == np.uint8
    assert set(np.unique(edges)) == {0, 255}
    # Check all four known sides with a margin for OpenCV edge localization.
    assert np.count_nonzero(edges[18:23, 23:41]) >= 15
    assert np.count_nonzero(edges[41:46, 23:41]) >= 15
    assert np.count_nonzero(edges[25:39, 16:21]) >= 11
    assert np.count_nonzero(edges[25:39, 43:48]) >= 11
    ys, xs = np.nonzero(edges)
    assert np.all((16 <= xs) & (xs <= 47) & (18 <= ys) & (ys <= 45))
    assert not np.any(edges[25:39, 23:41])
    selected = edges != 0
    assert np.all(overlay[selected] == np.asarray([0, 255, 0], dtype=np.uint8))
    np.testing.assert_array_equal(overlay[~selected], originalBgr[~selected])
    np.testing.assert_array_equal(image, original)
    assert not np.shares_memory(overlay, image)
    assert not np.shares_memory(edges, image)
    assert BBox2D.fromPayload(result["outputs"]["frame"]) == frame
    assert framePayload == frame.toPayload()


@pytest.mark.parametrize(
    "inputs,code",
    [
        ({}, "E_INPUT_MISSING"),
        ({"image": [[0, 255]]}, "E_INPUT_TYPE"),
        ({"image": np.zeros((8, 8), dtype=np.float32)}, "E_INPUT_TYPE"),
        ({"image": np.zeros((0, 8), dtype=np.uint8)}, "E_INPUT_SHAPE"),
        ({"image": np.zeros((8, 8, 4), dtype=np.uint8)}, "E_INPUT_SHAPE"),
    ],
    ids=["missing", "not-array", "float-pixels", "empty", "four-channels"],
)
def testCannyRejectsInvalidImages(inputs: dict[str, object], code: str) -> None:
    result = CannyEdgeOperator().executeNode(inputs, {}, {})

    assert result["status"] == "error"
    assert result["error"]["code"] == code
    assert "outputs" not in result


@pytest.mark.parametrize(
    "params",
    [
        {"thresholdLow": -1},
        {"thresholdHigh": 256},
        {"thresholdHigh": "150"},
        {"thresholdLow": 150, "thresholdHigh": 150},
        {"thresholdLow": 151, "thresholdHigh": 150},
        {"apertureSize": 4},
    ],
    ids=["low-range", "high-range", "high-type", "equal", "reversed", "aperture"],
)
def testCannyRejectsInvalidParametersWithoutMutatingImage(
    params: dict[str, object],
) -> None:
    image = np.arange(64, dtype=np.uint8).reshape(8, 8)
    original = image.copy()

    result = CannyEdgeOperator().executeNode({"image": image}, params, {})

    assert result["status"] == "error"
    assert result["error"]["code"] == "E_PARAM_INVALID"
    assert "outputs" not in result
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize("kind", ["malformed", "wrong-size", "outside-image"])
def testCannyRejectsInvalidFrameWithoutMutatingImage(kind: str) -> None:
    image = np.arange(64, dtype=np.uint8).reshape(8, 8)
    original = image.copy()
    width = 9 if kind == "wrong-size" else 8
    space = CoordinateSpace2D(
        sourceId="synthetic-frame", imageWidth=width, imageHeight=8
    )
    framePayload = (
        {}
        if kind == "malformed"
        else BBox2D(1 if kind == "outside-image" else 0, 0, 8, 8, space).toPayload()
    )

    result = CannyEdgeOperator().executeNode(
        {"image": image, "frame": framePayload}, {}, {}
    )

    assert result["status"] == "error"
    expected = "E_INPUT_TYPE" if kind == "malformed" else "E_INPUT_SHAPE"
    assert result["error"]["code"] == expected
    assert "outputs" not in result
    np.testing.assert_array_equal(image, original)
