import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested
from emo_master.core.contracts.geometry2d import BBox2D
from emo_master.plugins.builtins.video_loader.operator import VideoLoaderOperator


def makeVideo(path, count=11, fps=10.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = "mp4v" if path.suffix == ".mp4" else "MJPG"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), fps, (32, 24))
    assert writer.isOpened()
    try:
        for index in range(count):
            writer.write(np.full((24, 32, 3), index * 10, np.uint8))
    finally:
        writer.release()
    return path


def read(operator, path, inputs=None, context=None, **params):
    return operator.executeNode(inputs or {}, {"videoPath": str(path), **params}, context or {})


@pytest.mark.parametrize("suffix", [".avi", ".mp4"])
@pytest.mark.parametrize("interval,expected", [
    (.2, [0, 2, 4, 6, 8, 10]), (.25, [0, 3, 5, 8, 10]),
    (.01, list(range(11))), (2.0, [0]),
])
def testRealVideoSamplingAndFinalImage(tmp_path, suffix, interval, expected):
    path = makeVideo(tmp_path / ("video" + suffix))
    operator = VideoLoaderOperator()
    try:
        for index, frameIndex in enumerate(expected):
            result = read(operator, path, intervalSec=interval)
            assert result["status"] == "ok", result
            out = result["outputs"]
            assert out["index"] == index
            assert out["frameIndex"] == frameIndex
            assert out["timestampSec"] == pytest.approx(frameIndex / 10)
            assert out["hasNext"] is (index + 1 < len(expected))
            assert out["image"].shape == (24, 32, 3)
            assert np.mean(out["image"]) == pytest.approx(frameIndex * 10, abs=5)
            frame = BBox2D.fromPayload(out["frame"])
            assert (frame.width, frame.height) == (32, 24)
            assert frame.coordinateSpace.sourceId.endswith(f"#frame={frameIndex}")
        assert operator._capture is None
        for _ in range(2):
            assert read(operator, path, intervalSec=interval)["error"]["code"] == "E_INPUT_EXHAUSTED"
    finally:
        operator.disposeOperator()


def testUnicodeSingleFrameAndGrayscale(tmp_path):
    path = makeVideo(tmp_path / "\u89c6\u9891" / "\u6d4b\u8bd5.avi", count=1)
    operator = VideoLoaderOperator()
    result = read(operator, path, colorMode="grayscale")
    assert result["status"] == "ok", result
    assert result["outputs"]["image"].shape == (24, 32)
    assert result["outputs"]["hasNext"] is False
    assert operator._capture is None


@pytest.mark.parametrize("change", ["reset", "job", "path", "interval", "color", "dispose"])
def testRestartAndRelease(tmp_path, change):
    path = makeVideo(tmp_path / "video.avi")
    operator = VideoLoaderOperator()
    context = {"jobId": "first"}
    params = {"intervalSec": .2}
    try:
        assert read(operator, path, context=context, **params)["outputs"]["index"] == 0
        capture = operator._capture
        assert capture.isOpened()
        inputs = {}
        if change == "reset":
            inputs["reset"] = True
        elif change == "job":
            context["jobId"] = "second"
        elif change == "path":
            path = makeVideo(tmp_path / "other.avi")
        elif change == "interval":
            params["intervalSec"] = .3
        elif change == "color":
            params["colorMode"] = "grayscale"
        else:
            operator.disposeOperator()
        assert read(operator, path, inputs, context, **params)["outputs"]["index"] == 0
        assert not capture.isOpened()
    finally:
        operator.disposeOperator()


@pytest.mark.parametrize("interval", [0, -.1, True, "1", None, float("nan"), float("inf")])
def testInvalidInterval(interval):
    result = read(VideoLoaderOperator(), "video.avi", intervalSec=interval)
    assert result["error"]["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("params", [{}, {"videoPath": " "}, {"videoPath": 5},
                                       {"videoPath": "a", "colorMode": "bad"}])
def testInvalidParams(params):
    assert VideoLoaderOperator().executeNode({}, params, {})["error"]["code"] == "E_PARAM_INVALID"


def testMissingAndInvalidVideo(tmp_path):
    path = tmp_path / "video.avi"
    operator = VideoLoaderOperator()
    assert read(operator, path)["error"]["code"] == "E_INPUT_MISSING"
    path.write_bytes(b"not a video")
    assert read(operator, path)["error"]["code"] == "E_INPUT_DECODE"
    assert operator._capture is None
    assert read(operator, path, {"reset": 1})["error"]["code"] == "E_INPUT_TYPE"


@pytest.mark.parametrize("at", [1, 4])
def testCancellationClosesCaptureDuringSampling(tmp_path, at):
    path = makeVideo(tmp_path / "video.avi")
    operator = VideoLoaderOperator()
    read(operator, path, intervalSec=.2)
    capture = operator._capture
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == at:
            raise CancellationRequested("cancelled")

    with pytest.raises(CancellationRequested):
        read(operator, path, intervalSec=.2, context={"raiseIfCancellationRequested": cancel})
    assert not capture.isOpened()
    assert operator._capture is None
    assert operator._pending is None


def testInstancesAreIndependent(tmp_path):
    path = makeVideo(tmp_path / "video.avi")
    first, second = VideoLoaderOperator(), VideoLoaderOperator()
    try:
        assert read(first, path, intervalSec=.2)["outputs"]["index"] == 0
        assert read(first, path, intervalSec=.2)["outputs"]["index"] == 1
        assert read(second, path, intervalSec=.2)["outputs"]["index"] == 0
    finally:
        first.disposeOperator()
        second.disposeOperator()


def testManifestMatchesImplementation():
    root = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins/video_loader"
    manifest = json.loads((root / "manifest.json").read_text())
    for key in ("operatorId", "version", "displayName", "inputPorts", "outputPorts", "paramSchema"):
        assert manifest[key] == getattr(VideoLoaderOperator.meta, key)


@pytest.mark.parametrize("timestamps,expected", [
    ([0, .12, .18, .32, .51], [0, 3, 4]),
    ([0, 0, 0, 0, 0], [0, 2, 4]),
])
def testTimestampSamplingAndFpsFallback(tmp_path, monkeypatch, timestamps, expected):
    path = tmp_path / "fixture.avi"
    path.touch()

    class Capture:
        def __init__(self):
            self.position = -1
            self.open = True
            self.retrieved = []

        def isOpened(self):
            return self.open

        def get(self, prop):
            if prop == cv2.CAP_PROP_FPS:
                return 10.0
            assert prop == cv2.CAP_PROP_POS_MSEC
            return timestamps[self.position] * 1000

        def grab(self):
            self.position += 1
            return self.position < len(timestamps)

        def retrieve(self):
            self.retrieved.append(self.position)
            return True, np.full((24, 32, 3), self.position, np.uint8)

        def release(self):
            self.open = False

    capture = Capture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: capture)
    operator = VideoLoaderOperator()
    try:
        for index, frameIndex in enumerate(expected):
            result = read(operator, path, intervalSec=.2)
            assert result["status"] == "ok", result
            assert result["outputs"]["frameIndex"] == frameIndex
            assert result["outputs"]["hasNext"] is (index + 1 < len(expected))
        assert capture.retrieved == expected
        assert not capture.isOpened()
    finally:
        operator.disposeOperator()
