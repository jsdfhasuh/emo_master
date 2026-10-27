"""Two actual spawned production Jobs; only SDK/ONNX/network peers simulated."""
from contextlib import ExitStack
import json
import os
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
import shutil
import socket
import struct
from threading import Event, Thread
from time import monotonic, sleep

import numpy as np
import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import revisionOf
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.plugins.test_communication_operators import _recvExact, _slmpResponse
from tests.runtime.production_fixture import saveDocument, waitFor
from tests.runtime.test_hardware_trigger_normal_chain import syntheticFrame
from tests.runtime.imv_test_support import useSimulatedImv
from tests.runtime.plc_debug_server import PlcDebugServer
from tests.runtime.station_camera_boundary import FileTriggeredSdk


ROOT = Path(__file__).resolve().parents[2]


class Peer:
    def __init__(self, channel, station):
        self.channel, self.station = channel, station
        self.requests, self.errors = [], []
        self.stop = Event()
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.server.settimeout(.1)
        self.port = self.server.getsockname()[1]
        self.thread = Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, kind, *_):
        self.stop.set()
        self.thread.join(2)
        self.server.close()
        assert not self.thread.is_alive()
        if kind is None:
            assert not self.errors, self.errors

    def _run(self):
        try:
            while not self.stop.is_set():
                try:
                    client, _ = self.server.accept()
                except socket.timeout:
                    continue
                with client:
                    client.settimeout(1)
                    if self.channel == "slmp":
                        header = _recvExact(client, 9)
                        data = header + _recvExact(client, struct.unpack_from("<H", header, 7)[0])
                        reply = _slmpResponse(struct.pack("<H", self.station * 100 + len(self.requests) + 1))
                    else:
                        data = b""
                        while data.count(b'"') < 2:
                            data += _recvExact(client, 1)
                        assert data[:1] == data[-1:] == b'"' and b"\n" not in data
                        x, y = data[1:-1].decode("ascii").split(",")
                        reply = (f"OK D7={2 if int(x) < 0 else 1} D6={abs(int(x))}" if self.channel == "plc"
                                 else f"OK '{x},{y}EY'").encode("ascii") + b"\n"
                    self.requests.append(data)
                    client.sendall(reply[:3])
                    sleep(.005)
                    client.sendall(reply[3:])
        except Exception as error:
            self.errors.append(error)


def boundaryRegistry(tmp_path):
    target = tmp_path / "plugins"
    shutil.copytree(ROOT / "src/emo_master/plugins/builtins", target, ignore=shutil.ignore_patterns("__pycache__"))
    for folder, entry in (("yolo_inference", "BoundaryYolo"),):
        path = target / folder / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["entry"] = "tests.runtime.station_camera_boundary:" + entry
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return (str(target),)


def sendFrame(root, station, sequence, *, second=False):
    folder = root / f"fixture-camera-{station}"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "new-frame.npy"
    np.save(temporary, syntheticFrame(second))
    os.replace(temporary, folder / f"frame-{sequence}.npy")


def completed(owner, job):
    return [event for event in owner.runtime.eventStore.read(job) if event.eventType == "workflow.completed"
            and event.workflowId in {"station1", "station2"}]


def assertRetiredCamera(owner, station, job, memoryName=None):
    manager = owner.runtime.deviceManager
    driver = manager.drivers[f"station{station}-camera"]
    assert driver.process is None and driver.memory is None
    if memoryName is not None:
        with pytest.raises(FileNotFoundError):
            SharedMemory(name=memoryName)
    assert job not in manager.workers and job not in manager.permissions
    assert all(job not in frame.owners for frame in manager.frames.frames.values())
    assert all(reservation[0] != job for reservation in manager.frames.reservations.values())


def testNoTriggerStationDoesNotBlockOtherActualHardwareNormalJob(tmp_path, monkeypatch):
    cameras = tmp_path / "simulated-sdk"
    monkeypatch.setenv("EMO_M5_SIMULATED_CAMERA_ROOT", str(cameras))
    root = tmp_path / "engineering"
    root.mkdir()
    payload = json.loads((ROOT / "examples/hardware_trigger_normal_fixture/project.json").read_text(encoding="utf-8"))
    for role in ("a", "b", "holes"):
        (root / f"{role}.onnx").write_text("M5 simulated ONNX:" + role, encoding="ascii")
    for s in (1, 2):
        shutil.copyfile(ROOT / f"examples/hardware_trigger_normal_fixture/station{s}_reference.txt", root / f"station{s}_reference.txt")
        assert payload["devices"]["definitions"][f"station{s}-camera"]["defaults"]["captureTimeoutMs"] == 0
        # Additional test observation, still captured from the real workflow output.
        payload["presentation"]["dataSources"][f"station{s}-sample"] = dict(kind="workflow_output",
            resultScopeId=f"station{s}", workflowId=f"station{s}", port="plcSample", expectedType="collection",
            callPath=payload['presentation']['resultScopes'][f'station{s}']['callPath'])
        payload["presentation"]["pages"][f"station{s}"]["components"].append(dict(componentId=f"station{s}-sample",
            type="table", layout={"row": 3}, bindings={"rows": f"station{s}-sample"}))
    with ExitStack() as stack:
        peers = {(s, channel): stack.enter_context(Peer(channel, s)) for s in (1, 2) for channel in ("plc", "robot")}
        for s in (1, 2):
            peers[s, "slmp"] = stack.enter_context(PlcDebugServer([
                _slmpResponse(struct.pack("<H", s * 100 + n)) for n in range(1, 5)]))
            for channel, suffix in (("slmp", "plc"), ("plc", "plc-gateway"), ("robot", "robot-gateway")):
                payload["devices"]["definitions"][f"station{s}-{suffix}"]["connection"]["port"] = peers[s, channel].port
        document = ProjectDocument.model_validate(payload)
        saveDocument(root, document)
        owner = ProductionRuntime(tmp_path / "data", pluginRootPaths=boundaryRegistry(tmp_path))
        try:
            # Opt only this manager into software IMV. Production declarations
            # remain Windows-only and the workflow uses the real camera operator.
            useSimulatedImv(owner.runtime.deviceManager)
            owner.load(root)
            for s in (1, 2):
                owner.runtime.deviceManager.drivers[f"station{s}-camera"].apiFactory = FileTriggeredSdk
            assert not owner.runtime.jobRepository.all()
            started = owner.startAll(["station1-run", "station2-run"])
            assert all(row["ok"] for row in started.values()), started
            first, second = (owner.sessions[key].jobId for key in ("station1-run", "station2-run"))
            waitFor(lambda: all((cameras / f"fixture-camera-{s}" / "waiting").exists() for s in (1, 2)))
            for i in range(1, 5):
                sendFrame(cameras, 2, i, second=i % 2 == 0)
            # Hundreds of durable node events now backpressure the worker.
            # This tests functional four-frame equivalence, not a 20s takt:
            # retain every value/count/ACK assertion and the <3s stop bound.
            waitFor(lambda: len(completed(owner, second)) == 4 or owner.runtime.jobRepository.get(second).isTerminal,
                    seconds=60)
            record = owner.runtime.jobRepository.get(second)
            assert record.status == "RUNNING", (record.errorCode, record.message)
            assert not completed(owner, first) and not peers[1, "slmp"].requests
            assert len(peers[2, "slmp"].requests) == 4 and peers[2, "slmp"].connections == 1
            assert len(peers[2, "plc"].requests) == len(peers[2, "robot"].requests) == 2
            waitFor(lambda: len([r for r in owner.presentation.store.history if r.identity.jobId == second]) >= 4)
            results = sorted((r for r in owner.presentation.store.history if r.identity.jobId == second), key=lambda r: r.identity.resultOrdinal)
            values = [{s.sourceId: json.loads(s.valueJson) for s in r.sources if s.valueJson is not None} for r in results]
            assert [v["station2-shot"] for v in values] == [1, 2, 1, 2]
            assert [v["station2-count"] for v in values] == [1, 0, 1, 0]
            assert [v["station2-sample"]["values"] for v in values] == [[201], [202], [203], [204]]
            assert all(all(s.sourceId.startswith("station2-") for s in r.sources) for r in results)
            # Cancellation of an in-flight managed capture has an unknown
            # native outcome. Restart requires explicit recovery, never replay.
            manager = owner.runtime.deviceManager
            other = manager.drivers["station2-camera"]
            otherGeneration, otherPid = other.generation, other.process.pid
            for _ in range(2):
                driver = manager.drivers["station1-camera"]
                memoryName = driver.memory.name
                before = monotonic()
                owner.stop("station1-run")
                assert monotonic() - before < 3
                assert not owner.runtime.jobSupervisor.ownsJobResources(first)
                assertRetiredCamera(owner, 1, first, memoryName)
                stopped = owner.runtime.jobRepository.get(first)
                assert (stopped.status, stopped.errorCode) in {
                    ("ABORTED", "E_CANCELLED"), ("FAILED", "E_DEVICE_CANCELLED"),
                    ("FAILED", "E_DEVICE_OUTCOME_UNKNOWN")}, stopped
                if stopped.errorCode == "E_DEVICE_OUTCOME_UNKNOWN":
                    failures = [json.loads(event.payloadJson) for event in owner.runtime.eventStore.read(first)
                                if event.eventType == "node.failed" and event.workflowId == "station1"
                                and event.nodeId == "camera"]
                    assert failures[-1]["diagnostics"]["deviceOutcome"]["outcome"] == "UNKNOWN"
                assert manager.executors["station1-camera"].quarantined
                (cameras / "fixture-camera-1/waiting").unlink()
                rejected = owner.start("station1-run")
                waitFor(lambda: owner.runtime.jobRepository.get(rejected).isTerminal)
                failed = owner.runtime.jobRepository.get(rejected)
                assert (failed.status, failed.errorCode) == ("FAILED", "E_DEVICE_UNAVAILABLE")
                assert not completed(owner, rejected) and not peers[1, "slmp"].requests
                assert not (cameras / "fixture-camera-1/waiting").exists()
                waitFor(lambda: not owner.runtime.jobSupervisor.ownsJobResources(rejected))
                assertRetiredCamera(owner, 1, rejected)

                # The test models a deliberate operator recovery after external
                # triggers are stopped. Recovery does not reconnect or restart.
                generation = manager.executors["station1-camera"].generation
                request = pb.RecoverDeviceRequest(project_id=owner.runtime.loadedProjectId,
                    runtime_instance_id=owner.runtime.runtimeInstanceId, device_id="station1-camera",
                    expected_generation=generation,
                    expected_definition_revision=revisionOf(manager.definitions["station1-camera"].model_dump()))
                unconfirmed = owner.runtime.RecoverDevice(request, None)
                assert not unconfirmed.ok and "synchronized" in unconfirmed.message
                assert manager.executors["station1-camera"].generation == generation
                request.hardware_synchronized = True
                recovered = owner.runtime.RecoverDevice(request, None)
                assert recovered.ok, recovered.message
                replacement = manager.drivers["station1-camera"]
                replacement.apiFactory = FileTriggeredSdk
                assert manager.executors["station1-camera"].generation != generation
                assert replacement.process is None and replacement.memory is None
                assert not manager.executors["station1-camera"].quarantined
                assert owner.sessions["station1-run"].jobId == rejected
                assert not peers[1, "slmp"].requests
                assert not (cameras / "fixture-camera-1/waiting").exists()
                first = owner.start("station1-run")
                waitFor(lambda: (cameras / "fixture-camera-1/waiting").exists()
                        or owner.runtime.jobRepository.get(first).isTerminal)
                record = owner.runtime.jobRepository.get(first)
                assert record.status == "RUNNING", (record.errorCode, record.message)
                assert owner.sessions["station2-run"].jobId == second and len(owner.presentation.jobs) == 2
                assert manager.drivers["station2-camera"] is other
                assert other.generation == otherGeneration and other.process.pid == otherPid
                assert other.process.is_alive() and len(completed(owner, second)) == 4
            sendFrame(cameras, 1, 1)
            sendFrame(cameras, 1, 2, second=True)
            waitFor(lambda: len(completed(owner, first)) == 2 or owner.runtime.jobRepository.get(first).isTerminal)
            assert owner.runtime.jobRepository.get(first).status == "RUNNING"
            assert len(peers[1, "slmp"].requests) == 2 and peers[1, "slmp"].connections == 1
            assert len(peers[1, "plc"].requests) == len(peers[1, "robot"].requests) == 1
            # A completed frame releases its transfer memory. Wait until both
            # next captures are armed, then verify those live buffers are unlinked.
            waitFor(lambda: all(manager.drivers[f"station{s}-camera"].memory is not None for s in (1, 2)))
            memories = {s: manager.drivers[f"station{s}-camera"].memory.name for s in (1, 2)}
            assert all(row["ok"] for row in owner.stopAll().values())
            assertRetiredCamera(owner, 1, first, memories[1])
            assertRetiredCamera(owner, 2, second, memories[2])
            assert not list(cameras.glob("fixture-camera-*/frame-*.npy"))
        finally:
            owner.close()
