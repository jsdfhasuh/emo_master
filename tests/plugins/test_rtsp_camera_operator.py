import json
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.plugins.builtins.rtsp_camera import operator as module
from emo_master.plugins.builtins.rtsp_camera.operator import RtspCameraOperator


PARAMS = {"rtspUrl": "rtsp://camera.invalid:554/Streaming/Channels/102", "password": "test-only"}


class Capture:
    def __init__(self, *, opened=True, image=None):
        self.opened = opened
        self.image = np.full((6, 8, 3), 42, np.uint8) if image is None else image
        self.calls = []
        self.released = False

    def open(self, *args):
        self.calls.append(args)
        return self.opened

    def read(self):
        return True, self.image

    def release(self):
        self.released = True


@pytest.mark.parametrize("mode,shape", [("color", (6, 8, 3)), ("grayscale", (6, 8))])
def testRtspCapturesStandardImageAndReleases(monkeypatch, mode, shape):
    capture = Capture()
    monkeypatch.setattr(module.cv2, "VideoCapture", lambda: capture)
    result = RtspCameraOperator().executeNode({}, {**PARAMS, "colorMode": mode}, {})
    assert result["status"] == "ok"
    assert result["outputs"]["image"].shape == shape
    assert result["outputs"]["capturedAt"] > 0
    frame = result["outputs"]["frame"]
    assert frame["coordinateSpace"]["imageWidth"] == 8
    assert frame["coordinateSpace"]["imageHeight"] == 6
    assert PARAMS["password"] not in json.dumps(frame)
    assert capture.calls[0][1:] == (cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
    assert capture.released


def testCredentialsAreEncodedAndEnvironmentTakesPrecedence(monkeypatch):
    capture = Capture()
    monkeypatch.setattr(module.cv2, "VideoCapture", lambda: capture)
    monkeypatch.setenv("TEST_CAMERA_PASSWORD", "p:@/# %")
    params = {**PARAMS, "passwordEnv": "TEST_CAMERA_PASSWORD", "username": "operator@example",
              "rtspUrl": PARAMS["rtspUrl"] + "?token=private-token"}
    result = RtspCameraOperator().executeNode({}, params, {})
    url = capture.calls[0][0]
    assert "operator%40example:p%3A%40%2F%23%20%25@" in url
    assert "test-only" not in url
    assert "private-token" not in json.dumps(result["outputs"]["frame"])


@pytest.mark.parametrize("changed", [
    {"rtspUrl": "http://camera.invalid/live"}, {"rtspUrl": "rtsp://admin:secret@camera.invalid/live"},
    {"rtspUrl": "rtsp://camera.invalid:0/live"}, {"rtspUrl": "rtsp://[bad/live"},
    {"rtspUrl": "rtsp://camera.invalid/live\n"}, {"openTimeoutMs": 0}, {"readTimeoutMs": True},
    {"colorMode": "bad"}, {"password": 123},
])
def testInvalidParametersDoNotConnect(monkeypatch, changed):
    monkeypatch.setattr(module.cv2, "VideoCapture", lambda: pytest.fail("unexpected network access"))
    result = RtspCameraOperator().executeNode({}, {**PARAMS, **changed}, {})
    assert result["error"]["code"] == "E_PARAM_INVALID"
    assert "secret" not in result["error"]["message"]


def testMissingEnvironmentDoesNotConnect(monkeypatch):
    monkeypatch.delenv("TEST_CAMERA_PASSWORD", raising=False)
    monkeypatch.setattr(module.cv2, "VideoCapture", lambda: pytest.fail("unexpected network access"))
    result = RtspCameraOperator().executeNode({}, {**PARAMS, "passwordEnv": "TEST_CAMERA_PASSWORD"}, {})
    assert result["error"]["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("failure", ["open", "read", "exception"])
def testFailuresReleaseAndNextInvocationCanReconnect(monkeypatch, failure):
    first, second = Capture(opened=failure != "open"), Capture()
    if failure == "read":
        first.read = lambda: (False, None)
    elif failure == "exception":
        def fail():
            raise cv2.error("sensitive backend URL and test-only")
        first.read = fail
    captures = iter([first, second])
    monkeypatch.setattr(module.cv2, "VideoCapture", lambda: next(captures))
    operator = RtspCameraOperator()
    result = operator.executeNode({}, PARAMS, {})
    assert result["status"] == "error" and first.released
    assert "test-only" not in str(result)
    assert operator.executeNode({}, PARAMS, {})["status"] == "ok"
    assert second.released


@pytest.mark.parametrize("when", ["before", "open", "read"])
def testCancellationDoesNotPublishAndReleases(monkeypatch, when):
    token, capture = CancellationToken(), Capture()
    if when == "before":
        token.cancel()
        monkeypatch.setattr(module.cv2, "VideoCapture", lambda: pytest.fail("unexpected connection"))
    else:
        monkeypatch.setattr(module.cv2, "VideoCapture", lambda: capture)
        original = getattr(capture, when)
        def stop(*args):
            token.cancel()
            return original(*args)
        monkeypatch.setattr(capture, when, stop)
    with pytest.raises(CancellationRequested):
        RtspCameraOperator().executeNode({}, PARAMS, {"raiseIfCancellationRequested": token.raise_if_cancelled})
    assert when == "before" or capture.released


def testEachCallUsesNewConnectionAndCurrentParameters(monkeypatch):
    captures = []
    def factory():
        capture = Capture()
        captures.append(capture)
        return capture
    monkeypatch.setattr(module.cv2, "VideoCapture", factory)
    operator = RtspCameraOperator()
    for host in ("first.invalid", "second.invalid"):
        result = operator.executeNode({}, {**PARAMS, "rtspUrl": f"rtsp://{host}/live"}, {})
        assert result["status"] == "ok"
        assert urlsplit(captures[-1].calls[0][0]).hostname == host
    assert len(captures) == 2 and all(c.released for c in captures)


def testQuerySelectedChannelsKeepDistinctSafeFrameIdentities(monkeypatch):
    monkeypatch.setattr(module.cv2, "VideoCapture", Capture)
    identities = []
    for channel in (1, 2, 1):
        result = RtspCameraOperator().executeNode({}, {**PARAMS,
            "rtspUrl": f"rtsp://camera.invalid/cam/realmonitor?channel={channel}&token=private-token"}, {})
        identities.append(result["outputs"]["frame"]["coordinateSpace"]["sourceId"])
    assert identities[0] != identities[1] and identities[0] == identities[2]
    assert all("private-token" not in value for value in identities)


def testInstalledFfmpegAcceptsTimeoutOptionsOnLocalVideo(tmp_path, monkeypatch):
    path = tmp_path / "probe.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (16, 12))
    assert writer.isOpened()
    try:
        writer.write(np.full((12, 16, 3), 96, np.uint8))
    finally:
        writer.release()
    original = cv2.VideoCapture
    class LocalCapture:
        def __init__(self):
            self.capture = original()
        def open(self, url, backend, options):
            return self.capture.open(str(path), backend, options)
        def read(self):
            return self.capture.read()
        def release(self):
            self.capture.release()
    monkeypatch.setattr(module.cv2, "VideoCapture", LocalCapture)
    result = RtspCameraOperator().executeNode({}, PARAMS, {})
    assert result["status"] == "ok", result
    assert np.mean(result["outputs"]["image"]) == pytest.approx(96, abs=3)


def testManifestMatchesImplementation():
    manifest = json.loads(Path(module.__file__).with_name("manifest.json").read_text())
    for field in ("inputPorts", "outputPorts", "paramSchema"):
        assert manifest[field] == getattr(RtspCameraOperator.meta, field)
