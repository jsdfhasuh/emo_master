from __future__ import annotations

import threading
import time

import numpy as np
import pytest

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


class _FailingLiveCamera(_FakeLiveCamera):
    release = threading.Event()

    def executeNode(self, inputs, params, context):
        assert self.release.wait(2.0)
        return {
            "status": "error",
            "error": {
                "code": "E_CAMERA_DEVICE_NOT_FOUND",
                "message": "IMV_CreateHandle failed (-106)",
            },
        }


@pytest.mark.parametrize("subscribeBeforeFailure", [False, True])
def testPreviewFailureSurvivesCleanupAndReachesSubscriber(subscribeBeforeFailure) -> None:
    _FailingLiveCamera.release.clear()
    published = threading.Event()
    events = []

    def publish(**event):
        events.append(event)
        published.set()

    manager = LivePreviewManager(
        {"vision.io.fake_camera": _descriptor(_FailingLiveCamera)},
        eventPublisher=publish,
    )
    sessionId, error = manager.open("vision.io.fake_camera", "p", "main", "camera", {})
    assert error is None and sessionId
    session = manager._sessions[sessionId]
    stream = manager.stream(sessionId) if subscribeBeforeFailure else None
    _FailingLiveCamera.release.set()
    session.thread.join(2.0)
    assert not session.thread.is_alive()
    assert published.is_set()
    assert sessionId not in manager._sessions
    assert session.operator.disposed == 1
    with pytest.raises(RuntimeError, match=r"E_CAMERA_DEVICE_NOT_FOUND.*-106"):
        next(stream if stream is not None else manager.stream(sessionId))
    assert events[0]["eventType"] == "node.preview.failed"
    assert events[0]["nodeId"] == "camera"
    assert events[0]["projectId"] == "p"
    assert manager.close(sessionId) is None
    assert sessionId not in manager._failures


def testPreviewFailureIsWrittenToRuntimeLogAndExposedByService(tmp_path) -> None:
    import json
    from types import SimpleNamespace

    import grpc

    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    _FailingLiveCamera.release.clear()
    service.livePreviewManager.operatorRegistry = {
        "vision.io.fake_camera": _descriptor(_FailingLiveCamera)
    }
    try:
        sessionId, error = service.livePreviewManager.open(
            "vision.io.fake_camera", "p", "main", "camera", {}
        )
        assert error is None and sessionId
        session = service.livePreviewManager._sessions[sessionId]
        _FailingLiveCamera.release.set()
        session.thread.join(2.0)
        assert not session.thread.is_alive()
        request = SimpleNamespace(session_id=sessionId)
        with pytest.raises(RuntimeError, match="-106"):
            next(service.StreamOperatorPreviewFrames(request, None))
        aborted = []

        def abort(code, message):
            aborted.append((code, message))
            raise RuntimeError(message)

        with pytest.raises(RuntimeError, match="-106"):
            next(service.StreamOperatorPreviewFrames(request, SimpleNamespace(abort=abort)))
        assert aborted[0][0] == grpc.StatusCode.FAILED_PRECONDITION
        from concurrent.futures import ThreadPoolExecutor

        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2, runtime_pb2_grpc

        with ThreadPoolExecutor(max_workers=2) as executor:
            server = grpc.server(executor)
            runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(service, server)
            port = server.add_insecure_port("127.0.0.1:0")
            server.start()
            try:
                with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                    stub = runtime_pb2_grpc.RuntimeServiceStub(channel)
                    requestType = runtime_pb2.StreamOperatorPreviewFramesRequest
                    with pytest.raises(grpc.RpcError) as remoteError:
                        next(stub.StreamOperatorPreviewFrames(
                            requestType(session_id=sessionId), timeout=3.0
                        ))
                    assert remoteError.value.code() == grpc.StatusCode.FAILED_PRECONDITION
                    assert "-106" in remoteError.value.details()
            finally:
                server.stop(0).wait()
    finally:
        _FailingLiveCamera.release.set()
        service.close()
    records = [
        json.loads(line)
        for path in (tmp_path / "logs").glob("runtime-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    failures = [record for record in records if record["eventType"] == "node.preview.failed"]
    assert len(failures) == 1
    assert "-106" in failures[0]["message"]
