"""Runtime-owned image files through gRPC and a real isolated debug worker."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from uuid import uuid4

import cv2
import grpc
import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorKey
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.operator_debug.assets import VALUE_BYTES
from emo_master.apps.runtime.operator_debug.contracts import DebugError
from emo_master.apps.runtime.operator_debug.image_read import validateRuntimeImagePath
from emo_master.core.contracts.geometry2d import BBox2D
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor
from tests.runtime.test_workflow_debug_control import nestedProject


OPERATOR = "vision.io.image_loader"


@pytest.fixture
def connection(runtime):
    with ThreadPoolExecutor(max_workers=4) as pool:
        server = grpc.server(pool)
        rpc.add_RuntimeServiceServicer_to_server(runtime, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        debug = DebugConnection(RuntimeClient(rpc.RuntimeServiceStub(channel)))
        try:
            payload = project(OPERATOR)
            before = deepcopy(payload)
            debug.open(payload, EditorKey("draft", "main", "node"), OPERATOR)
            waitFor(lambda: debug.snapshot()["session"]["state"] == "READY")
            assert payload == before
            yield debug
        finally:
            debug.close()
            channel.close()
            server.stop(0).wait(3)
        assert runtime.loadedDocument is None and not runtime.jobRepository.all()
        assert not runtime.operatorDebugManager.ownsResources()


def read(connection, path, mode="color"):
    execution = connection.execute({"imagePath": str(path), "colorMode": mode}, {})
    def finished():
        record = connection.snapshot(execution["executionId"])["result"]
        return record if record["status"] != "RUNNING" else None
    return waitFor(finished)


@pytest.mark.parametrize("extension", ["png", "jpg", "bmp", "tiff"])
@pytest.mark.parametrize("mode", ["color", "grayscale"])
def testRuntimeFileProducesExactPixelsAndSourceFrame(connection, tmp_path, extension, mode):
    path = tmp_path / ("\u56fe\u50cf sample." + extension)
    image = np.zeros((24, 32, 3), np.uint8)
    image[:, :16] = (17, 45, 220)
    image[:, 16:] = (180, 75, 20)
    ok, encoded = cv2.imencode("." + extension, image)
    assert ok
    path.write_bytes(encoded.tobytes())
    expected = cv2.imdecode(encoded, cv2.IMREAD_COLOR if mode == "color" else cv2.IMREAD_GRAYSCALE)
    record = read(connection, path, mode)
    assert record["status"] == "SUCCEEDED", record
    asset = connection.download(record["outputAssets"]["image"]["assetId"])
    actual = cv2.imdecode(np.frombuffer(asset["content"], np.uint8), cv2.IMREAD_UNCHANGED)
    assert np.array_equal(actual, expected)
    frame = BBox2D.fromPayload(record["outputs"]["frame"])
    assert (frame.width, frame.height) == (32, 24)
    assert frame.coordinateSpace.sourceId == str(path.resolve())
    assert record["rawParams"] == {"imagePath": str(path), "colorMode": mode}
    assert path.read_bytes() == encoded.tobytes()
    assert record["inputSources"] == {}


@pytest.mark.parametrize("kind,code", [
    ("missing", "E_INPUT_MISSING"), ("directory", "E_INPUT_MISSING"),
    ("relative", "E_PARAM_INVALID"), ("corrupt", "E_INPUT_DECODE"),
    ("empty", "E_INPUT_DECODE"),
])
def testFileFailuresStayActionableAndNeverUpload(connection, tmp_path, monkeypatch, kind, code):
    path = tmp_path / "input.png"
    if kind == "directory":
        path.mkdir()
    elif kind == "relative":
        path = Path("input.png")
    elif kind in {"corrupt", "empty"}:
        path.write_bytes(b"not an image" if kind == "corrupt" else b"")
    def upload(*_args, **_kwargs):
        pytest.fail("Runtime file reads must not upload Designer-local files")
    monkeypatch.setattr(connection, "upload", upload)
    record = read(connection, path)
    assert record["status"] == "FAILED" and record["code"] == code, record
    assert not record.get("outputs") and not record.get("outputAssets")
    if kind in {"missing", "directory", "relative"}:
        assert "Runtime" in record["message"]
    assert str(path) in record["message"]
    assert connection.snapshot()["session"]["state"] == "READY"


def testEachStepReadsCurrentFileAndKeepsFrozenPreviousImage(connection, tmp_path):
    path = tmp_path / "changing.png"
    first = np.full((8, 12, 3), 40, np.uint8)
    second = np.full((10, 16, 3), 150, np.uint8)
    assert cv2.imwrite(str(path), first)
    old = read(connection, path)
    assert old["status"] == "SUCCEEDED"
    assert cv2.imwrite(str(path), second)
    new = read(connection, path, "grayscale")
    assert new["status"] == "SUCCEEDED"
    for record, expected in ((old, first), (new, cv2.cvtColor(second, cv2.COLOR_BGR2GRAY))):
        asset = connection.download(record["outputAssets"]["image"]["assetId"])
        actual = cv2.imdecode(np.frombuffer(asset["content"], np.uint8), cv2.IMREAD_UNCHANGED)
        assert np.array_equal(actual, expected)
    path.unlink()
    missing = read(connection, path)
    assert missing["status"] == "FAILED" and missing["code"] == "E_INPUT_MISSING"
    assert "Runtime" in missing["message"]


@pytest.mark.parametrize("path", ["", "image.png", "../image.png", "C:relative.png", "https://host/image.png", "bad\x00path"])
def testNoImplicitRuntimeOrDesignerWorkingDirectory(path):
    with pytest.raises(DebugError) as error:
        validateRuntimeImagePath({"imagePath": path})
    assert error.value.code == "E_PARAM_INVALID"


@pytest.mark.parametrize("failure,code", [(PermissionError("denied"), "E_INPUT_UNREADABLE"),
                                         (OSError("unavailable"), "E_INPUT_UNREADABLE")])
def testRuntimePermissionAndIoFailuresAreExplicit(tmp_path, monkeypatch, failure, code):
    path = tmp_path / "inaccessible.png"
    def denied(_path):
        raise failure
    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(DebugError) as error:
        validateRuntimeImagePath({"imagePath": str(path)})
    assert error.value.code == code and "Runtime" in str(error.value)


def testOversizedFileRejectedBeforeOpening(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "stat", lambda _path: SimpleNamespace(st_mode=stat.S_IFREG, st_size=VALUE_BYTES+1))
    monkeypatch.setattr(Path, "open", lambda *_args: pytest.fail("oversized file was opened"))
    with pytest.raises(DebugError) as error:
        validateRuntimeImagePath({"imagePath": str(tmp_path / "large.png")})
    assert error.value.code == "E_DEBUG_LIMIT"


def testImageReadDoesNotAdmitAutomaticWorkflowOrTrial(runtime):
    for method, supported in ((runtime.GetOperatorDebugCapabilities, True), (runtime.GetWorkflowDebugCapabilities, False)):
        reply = method(pb.OperatorDebugRequest(), None)
        assert reply.ok
        row = next(row for row in json.loads(reply.snapshot_json)["operators"] if row["operatorId"] == OPERATOR)
        assert row["supported"] is supported and row["singleNodeOnly"]
    payload = nestedProject()
    payload["workflows"]["child"]["nodes"][1].update(operatorId=OPERATOR, params={"imagePath": "never-read.png"})
    reply = runtime.OpenWorkflowDebugSession(pb.OperatorDebugRequest(
        runtime_instance_id=runtime.runtimeInstanceId, open_request_id=uuid4().hex,
        project_id=payload["project"]["projectId"], workflow_id="main", project_json=json.dumps(payload)), None)
    assert not reply.ok and reply.code == "E_DEBUG_UNSUPPORTED"
    assert not runtime.operatorDebugManager.ownsResources()
