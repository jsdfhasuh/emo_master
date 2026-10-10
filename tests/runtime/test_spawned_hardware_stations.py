"""Two actual spawned production Jobs; only SDK/ONNX/network peers simulated."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import socket
import struct
from threading import Event, Thread
from time import monotonic, sleep

import numpy as np

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.core.project.models import ProjectDocument
from tests.plugins.test_communication_operators import _recvExact, _slmpResponse
from tests.runtime.production_fixture import saveDocument, waitFor
from tests.runtime.test_hardware_trigger_normal_chain import syntheticFrame


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
    for folder, entry in (("huaray_camera", "TriggeredCamera"), ("yolo_inference", "BoundaryYolo")):
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
        next(n for n in payload["workflows"][f"station{s}"]["nodes"] if n["nodeId"] == "camera")["params"]["captureTimeoutMs"] = 1
        # Additional test observation, still captured from the real workflow output.
        payload["presentation"]["dataSources"][f"station{s}-sample"] = dict(kind="workflow_output",
            resultScopeId=f"station{s}", workflowId=f"station{s}", port="plcSample", expectedType="collection",
            callPath=payload['presentation']['resultScopes'][f'station{s}']['callPath'])
        payload["presentation"]["pages"][f"station{s}"]["components"].append(dict(componentId=f"station{s}-sample",
            type="table", layout={"row": 3}, bindings={"rows": f"station{s}-sample"}))
    with ExitStack() as stack:
        peers = {(s, channel): stack.enter_context(Peer(channel, s)) for s in (1, 2) for channel in ("slmp", "plc", "robot")}
        for s in (1, 2):
            for wid, flow in payload["workflows"].items():
                if not wid.startswith(f"station{s}"):
                    continue
                for node in flow["nodes"]:
                    if node.get("operatorId") == "communication.plc.slmp_read":
                        node["params"]["port"] = peers[s, "slmp"].port
                    if node.get("nodeId") == "exchange":
                        node["params"]["port"] = peers[s, "robot" if wid.endswith("second") else "plc"].port
        document = ProjectDocument.model_validate(payload)
        saveDocument(root, document)
        owner = ProductionRuntime(tmp_path / "data", pluginRootPaths=boundaryRegistry(tmp_path))
        try:
            owner.load(root)
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
            assert len(peers[2, "slmp"].requests) == 4
            assert len(peers[2, "plc"].requests) == len(peers[2, "robot"].requests) == 2
            waitFor(lambda: len([r for r in owner.presentation.store.history if r.identity.jobId == second]) >= 4)
            results = sorted((r for r in owner.presentation.store.history if r.identity.jobId == second), key=lambda r: r.identity.resultOrdinal)
            values = [{s.sourceId: json.loads(s.valueJson) for s in r.sources if s.valueJson is not None} for r in results]
            assert [v["station2-shot"] for v in values] == [1, 2, 1, 2]
            assert [v["station2-count"] for v in values] == [1, 0, 1, 0]
            assert [v["station2-sample"]["values"] for v in values] == [[201], [202], [203], [204]]
            assert all(all(s.sourceId.startswith("station2-") for s in r.sources) for r in results)
            # Repeated stops/restarts while the other camera remains live/owned.
            for received in (0, 0):
                before = monotonic()
                owner.stop("station1-run")
                assert monotonic() - before < 3
                assert not owner.runtime.jobSupervisor.ownsJobResources(first)
                assert (cameras / "fixture-camera-1/destroyed").read_text() == str(received)
                first = owner.start("station1-run")
                assert owner.sessions["station2-run"].jobId == second and len(owner.presentation.jobs) == 2
            sendFrame(cameras, 1, 1)
            sendFrame(cameras, 1, 2, second=True)
            waitFor(lambda: len(completed(owner, first)) == 2 or owner.runtime.jobRepository.get(first).isTerminal)
            assert owner.runtime.jobRepository.get(first).status == "RUNNING"
            assert len(peers[1, "slmp"].requests) == 2
            assert len(peers[1, "plc"].requests) == len(peers[1, "robot"].requests) == 1
            assert all(row["ok"] for row in owner.stopAll().values())
            assert (cameras / "fixture-camera-1/destroyed").read_text() == "2"
            assert (cameras / "fixture-camera-2/destroyed").read_text() == "4"
        finally:
            owner.close()
