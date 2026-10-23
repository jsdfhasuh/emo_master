"""Boundary simulation of actual operators, not old-algorithm equivalence.

Known external substitutes: IMV SDK, SLMP loopback server, ONNX inference backend,
gateway loopback servers. No fabricated final points or driver-side business logic.
"""
from contextlib import ExitStack, contextmanager
from functools import partial
from multiprocessing.shared_memory import SharedMemory
import json
from pathlib import Path
import socket
import struct
from threading import Thread, Event
from time import monotonic, sleep

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
from emo_master.core.project.runtime_package import trustedDeviceTypes
from emo_master.core.contracts.device_driver import DeviceError
from emo_master.plugins.builtins.yolo_inference import operator as yolo
from emo_master.plugins.builtins.yolo_inference.onnx_backend import OnnxInferenceResult
from emo_master.plugins.builtins._huaray_imv import IMV_TIMEOUT
from tests.plugins.test_huaray_camera_operator import _FakeImvApi
from tests.plugins.test_communication_operators import _slmpResponse
from tests.runtime.plc_debug_server import PlcDebugServer
from tests.runtime.imv_test_support import useSimulatedImv
from tests.runtime.test_dual_plc_field_project import managedDevices


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
    def __init__(self, frames, audit):
        super().__init__()
        self.audit = Path(audit)
        self.frames = list(frames)
        self.originals = [i.copy() for i in frames]
        self.ints.update(Width=160, Height=120, OffsetX=0, OffsetY=0)
        self._publish()

    def _publish(self):
        names = ("openCount", "releaseCount", "stopCount", "closeCount", "destroyCount", "commands")
        temporary = self.audit / "state.tmp"
        temporary.write_text(json.dumps({name: getattr(self, name) for name in names}), encoding="utf-8")
        temporary.replace(self.audit / "state.json")

    def open(self, handle):
        result = super().open(handle)
        self._publish()
        return result

    def release_frame(self, handle, frame):
        result = super().release_frame(handle, frame)
        self._publish()
        return result

    def stop_grabbing(self, handle):
        result = super().stop_grabbing(handle)
        self._publish()
        return result

    def close(self, handle):
        result = super().close(handle)
        self._publish()
        return result

    def destroy_handle(self, handle):
        result = super().destroy_handle(handle)
        self._publish()
        return result

    def execute_command(self, handle, name):
        result = super().execute_command(handle, name)
        self._publish()
        return result

    def get_frame(self, handle, timeoutMs):
        (self.audit / "waiting").touch()
        if not self.frames:
            sleep(timeoutMs / 1000)
            return IMV_TIMEOUT, None
        self.current = self.frames.pop(0)
        return super().get_frame(handle, timeoutMs)

    def convert_frame(self, handle, frame, outputColor, demosaic):
        return self.current.copy()


class SdkAudit:
    """Read actual child-SDK observations without sharing a native camera object."""
    def __init__(self, root, frames):
        self.root = root
        self.root.mkdir()
        self.originals = [frame.copy() for frame in frames]
        self.waited = self

    def wait(self, timeout):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            if (self.root / "waiting").exists():
                return True
            sleep(.01)
        return False

    def __getattr__(self, name):
        state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        return state[name]

    @property
    def frames(self):
        return self.originals[self.releaseCount:]


@contextmanager
def hardwareDevices(payload, state, api, cancellation=None):
    with managedDevices(payload, state.jobId, prepare=useSimulatedImv,
                        cancellation=cancellation) as (manager, devices, communicationProxy):
        api.manager = manager
        api.drivers = [driver for key, driver in manager.drivers.items() if key.endswith("-camera")]
        for driver in api.drivers:
            driver.apiFactory = partial(FifoSdk, api.originals, str(api.root))
        yield devices, communicationProxy


def compiler(operators):
    return WorkflowCompiler(operators, deviceTypes=trustedDeviceTypes())


class LocalGateway:
    def __init__(self, channel, count=2, delay=.03):
        self.channel, self.count, self.delay = channel, count, delay
        self.requests, self.errors = [], []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(count)
        self.server.settimeout(10)
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
    api = SdkAudit(tmp_path / "sdk-observations", frames)
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
        plc = stack.enter_context(PlcDebugServer([_slmpResponse(struct.pack("<H", 101 + n)) for n in range(4)]))
        plcGateway = stack.enter_context(LocalGateway("plc"))
        robotGateway = stack.enter_context(LocalGateway("robot"))
        payload["devices"]["definitions"][root + "-plc"]["connection"]["port"] = plc.port
        for kind, gateway in (("plc", plcGateway), ("robot", robotGateway)):
            payload["devices"]["definitions"][root + "-" + kind + "-gateway"]["connection"]["port"] = gateway.port
        devices, communicationProxy = stack.enter_context(hardwareDevices(payload, state, api))
        events = []
        runner = ObservedRunner(compiler(operators).compile(payload), operators, globalVariables=state,
                                retainOperators=True, eventPublisher=lambda **event: events.append(event),
                                devices=devices, communication=communicationProxy)
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
        # Closing the owner, not the node, retires the managed SDK host.
        stack.close()
        assert [(i[0], i[1]) for i in observed] == [(1, 1), (2, 0), (1, 1), (2, 0)]
        assert [i[2] for i in observed] == [41, 42, 43, 44]
        assert [i[3] for i in observed] == [[101], [102], [103], [104]]
        assert len(plc.requests) == 4 and plc.connections == 1
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
    assert payload["devices"]["definitions"]["station1-camera"]["defaults"]["captureTimeoutMs"] == 0
    sampled = []
    def forbidden(*args):
        sampled.append(args)
        pytest.fail("a waiting camera must not open the PLC connection")
    monkeypatch.setattr("emo_master.plugins.builtins._slmp.Slmp3ESession.open", forbidden)
    token = CancellationToken()
    cancelled = Event()
    errors = []
    with hardwareDevices(payload, state, api, cancelled) as (devices, communicationProxy):
        runner = WorkflowRunner(compiler(operators).compile(payload), operators, globalVariables=state,
                                retainOperators=True, devices=devices, communication=communicationProxy)
        def run():
            try:
                runner.run("station1", {}, RunContext.root(state.jobId, "station1", str(tmp_path)), token)
            except Exception as exc:
                errors.append(exc)
        thread = Thread(target=run)
        thread.start()
        try:
            assert api.waited.wait(10)
            memoryNames = [driver.memory.name for driver in api.drivers if driver.memory is not None]
            assert len(memoryNames) == 1
            sleep(.13)
            assert thread.is_alive()  # unlimited hardware wait, not a failed Job/retry
            assert not sampled and not calls and state.get("station1-shot-count") == 0
        finally:
            token.cancel()
            cancelled.set()
            thread.join(5)
            runner.closeSession(RunContext.root(state.jobId, "station1"))
    assert not thread.is_alive() and len(errors) == 1
    # A cancelled native/IPC capture may lose its receipt. Preserve the managed
    # failure's uncertainty rather than claiming no trigger occurred.
    assert isinstance(errors[0], (CancellationRequested, DeviceError, WorkflowExecutionError))
    assert getattr(errors[0], "code", "E_CANCELLED") in {"E_CANCELLED", "E_DEVICE_CANCELLED", "E_DEVICE_OUTCOME_UNKNOWN"}
    if getattr(errors[0], "code", "") == "E_DEVICE_OUTCOME_UNKNOWN":
        assert errors[0].outcome == "UNKNOWN"
    # An interrupted native call retires the process and mappings, not a
    # fabricated SDK-close receipt. Successful calls retain callback checks above.
    assert api.releaseCount == 0
    assert all(driver.process is None and driver.memory is None for driver in api.drivers)
    for name in memoryNames:
        with pytest.raises(FileNotFoundError):
            SharedMemory(name=name)
    assert not api.manager.workers and not api.manager.permissions
    assert not api.manager.frames.frames and not api.manager.frames.reservations


def testPlcFailureAfterExposureStopsBeforeIncrementAndVision(tmp_path, monkeypatch):
    payload, operators, api, calls, state = setup(tmp_path, monkeypatch, [syntheticFrame()])
    with PlcDebugServer([_slmpResponse(endCode=0xC051)]) as plc:
        payload["devices"]["definitions"]["station1-plc"]["connection"]["port"] = plc.port
        with hardwareDevices(payload, state, api) as (devices, communicationProxy):
            runner = WorkflowRunner(compiler(operators).compile(payload), operators, globalVariables=state,
                                    devices=devices, communication=communicationProxy)
            with pytest.raises(WorkflowExecutionError) as failure:
                runner.run("station1", {}, RunContext.root(state.jobId, "station1", str(tmp_path)), CancellationToken())
            assert failure.value.code == "E_DEVICE_SLMP_RESPONSE"
            assert len(plc.requests) == 1
    assert api.releaseCount == 1 and not calls
    assert state.get("station1-shot-count") == 0
    assert api.openCount == 1 and api.destroyCount == 1
