"""Real spawned workers; local fixtures only, no production devices or database writes."""
import base64
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import sqlite3
from threading import Event, Thread
import time
from uuid import uuid4

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.contracts import digest, trustedEntries
from tests.runtime.operator_debug_fixture import project
from tests.runtime.tcp_port_probe import assertTcpPortReleased
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


@contextmanager
def connected(runtime, operatorId):
    debug = DebugConnection(RuntimeClient(runtime))
    try:
        debug.open(project(operatorId), EditorKey("draft", "main", "node"), operatorId)
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        yield debug
    finally:
        debug.close()
    assert not runtime.operatorDebugManager.ownsResources()
    assert runtime.loadedDocument is None and not runtime.jobRepository.all()


def finished(debug, execution):
    def poll():
        record = debug.snapshot(execution["executionId"])["result"]
        return record if record["status"] != "RUNNING" else None
    return waitFor(poll)


def run(debug, params, inputs=None, **kwargs):
    return finished(debug, debug.execute(params, inputs or {}, **kwargs))


def uploadImage(debug):
    image = np.full((8, 12, 3), 120, np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return debug.upload(encoded.tobytes(), "image/png", {"kind": "file"})


def testReviewedPureAdaptersAdmittedAndManagedDevicesRequireDiagnostics(runtime):
    rows = json.loads(runtime.GetOperatorDebugCapabilities(pb.OperatorDebugRequest(), None).snapshot_json)["operators"]
    registry = runtime.pluginScanResult.activeOperators
    managed = {key for key, descriptor in registry.items() if descriptor.manifest.deviceRequirements}
    admitted = trustedEntries(registry)
    assert {row["operatorId"] for row in rows} == set(registry)
    assert {row["operatorId"] for row in rows if row["supported"]} == set(admitted) - managed
    assert all(row["reason"] == "use managed device diagnostics" for row in rows if row["operatorId"] in managed)
    flow = json.loads(runtime.GetWorkflowDebugCapabilities(pb.OperatorDebugRequest(), None).snapshot_json)["operators"]
    assert {row["operatorId"] for row in flow if row["supported"]} == {
        key for key, descriptor in admitted.items() if not descriptor.get("singleNodeOnly") and key not in managed}
    for operatorId in managed:
        assertPrivateDebugRefused(runtime, operatorId)


def testSelectValueReallyExecutes(runtime):
    with connected(runtime, "vision.flow.select_value") as debug:
        record = run(debug, {}, {"condition": {"inline": True}, "trueValue": {"inline": {"value": 7}}})
        assert record["status"] == "SUCCEEDED", record
        assert record["outputs"] == {"value": {"value": 7}}


def testCalculatorReallyExecutes(runtime):
    from emo_master.core.contracts.geometry2d import CoordinateSpace2D, Point2D
    space = CoordinateSpace2D(sourceId="fixture", imageWidth=20, imageHeight=20)
    points = [Point2D(x=2, y=3, coordinateSpace=space).toPayload()]
    with connected(runtime, "vision.geometry.coordinate_calculator") as debug:
        record = run(debug, {"mode": "transform", "offsetX": 4}, {"points": {"inline": points}})
        assert record["status"] == "SUCCEEDED", record
        assert record["outputs"]["points"][0]["x"] == 6


@pytest.mark.parametrize("mode", ["perInvocation", "session"])
def testCoordinatesFreezeAndReset(runtime, tmp_path, mode):
    path = tmp_path / "coordinates.csv"
    path.write_text("1,2\n3,4\n", encoding="utf-8")
    params = {"filePath": str(path), "readMode": mode, "sourceId": "fixture", "imageWidth": 20, "imageHeight": 20}
    with connected(runtime, "vision.io.coordinate_reader") as debug:
        first = run(debug, params)
        assert first["status"] == "SUCCEEDED", first
        path.write_text("5,6\n", encoding="utf-8")
        second = run(debug, params)
        assert second["status"] == "SUCCEEDED", second
        assert second["outputs"]["pointCount"] == (2 if mode == "session" else 1)
        debug.mutation("ResetOperatorDebugSession")
        waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
        third = run(debug, params)
        assert third["status"] == "SUCCEEDED", third
        assert third["outputs"]["pointCount"] == 1


def testBatchLoaderSequenceExhaustionResetAndParameterChange(runtime, tmp_path):
    for name in ("image10.png", "image2.png"):
        assert cv2.imwrite(str(tmp_path / name), np.zeros((4, 5, 3), np.uint8))
    with connected(runtime, "vision.io.image_batch_loader") as debug:
        params = {"folderPath": str(tmp_path)}
        first, second = run(debug, params), run(debug, params)
        assert first["status"] == second["status"] == "SUCCEEDED", (first, second)
        assert Path(first["outputs"]["imagePath"]).name == "image2.png"
        assert first["outputs"]["index"] == 0 and first["outputs"]["hasNext"]
        assert second["outputs"]["index"] == 1 and not second["outputs"]["hasNext"]
        assert run(debug, params)["code"] == "E_INPUT_EXHAUSTED"
        assert run(debug, params, {"reset": {"inline": True}})["outputs"]["index"] == 0
        assert run(debug, {**params, "colorMode": "grayscale"})["outputs"]["index"] == 0


def testVideoLoaderSequenceExhaustionResetAndParameterChange(runtime, tmp_path):
    from tests.plugins.test_video_loader_operator import makeVideo
    path = makeVideo(tmp_path / "video.avi", count=6)
    with connected(runtime, "vision.io.video_loader") as debug:
        params = {"videoPath": str(path), "intervalSec": .5}
        first, second = run(debug, params), run(debug, params)
        assert first["status"] == second["status"] == "SUCCEEDED", (first, second)
        assert first["outputs"]["frameIndex"] == 0 and first["outputs"]["hasNext"]
        assert second["outputs"]["frameIndex"] == 5 and not second["outputs"]["hasNext"]
        assert first["outputAssets"]["image"] and second["outputAssets"]["image"]
        assert run(debug, params)["code"] == "E_INPUT_EXHAUSTED"
        assert run(debug, params, {"reset": {"inline": True}})["outputs"]["index"] == 0
        assert run(debug, {**params, "intervalSec": .25})["outputs"]["index"] == 0


def testFileListDebugReturnsRuntimePaths(runtime, tmp_path):
    (tmp_path / "clip10.mp4").touch()
    (tmp_path / "clip2.mp4").touch()
    with connected(runtime, "vision.io.file_list") as debug:
        result = run(debug, {"folderPath": str(tmp_path), "patterns": ["*.mp4"]})
        assert result["status"] == "SUCCEEDED", result
        assert result["outputs"] == {"paths": [str(tmp_path / "clip2.mp4"), str(tmp_path / "clip10.mp4")], "count": 2}


def testVideoLoaderDebugUsesDynamicPath(runtime, tmp_path):
    from tests.plugins.test_video_loader_operator import makeVideo
    makeVideo(tmp_path / "01.avi", count=1)
    makeVideo(tmp_path / "02.mp4", count=1)
    with connected(runtime, "vision.io.video_loader") as debug:
        params = {"videoPath": "missing-old-draft.mp4", "intervalSec": .5}
        firstInput = {"videoPath": {"inline": str(tmp_path / "01.avi")}}
        secondInput = {"videoPath": {"inline": str(tmp_path / "02.mp4")}}
        first, second = run(debug, params, firstInput), run(debug, params, secondInput)
        assert first["status"] == second["status"] == "SUCCEEDED", (first, second)
        assert first["outputs"]["videoPath"] == firstInput["videoPath"]["inline"]
        assert second["outputs"]["videoPath"] == secondInput["videoPath"]["inline"]
        assert not first["outputs"]["hasNext"] and not second["outputs"]["hasNext"]
        assert first["outputAssets"]["image"] and second["outputAssets"]["image"]
        assert run(debug, params, secondInput)["code"] == "E_INPUT_EXHAUSTED"
        assert run(debug, params, {**secondInput, "reset": {"inline": True}})["outputs"]["index"] == 0
        assert run(debug, params, {"videoPath": {"inline": "relative.mp4"}})["code"] == "E_PARAM_INVALID"


@pytest.mark.parametrize("operatorId,key", [
    ("vision.io.coordinate_reader", "filePath"), ("vision.io.image_batch_loader", "folderPath"),
    ("vision.inference.yolo", "modelPath"),
    ("vision.io.video_loader", "videoPath"),
    ("vision.io.file_list", "folderPath"),
])
def testPathsMustExistOnRuntime(runtime, tmp_path, operatorId, key):
    with connected(runtime, operatorId) as debug:
        inputs = {"image": uploadImage(debug)} if key == "modelPath" else {}
        for path, code in (("relative.onnx", "E_PARAM_INVALID"), (str(tmp_path / "missing.onnx"), "E_INPUT_MISSING")):
            record = run(debug, {key: path}, inputs)
            assert record["status"] == "FAILED" and record["code"] == code, record
            assert "Runtime" in record["message"]


def testYoloUsesRealOnnxSession(runtime, tmp_path):
    from tests.plugins.test_yolo_onnx_backend import _TINY_YOLO_ONNX
    model = tmp_path / "tiny.onnx"
    model.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
    with connected(runtime, "vision.inference.yolo") as debug:
        record = run(debug, {"modelPath": str(model)}, {"image": uploadImage(debug)})
        assert record["status"] == "SUCCEEDED", record
        assert record["metrics"]["detectionCount"] == 1
        assert record["outputs"]["detections"]["items"][0]["label"] == "screw"


def testFileWriterConfirmationIsBoundToParamsAndPreparedInputs(runtime, tmp_path):
    output = tmp_path / "saved.png"
    with connected(runtime, "vision.io.image_saver") as debug:
        image = uploadImage(debug)
        params = {"outputPath": str(output), "overwrite": False}
        prepared = debug.mutation("PrepareOperatorDebugInputs", params_json=json.dumps(params),
            inputs={"image": pb.OperatorDebugValue(asset_ref=image["assetRef"])})
        for control in ({}, {"confirmedInputSetId": "old", "confirmedParamsDigest": digest(params)},
                        {"confirmedInputSetId": prepared["inputSetId"], "confirmedParamsDigest": digest({})}):
            reply = runtime.ExecuteOperatorDebugNode(pb.OperatorDebugRequest(**debug.identity,
                request_id=uuid4().hex, input_set_id=prepared["inputSetId"], params_json=json.dumps(params),
                control_json=json.dumps(control)), None)
            assert not reply.ok and reply.code == "E_DEBUG_CONFIRM_REQUIRED"
            assert not output.exists()
        record = run(debug, params, {"image": image}, confirmSideEffects=True)
        assert record["status"] == "SUCCEEDED" and output.is_file(), record
        assert np.all(cv2.imread(str(output)) == 120)
        assert run(debug, params, {"image": image}, confirmSideEffects=True)["code"] == "E_OUTPUT_EXISTS"


def testResultWriterIsIsolatedAndRejectsTraversal(runtime, tmp_path):
    with connected(runtime, "vision.io.result_writer") as debug:
        record = run(debug, {"relativePath": "results/step"}, {"numberValue": {"inline": 12}})
        assert record["status"] == "SUCCEEDED", record
        path = Path(record["outputs"]["result"]["path"])
        assert path.is_file() and "debugIsolation" in record["diagnostics"]
        bad = run(debug, {"relativePath": str(tmp_path / "escape.json")}, {"numberValue": {"inline": 1}})
        assert bad["status"] == "FAILED" and not (tmp_path / "escape.json").exists()
    assert not path.exists()


def sqliteConfig(path):
    return {"configVersion": 1, "databasePath": str(path), "table": "records", "mappings": [
        {"column": "value", "storageType": "INTEGER", "source": {"kind": "node_output", "nodeId": "upstream", "port": "value"}}]}


def testSqliteMappingsWriteOnlyToIsolatedDatabase(runtime, tmp_path):
    path = tmp_path / "production.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE records(value INTEGER NOT NULL)")
        db.execute("INSERT INTO records VALUES (999)")
    before = path.read_bytes()
    params = sqliteConfig(path)
    with connected(runtime, "vision.io.sqlite_writer") as debug:
        for value in (7, 8):
            record = run(debug, params, {"mapping_1": {"inline": value}})
            assert record["status"] == "SUCCEEDED", record
            assert record["outputs"]["receipt"]["status"] == "COMMITTED"
        isolated = Path(record["diagnostics"]["debugDatabasePath"])
        with closing(sqlite3.connect(isolated)) as db:
            assert db.execute("SELECT value FROM records").fetchall() == [(7,), (8,)]
        assert path.read_bytes() == before
        alias = run(debug, {**params, "debugDatabasePath": str(path)}, {"mapping_1": {"inline": 3}})
        assert alias["status"] == "FAILED" and alias["code"] == "E_SQLITE_TARGET", alias
        assert path.read_bytes() == before
    assert not isolated.exists()


def testComplexSqliteSchemaRequiresTemplateAndKeepsBothSourcesUnchanged(runtime, tmp_path):
    production, template = tmp_path / "business.sqlite3", tmp_path / "template.sqlite3"
    for path in (production, template):
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("CREATE TABLE records(value INTEGER NOT NULL); CREATE TABLE audit(value INTEGER); "
                             "CREATE TRIGGER audit_insert AFTER INSERT ON records BEGIN INSERT INTO audit VALUES(NEW.value); END;")
    original, testOriginal = production.read_bytes(), template.read_bytes()
    with connected(runtime, "vision.io.sqlite_writer") as debug:
        params = sqliteConfig(production)
        failed = run(debug, params, {"mapping_1": {"inline": 5}})
        assert failed["status"] == "FAILED" and failed["code"] == "E_SQLITE_DEBUG_SCHEMA", failed
        record = run(debug, {**params, "debugDatabasePath": str(template)}, {"mapping_1": {"inline": 5}})
        assert record["status"] == "SUCCEEDED", record
        with closing(sqlite3.connect(record["diagnostics"]["debugDatabasePath"])) as db:
            assert db.execute("SELECT value FROM audit").fetchall() == [(5,)]
    assert production.read_bytes() == original and template.read_bytes() == testOriginal


def assertPrivateDebugRefused(runtime, operatorId):
    request = pb.OperatorDebugRequest(runtime_instance_id=runtime.runtimeInstanceId, open_request_id=uuid4().hex,
        project_json=json.dumps(project(operatorId)), project_id="draft", workflow_id="main", node_id="node",
        operator_id=operatorId)
    reply = runtime.OpenOperatorDebugSession(request, None)
    assert not reply.ok and reply.code == "E_DEVICE_DEBUG_REQUIRED"
    assert not runtime.operatorDebugManager.ownsResources() and not runtime.operatorDebugManager.opens
    assert runtime.loadedDocument is None and not runtime.jobRepository.all()


def testManagedPlcWriteRequiresUnlockConfirmationAndDeduplicatesReceipt(runtime, tmp_path):
    from tests.runtime.plc_debug_server import PlcDebugServer
    from tests.runtime.test_managed_slmp import plcProject, openManagedDebug, debugCommand

    with PlcDebugServer() as peer:
        assertPrivateDebugRefused(runtime, "communication.plc.slmp_write")
        assert peer.connections == 0 and not peer.requests
        path = tmp_path / "plc.emoproj"
        path.write_text(json.dumps(plcProject(peer.port)), encoding="utf-8")
        before = path.read_bytes()
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        session = openManagedDebug(runtime)
        assert session.ok, session
        try:
            params = dict(device="D", startAddress=800, dataType="uint16", values=[123], lockGeneration=0)
            locked = debugCommand(runtime, session, "write", "locked", confirmed=True, **params)
            assert not locked.ok and locked.code == "E_PLC_WRITE_LOCKED" and not peer.requests
            assert debugCommand(runtime, session, "set_write_enabled", "unlock", enabled=True, lockGeneration=0).ok
            unconfirmed = debugCommand(runtime, session, "write", "unconfirmed", **params)
            assert not unconfirmed.ok and not peer.requests
            record = debugCommand(runtime, session, "write", "write-once", confirmed=True, **params)
            assert record.ok, record
            payload = json.loads(record.result_json)
            assert payload["receipt"]["outcome"] == "confirmed" and payload["readback"]["values"] == [123]
            assert peer.words[800] == 123
            assert [request.command for request in peer.requests] == [0x1401, 0x0401]
            fractional = debugCommand(runtime, session, "write", "fractional", confirmed=True,
                                      **dict(params, values=[1.5]))
            assert not fractional.ok and len(peer.requests) == 2
            replay = debugCommand(runtime, session, "write", "write-once", confirmed=True, **params)
            assert replay.result_json == record.result_json and len(peer.requests) == 2
            assert path.read_bytes() == before and not runtime.jobRepository.all()
        finally:
            closed = runtime.ClosePlcDebugSession(pb.ClosePlcDebugSessionRequest(
                session_id=session.session_id, runtime_instance_id=runtime.runtimeInstanceId), None)
            assert closed.ok and not runtime.deviceManager.maintenanceOwners


@contextmanager
def managedTcp(runtime, kind, port, **defaults):
    """Actual device authority and operator adapter, not a private debug worker."""
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    from emo_master.apps.runtime.devices.manager import DeviceManager
    from emo_master.core.project.models import ProjectDocument
    from emo_master.plugins.builtins._communication_operators import TcpClientOperator, TcpReceiveOnceOperator
    from tests.runtime.test_tcp_device_integration import definition

    operatorId = "communication.tcp." + ("client" if kind == "client_once" else "receive_once")
    assertPrivateDebugRefused(runtime, operatorId)
    declaration = runtime.pluginScanResult.deviceTypes["device.tcp." + kind]
    device = definition(declaration, port=port)
    device["defaults"].update(defaults)
    payload = WorkflowStore().toPayload()
    payload["devices"]["definitions"] = {"tcp": device}
    manager = DeviceManager(runtime.pluginScanResult.deviceTypes)
    manager.load(ProjectDocument.model_validate(payload))
    manager.authorize("test-owner", ["tcp"])
    stop = Event()
    parameters = manager.definitions["tcp"].defaults
    context = dict(deviceParameters={"tcp": parameters}, executeDevice=lambda slot, action, arguments:
        manager.execute("test-owner", "tcp", action, parameters, arguments=arguments,
                        cycleId="test-cycle", cancelled=stop.is_set))
    operator = TcpClientOperator() if kind == "client_once" else TcpReceiveOnceOperator()
    try:
        yield operator, context, manager, stop
    finally:
        stop.set()
        manager.retire("test-owner")
        manager.close()
        assert not manager.workers and not manager.drivers and not manager.frames.frames
        assert runtime.loadedDocument is None and not runtime.jobRepository.all()


def testManagedTcpSendExchangeUsesLoopbackOnly(runtime):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)
        received = []
        def serve():
            with listener.accept()[0] as peer:
                received.append(peer.recv(100))
                peer.sendall(b"ACK\n")
        thread = Thread(target=serve)
        thread.start()
        try:
            with managedTcp(runtime, "client_once", listener.getsockname()[1], appendNewline=True,
                            responseTextEncoding="utf-8") as (operator, context, manager, stop):
                record = operator.executeNode({}, {"operation": "exchange", "text": "HELLO"}, context)
                assert record["status"] == "ok", record
                assert record["outputs"]["responseText"] == "ACK"
                assert received == [b"HELLO\n"]
                assert not manager.drivers["tcp"].snapshot()["ownsResources"]
        finally:
            thread.join(5)
            assert not thread.is_alive()


def testManagedTcpReceiveAndAcknowledgeThenReleasePort(runtime):
    from tests.runtime.test_managed_tcp import freePort
    port = freePort()
    with managedTcp(runtime, "receive_once", port, ackText="ACK", messageTextEncoding="utf-8") as (
            operator, context, manager, stop), ThreadPoolExecutor(1) as pool:
        pending = pool.submit(operator.executeNode, {}, {}, context)
        deadline = time.monotonic() + 5
        while True:
            try:
                peer = socket.create_connection(("127.0.0.1", port), timeout=.2)
                break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(.02)
        with peer:
            peer.settimeout(3)
            peer.sendall(b"HELLO\n")
            assert peer.recv(100) == b"ACK\n"
            assert peer.recv(1) == b"", "Receive-once kept the accepted connection open"
        record = pending.result(5)
        assert record["status"] == "ok" and record["outputs"]["text"] == "HELLO", record
        assert not manager.drivers["tcp"].snapshot()["ownsResources"]
    assertTcpPortReleased(port)


def testCancelManagedTcpReceiveNeverClaimsSuccessAndReleasesPort(runtime):
    from tests.runtime.test_managed_tcp import freePort
    port = freePort()
    with managedTcp(runtime, "receive_once", port, acceptTimeoutMs=60000) as (
            operator, context, manager, stop), ThreadPoolExecutor(1) as pool:
        pending = pool.submit(operator.executeNode, {}, {}, context)
        waitFor(lambda: manager.drivers["tcp"].active)
        stop.set()
        record = pending.result(5)
        assert record["status"] == "error" and record["error"]["code"] == "E_DEVICE_CANCELLED", record
        manager.waitOwner("test-owner")
        assert not manager.drivers["tcp"].snapshot()["ownsResources"]
    assertTcpPortReleased(port)


def testManagedCameraLifecycleWithSimulatedSdkInActualHost(runtime):
    import os
    from emo_master.apps.runtime.devices.manager import DeviceManager
    from emo_master.apps.runtime.devices.frame_store import copyFrame
    from emo_master.core.project.models import ProjectDocument
    from tests.runtime.imv_test_support import useSimulatedImv
    from tests.runtime.test_managed_imv import cameraProject, FakeImv

    assertPrivateDebugRefused(runtime, "vision.io.huaray_camera")
    manager = DeviceManager(runtime.pluginScanResult.deviceTypes)
    useSimulatedImv(manager)
    manager.load(ProjectDocument.model_validate(cameraProject("freeRun")))
    driver = manager.drivers["camera"]
    driver.apiFactory = FakeImv
    assert driver.process is None
    manager.authorize("camera-test", ["camera"])
    try:
        params = manager.definitions["camera"].defaults
        first = manager.execute("camera-test", "camera", "capture", params, cycleId="first")
        process = driver.process
        second = manager.execute("camera-test", "camera", "capture", params, cycleId="second")
        assert copyFrame(first).shape == copyFrame(second).shape == (3, 4, 3)
        assert first["frameId"] != second["frameId"]
        assert first["acquisition"]["blockId"] + 1 == second["acquisition"]["blockId"]
        assert first["acquisition"]["connectionGeneration"] == second["acquisition"]["connectionGeneration"]
        assert driver.process is process and process.pid != os.getpid()
        assert driver.memory is None
        manager.retire("camera-test")
        assert not manager.frames.frames and not manager.permissions
    finally:
        manager.close()
    assert process._closed and driver.process is None and driver.memory is None
    assert runtime.loadedDocument is None and not runtime.jobRepository.all()
