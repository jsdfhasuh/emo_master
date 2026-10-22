"""Real spawned workers; local fixtures only, no production devices or database writes."""
import base64
from contextlib import closing, contextmanager
import json
from pathlib import Path
import socket
import sqlite3
from threading import Thread
import time
from uuid import uuid4

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.contracts import SINGLE_NODE_FOLDERS, digest
from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
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


def testAllBuiltinsAdmittedButExternalResourcesNotWorkflowEnabled(runtime):
    rows = json.loads(runtime.GetOperatorDebugCapabilities(pb.OperatorDebugRequest(), None).snapshot_json)["operators"]
    managed = {"communication.tcp.session", "communication.tcp.session_send", "vision.io.simulated_camera"}
    assert len(rows) == 71
    assert {row["operatorId"] for row in rows if not row["supported"]} == managed
    flow = json.loads(runtime.GetWorkflowDebugCapabilities(pb.OperatorDebugRequest(), None).snapshot_json)["operators"]
    assert sum(not row["supported"] for row in flow) == len(SINGLE_NODE_FOLDERS) + len(managed)
    assert all(row["supported"] != row["singleNodeOnly"] for row in flow if row["operatorId"] not in managed)


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


@pytest.mark.parametrize("operatorId,key", [
    ("vision.io.coordinate_reader", "filePath"), ("vision.io.image_batch_loader", "folderPath"),
    ("vision.inference.yolo", "modelPath"),
    ("vision.io.video_loader", "videoPath"),
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


def testPlcWriteRequiresOneShotUnlockAndUsesConfiguredValues(runtime):
    from tests.runtime.plc_debug_server import PlcDebugServer
    with PlcDebugServer() as peer, connected(runtime, "communication.plc.slmp_write") as debug:
        params = peer.params(device="D", startAddress=800, dataType="uint16", values=[123], retryCount=0)
        from emo_master.apps.designer.services.runtime_client import RuntimeClientError
        with pytest.raises(RuntimeClientError) as locked:
            debug.execute(params, {})
        assert locked.value.code == "E_DEBUG_CONFIRM_REQUIRED" and not debug.uncertain
        assert not peer.requests
        with pytest.raises(RuntimeClientError) as retry:
            debug.execute({**params, "retryCount": 1}, {}, confirmSideEffects=True)
        assert retry.value.code == "E_DEBUG_WRITE_RETRY_FORBIDDEN" and not peer.requests
        record = run(debug, params, confirmSideEffects=True)
        assert record["status"] == "SUCCEEDED", json.dumps(record, ensure_ascii=False)
        assert peer.words[800] == 123 and len(peer.requests) == 1
        fractional = run(debug, {**params, "values": [1.5]}, confirmSideEffects=True)
        assert fractional["status"] == "FAILED" and len(peer.requests) == 1
        replay = runtime.ExecuteOperatorDebugNode(pb.OperatorDebugRequest(**debug.identity,
            request_id=uuid4().hex, input_set_id=record["inputSetId"], params_json=json.dumps(params),
            control_json=json.dumps({"confirmedInputSetId": record["inputSetId"], "confirmedParamsDigest": digest(params)})), None)
        assert not replay.ok and replay.code == "E_DEBUG_RESULT_EXPIRED" and len(peer.requests) == 1


def testTcpSendExchangeUsesLoopbackOnly(runtime):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)
        received = []
        def serve():
            with listener.accept()[0] as peer:
                received.append(peer.recv(100))
                peer.sendall(b"ACK\n")
        thread = Thread(target=serve)
        thread.start()
        try:
            with connected(runtime, "communication.tcp.client") as debug:
                record = run(debug, {"host": "127.0.0.1", "port": listener.getsockname()[1], "operation": "exchange",
                    "text": "HELLO", "appendNewline": True, "responseTextEncoding": "utf-8"}, confirmSideEffects=True)
                assert record["status"] == "SUCCEEDED", record
                assert record["outputs"]["responseText"] == "ACK"
                assert received == [b"HELLO\n"]
        finally:
            thread.join(10)


def testTcpReceiveAndAcknowledgeThenReleasePort(runtime):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with connected(runtime, "communication.tcp.receive_once") as debug:
        execution = debug.execute({"bindHost": "127.0.0.1", "port": port, "ackText": "ACK",
            "messageTextEncoding": "utf-8"}, {}, confirmSideEffects=True)
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
        record = finished(debug, execution)
        assert record["status"] == "SUCCEEDED" and record["outputs"]["text"] == "HELLO", record
    assertTcpPortReleased(port)


def testCancelBlockedTcpReceiveNeverClaimsSuccessAndReleasesPort(runtime):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with connected(runtime, "communication.tcp.receive_once") as debug:
        execution = debug.execute({"bindHost": "127.0.0.1", "port": port, "acceptTimeoutMs": 60000}, {},
                                  confirmSideEffects=True)
        debug.mutation("CancelOperatorDebugExecution", execution_id=execution["executionId"])
        record = finished(debug, execution)
        assert record["status"] in {"CANCELLED", "UNKNOWN"}, record
    assertTcpPortReleased(port)


class SimulatedCamera(HuarayCameraOperator):
    """Only injected into a test manager, never into the production registry."""
    def __init__(self):
        from tests.plugins.test_huaray_camera_operator import _FakeImvApi
        self.fake = _FakeImvApi()
        super().__init__(lambda: self.fake)

    def executeNode(self, inputs, params, context):
        result = super().executeNode(inputs, params, context)
        if result.get("status") == "ok":
            result.setdefault("metrics", {})["simulatedOpenCount"] = self.fake.openCount
        return result

    def disposeOperator(self):
        super().disposeOperator()
        assert self.fake.openCount == self.fake.closeCount == self.fake.destroyCount


def testCameraLifecycleWithSimulatedSdkInActualWorker(runtime):
    manager = runtime.operatorDebugManager
    operatorId = "vision.io.huaray_camera"
    manager.admitted[operatorId] = {**manager.admitted[operatorId],
        "entry": "tests.runtime.test_operator_debug_resources:SimulatedCamera"}
    base = {"runtimeInstanceId": runtime.runtimeInstanceId}
    opened = manager.call("open", {**base, "openRequestId": uuid4().hex, "projectJson": json.dumps(project(operatorId)),
        "projectId": "draft", "workflowId": "main", "nodeId": "node", "operatorId": operatorId})
    identity = {**base, "sessionId": opened["sessionId"], "generation": opened["generation"]}
    waitFor(lambda: manager.call("get", identity)["state"] == "READY")
    params = {"selectionMode": "ip", "ipAddress": "127.0.0.1", "triggerMode": "freeRun", "retryCount": 0}
    for _ in range(2):
        prepared = manager.call("prepare", {**identity, "requestId": uuid4().hex, "paramsJson": json.dumps(params), "inputs": {}})
        execution = manager.call("execute", {**identity, "requestId": uuid4().hex, "inputSetId": prepared["inputSetId"],
            "paramsJson": json.dumps(params), "controlJson": json.dumps({"confirmedInputSetId": prepared["inputSetId"],
                                                                       "confirmedParamsDigest": digest(params)})})
        def result():
            record = manager.call("execution", {**identity, "executionId": execution["executionId"]})
            return record if record["status"] != "RUNNING" else None
        record = waitFor(result)
        assert record["status"] == "SUCCEEDED", record
        assert record["metrics"]["simulatedOpenCount"] == 1
        assert "image" in record["outputAssets"]
    manager.call("close", {**identity, "requestId": uuid4().hex})
    state = waitFor(lambda: (state if (state := manager.call("get", identity))["state"] in {"CLOSED", "FAULTED"} else None))
    assert state["state"] == "CLOSED", state
