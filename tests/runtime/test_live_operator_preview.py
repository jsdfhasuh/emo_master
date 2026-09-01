from __future__ import annotations

import threading
import time

import numpy as np

from emo_master.apps.runtime.preview.live import LivePreviewManager
from emo_master.core.plugin.models import (
    OperatorEditorSpec,
    PluginDescriptor,
    PluginManifest,
)


class _FakeLiveCamera:
    instances: list["_FakeLiveCamera"] = []

    def __init__(self) -> None:
        self.initialized = 0
        self.disposed = 0
        self.seenParams: list[dict[str, object]] = []
        self.instances.append(self)

    def initOperator(self, context: dict[str, object]) -> None:
        assert context["isPreview"] is True
        self.initialized += 1

    def validateParams(self, params: dict[str, object]):
        assert params["triggerMode"] == "freeRun"
        return None

    def executeNode(self, inputs, params, context):
        assert inputs == {}
        assert context["isPreview"] is True
        self.seenParams.append(dict(params))
        return {
            "status": "ok",
            "outputs": {
                "image": np.full((100, 2500, 3), 127, dtype=np.uint8),
                "blockId": len(self.seenParams),
                "deviceTimestamp": 123,
                "actualExposureUs": 456.5,
            },
        }

    def disposeOperator(self) -> None:
        self.disposed += 1


def _descriptor(operatorClass: type = _FakeLiveCamera) -> PluginDescriptor:
    return PluginDescriptor(
        manifest=PluginManifest(
            operatorId="vision.io.fake_camera",
            displayName="Fake Camera",
            version="1.0.0",
            entry="tests.fake:Fake",
            category="test",
            iconKey="camera",
            summary="",
            inputPorts={},
            outputPorts={"image": "image"},
            paramSchema={"type": "object"},
            minCoreVersion="0.1.0",
            maxCoreVersion="1.x",
            editor=OperatorEditorSpec(
                schemaVersion="1.0",
                kind="customUi",
                openMode="window",
                uiResource="ui/editor.ui",
                controllerEntry="tests.fake:Controller",
                fallback="schemaForm",
                previewMode="live",
            ),
        ),
        operatorClass=operatorClass,
    )


def testLivePreviewForcesFreeRunKeepsLatestJpegAndDisposes() -> None:
    _FakeLiveCamera.instances.clear()
    manager = LivePreviewManager({"vision.io.fake_camera": _descriptor()})
    sessionId, error = manager.open(
        "vision.io.fake_camera",
        "project-a",
        "main",
        "camera",
        {"triggerMode": "hardware", "captureTimeoutMs": 5000},
    )
    assert error is None
    assert sessionId
    frame = next(manager.stream(sessionId))
    assert frame.jpeg.startswith(b"\xff\xd8")
    assert frame.width == 1920
    assert frame.height == 77
    assert frame.blockId >= 1
    assert frame.deviceTimestamp == 123
    assert frame.actualExposureUs == 456.5

    assert manager.close(sessionId) is None
    instance = _FakeLiveCamera.instances[-1]
    assert instance.initialized == 1
    assert instance.disposed == 1
    assert instance.seenParams[0]["triggerMode"] == "freeRun"
    assert instance.seenParams[0]["captureTimeoutMs"] == 1000
    assert manager.close(sessionId) is None


class _DelayedLiveCamera:
    instances: list["_DelayedLiveCamera"] = []
    started = threading.Event()

    def __init__(self) -> None:
        self.disposed = 0
        self.instances.append(self)

    def initOperator(self, _context: dict[str, object]) -> None:
        return

    def validateParams(self, _params: dict[str, object]):
        return None

    def executeNode(self, _inputs, _params, _context):
        self.started.set()
        time.sleep(0.15)
        return {
            "status": "ok",
            "outputs": {
                "image": np.zeros((8, 8, 3), dtype=np.uint8),
            },
        }

    def disposeOperator(self) -> None:
        self.disposed += 1


def testTimedOutLiveCloseDisposesWhenCaptureThreadEventuallyReturns() -> None:
    _DelayedLiveCamera.instances.clear()
    _DelayedLiveCamera.started.clear()
    manager = LivePreviewManager(
        {"vision.io.fake_camera": _descriptor(_DelayedLiveCamera)}
    )
    sessionId, error = manager.open(
        "vision.io.fake_camera",
        "project-a",
        "main",
        "camera",
        {"triggerMode": "hardware"},
    )
    assert error is None
    assert sessionId
    assert _DelayedLiveCamera.started.wait(1.0)

    closeError = manager.close(sessionId, timeoutSeconds=0.01)
    assert closeError is not None
    deadline = time.monotonic() + 1.0
    instance = _DelayedLiveCamera.instances[-1]
    while instance.disposed == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert instance.disposed == 1
    assert sessionId not in manager._sessions
    assert manager.close(sessionId) is None
