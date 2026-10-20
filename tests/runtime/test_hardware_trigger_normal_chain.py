"""Boundary simulation of actual operators, not old-algorithm equivalence.

Known external substitutes: IMV SDK, SLMP loopback server, ONNX inference backend,
gateway loopback servers. No fabricated final points or driver-side business logic.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import struct
from threading import Thread, Event
from time import sleep

import cv2
import numpy as np
import pytest

from emo_master import __version__
from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner, WorkflowExecutionError
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins import _communication_operators as communication
from emo_master.plugins.builtins.huaray_camera import operator as camera
from emo_master.plugins.builtins.yolo_inference import operator as yolo
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxInferenceResult
from emo_master.plugins.builtins._huaray_imv import IMV_TIMEOUT
from tests.plugins.test_huaray_camera_operator import _FakeImvApi
from tests.plugins.test_communication_operators import _ScriptedSlmpServer, _slmpResponse


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/hardware_trigger_normal_fixture"


def syntheticFrame(second=False):
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    if second:
        cv2.circle(image, (70, 80), 8, (255, 255, 255), -1)
    else:
        cv2.circle(image, (30, 40), 10, (255, 255, 255), -1)
        cv2.rectangle(image, (110, 40), (130, 60), (255, 255, 255), -1)
    return image


class FifoSdk(_FakeImvApi):
    def __init__(self, frames):
        super().__init__()
        self.frames = list(frames)
        self.originals = [i.copy() for i in frames]
        self.ints.update(Width=160, Height=120, OffsetX=0, OffsetY=0)
        self.waited = Event()

    def get_frame(self, handle, timeoutMs):
        self.waited.set()
        if not self.frames:
            sleep(timeoutMs / 1000)
            return IMV_TIMEOUT, None
        self.current = self.frames.pop(0)
        return super().get_frame(handle, timeoutMs)

    def convert_frame(self, handle, frame, outputColor, demosaic):
        return self.current.copy()


class LocalGateway:
    def __init__(self, channel, count=2, delay=.03):
        self.channel, self.count, self.delay = channel, count, delay
        self.requests, self.errors = [], []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(count)
        self.server.settimeout(2)
        self.port = self.server.getsockname()[1]
        self.thread = Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with self.server:
                for _ in range(self.count):
                    client, _ = self.server.accept()
                    with client:
                        client.settimeout(1)
                        data = b""
                        while data.count(b'"') < 2:
                            chunk = client.recv(100)
                            if not chunk:
                                raise RuntimeError("request closed before both quotes")
                            data += chunk
                        self.requests.append(data)
                        assert b"\n" not in data and data[:1] == data[-1:] == b'"'
                        x, y = data[1:-1].decode("ascii").split(",")
                        ack = (f"OK D7={2 if int(x) < 0 else 1} D6={abs(int(x))}" if self.channel == "plc"
                               else f"OK '{x},{y}EY'").encode("ascii") + b"\n"
                        sleep(self.delay)  # other hardware frames may already be buffered
                        client.sendall(ack[:3])
                        sleep(.005)
                        client.sendall(ack[3:])
        except BaseException as exc:
            self.errors.append(exc)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.thread.join(3)
        self.server.close()
        assert not self.thread.is_alive() and not self.errors, self.errors


def setup(tmp_path, monkeypatch, frames, station=1):
    payload = json.loads((EXAMPLE / "project.json").read_text(encoding="utf-8"))
    scan = PluginRegistry(coreVersion=__version__).scan(ROOT / "src/emo_master/plugins")
    assert not scan.rejectedOperators
    for name in ("a", "b", "holes"):
        (tmp_path / (name + ".onnx")).write_bytes(b"ONNX external boundary fixture, not a model")
    for s in (1, 2):
        (tmp_path / f"station{s}_reference.txt").write_bytes((EXAMPLE / f"station{s}_reference.txt").read_bytes())
    api = FifoSdk(frames)
    monkeypatch.setattr(camera, "getImvApi", lambda: api)
    yolo.YoloInferenceOperator.clearCache()
    calls = []
    class Backend:
        def __init__(self, modelPath):
            self.name = Path(modelPath).stem
        def predict(self, **kwargs):
            calls.append((self.name, kwargs["image"].copy()))
            box = {"a": [19, 29, 41, 51], "b": [109, 39, 132, 62], "holes": [61, 71, 79, 89]}[self.name]
            return OnnxInferenceResult(np.array([box], dtype=float), np.array([.99]), np.array([0]),
                                       {0: "test-target"}, "boundary-simulator", (1, 3, 640, 640))
    monkeypatch.setattr(yolo, "_createModel", Backend)
    store = SqliteStore(tmp_path / "variables.db")
    store.initialize()
    state = ProjectGlobalVariables(store, payload["project"]["projectId"], payload["globalVariables"], f"job-{station}")
    state.synchronize()
    state.initializeJob()
    return payload, scan.activeOperators, api, calls, state


class ObservedRunner(WorkflowRunner):
    def _runWorkflow(self, workflowId, inputs, context, cancellation, **kwargs):
        if workflowId.endswith("_holes"):
            station = workflowId.split("_")[0]
            self.holesCounts.append(self.globalVariables.get(station + "-shot-count"))
        return super()._runWorkflow(workflowId, inputs, context, cancellation, **kwargs)


@pytest.mark.parametrize("station", [1, 2])
def testFourHardwareFramesRealOperatorChainAndFifoWhileAckDelayed(tmp_path, monkeypatch, station):
    images = [syntheticFrame(bool(n % 2)) for n in range(4)]
    payload, operators, api, calls, state = setup(tmp_path, monkeypatch, images, station)
    root = f"station{station}"
    with ExitStack() as stack:
        plc = stack.enter_context(_ScriptedSlmpServer([_slmpResponse(struct.pack("<H", 101 + n)) for n in range(4)]))
        plcGateway = stack.enter_context(LocalGateway("plc"))
        robotGateway = stack.enter_context(LocalGateway("robot"))
        payload["workflows"][root + "_sample"]["nodes"][1]["params"]["port"] = plc.port
        for kind, gateway in (("first", plcGateway), ("second", robotGateway)):
            next(n for n in payload["workflows"][root + "_" + kind]["nodes"] if n["nodeId"] == "exchange")["params"]["port"] = gateway.port
        events = []
        runner = ObservedRunner(WorkflowCompiler(operators).compile(payload), operators, globalVariables=state,
                                retainOperators=True, eventPublisher=lambda **event: events.append(event))
        runner.holesCounts = []
        observed = []
        try:
            for n in range(4):
                result = runner.run(root, {}, RunContext.root(state.jobId, root, str(tmp_path)), CancellationToken())
                observed.append((result.outputs["shot"], state.get(root + "-shot-count"), result.outputs["blockId"], result.outputs["plcSample"]["values"]))
                assert result.outputs["robotAck" if n % 2 else "plcAck"] is True
                if n == 0:
                    # All later exposures already buffered; do not flush or choose latest.
                    assert len(api.frames) == 3
                    (tmp_path / f"station{station}_reference.txt").write_bytes(b"999 999\n")
        finally:
            runner.closeSession(RunContext.root(state.jobId, root))
            yolo.YoloInferenceOperator.clearCache()
        assert [(i[0], i[1]) for i in observed] == [(1, 1), (2, 0), (1, 1), (2, 0)]
        assert [i[2] for i in observed] == [41, 42, 43, 44]
        assert [i[3] for i in observed] == [[101], [102], [103], [104]]
        assert len(plc.requests) == 4
        assert runner.holesCounts == [0, 0]
        assert [name for name, _ in calls].count("a") == [name for name, _ in calls].count("b") == [name for name, _ in calls].count("holes") == 2
        assert api.openCount == 1 and not api.commands
        assert (api.releaseCount, api.stopCount, api.closeCount, api.destroyCount) == (4, 1, 1, 1)
        assert len(plcGateway.requests) == len(robotGateway.requests) == 2
        # Actual geometry and two non-commuting test transforms, not injected points.
        expectedPlc = b'"-106,165"' if station == 1 else b'"-66,155"'
        expectedRobot = b'"82.00,103.00"' if station == 1 else b'"62.00,98.00"'
        assert plcGateway.requests == [expectedPlc] * 2
        assert robotGateway.requests == [expectedRobot] * 2
        for original, supplied in zip(api.originals, images):
            assert np.array_equal(original, supplied)
        # Measurement never consumes the annotated overlay, even when enabled.
        assert all(e["context"].callerNodeId != "invalid" for e in events if e["eventType"] == "node.started")


def testNoHardwareFrameNoPlcNoCountAndCancellationReleasesResources(tmp_path, monkeypatch):
    payload, operators, api, calls, state = setup(tmp_path, monkeypatch, [])
    next(n for n in payload["workflows"]["station1"]["nodes"] if n["nodeId"] == "camera")["params"]["captureTimeoutMs"] = 30
    sampled = []
    monkeypatch.setattr(communication.Slmp3EClient, "readWords", lambda *args: sampled.append(args))
    runner = WorkflowRunner(WorkflowCompiler(operators).compile(payload), operators, globalVariables=state, retainOperators=True)
    token = CancellationToken()
    errors = []
    def run():
        try:
            runner.run("station1", {}, RunContext.root(state.jobId, "station1", str(tmp_path)), token)
        except Exception as exc:
            errors.append(exc)
    thread = Thread(target=run)
    thread.start()
    try:
        assert api.waited.wait(2)
        sleep(.13)
        assert thread.is_alive()  # beyond captureTimeoutMs, not a failed Job/retry
        assert not sampled and not calls and state.get("station1-shot-count") == 0
    finally:
        token.cancel()
        thread.join(1)
        runner.closeSession(RunContext.root(state.jobId, "station1"))
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], CancellationRequested)
    assert (api.stopCount, api.closeCount, api.destroyCount) == (1, 1, 1)


def testPlcFailureAfterExposureStopsBeforeIncrementAndVision(tmp_path, monkeypatch):
    payload, operators, api, calls, state = setup(tmp_path, monkeypatch, [syntheticFrame()])
    def fail(*args):
        raise communication.SlmpResponseError(0xC051)
    monkeypatch.setattr(communication.Slmp3EClient, "readWords", fail)
    runner = WorkflowRunner(WorkflowCompiler(operators).compile(payload), operators, globalVariables=state)
    with pytest.raises(WorkflowExecutionError):
        runner.run("station1", {}, RunContext.root(state.jobId, "station1", str(tmp_path)), CancellationToken())
    assert api.releaseCount == 1 and not calls
    assert state.get("station1-shot-count") == 0
    assert api.openCount == 1 and api.destroyCount == 1
