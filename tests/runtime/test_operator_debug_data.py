import hashlib
import json
import struct
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.operator_debug.assets import DebugAssets, VALUE_BYTES, decode, imageBytes
from emo_master.apps.runtime.operator_debug.contracts import DebugError
from emo_master.plugins.builtins._image_frame import defaultFrame
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import openRequest, ready, request, runtime as runtime
from tests.runtime.test_operator_debug_sessions import waitFor


def upload(runtime, identity, value, provenance=None):
    raw = imageBytes(value) if isinstance(value, np.ndarray) else json.dumps(value).encode()
    reply = runtime.WriteOperatorDebugAsset(request(runtime, **identity, request_id=uuid4().hex,
        content=raw, total_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        mime_type="image/png" if isinstance(value, np.ndarray) else "application/json",
        provenance_json=json.dumps(provenance or {"kind": "upload"})), None)
    assert reply.ok, reply.message
    return json.loads(reply.snapshot_json)["asset"]["assetId"]


@pytest.mark.parametrize("operatorId", ["vision.state.variable_read", "vision.state.variable_write"])
def testVariableIdentityBindingRejectedLikeFormalCompiler(runtime, operatorId):
    payload = project(operatorId)
    payload["globalVariables"] = {"v": {"name": "Target", "type": "string", "kind": "variable",
        "lifetime": "persistent", "initialValue": "target"}}
    payload["workflows"]["main"]["nodes"][0]["globalVariableBindings"] = [
        {"variableId": "v", "parameterPath": ["variableId"]}]
    reply = runtime.OpenOperatorDebugSession(openRequest(runtime, project_json=json.dumps(payload), operator_id=operatorId), None)
    assert not reply.ok, reply
    assert not runtime.operatorDebugManager.ownsResources()


def execute(runtime, identity, params, values):
    prepared = runtime.PrepareOperatorDebugInputs(request(runtime, **identity, request_id=uuid4().hex,
        params_json=json.dumps(params), inputs=values), None)
    assert prepared.ok, prepared.message
    started = runtime.ExecuteOperatorDebugNode(request(runtime, **identity, request_id=uuid4().hex,
        input_set_id=prepared.input_set_id, params_json=json.dumps(params)), None)
    assert started.ok, started.message
    def terminal():
        reply = runtime.GetOperatorDebugExecution(request(runtime, **identity, execution_id=started.execution_id), None)
        assert reply.ok, reply.message
        record = json.loads(reply.snapshot_json)
        return record if record["status"] != "RUNNING" else None
    return waitFor(terminal)


def testImageAssetsMultiInputOutputAndReuse(runtime):
    identity = ready(runtime, operatorId="vision.image.absdiff")
    first = np.full((16, 24, 3), 35, dtype=np.uint8)
    second = np.full_like(first, 11)
    a, b = upload(runtime, identity, first), upload(runtime, identity, second)
    record = execute(runtime, identity, {}, {"imageA": pb.OperatorDebugValue(asset_ref=a),
                                            "imageB": pb.OperatorDebugValue(asset_ref=b)})
    assert record["status"] == "SUCCEEDED", record
    assert "frame" in record["outputs"] and "image" in record["outputAssets"]
    asset = record["outputAssets"]["image"]
    reply = runtime.ReadOperatorDebugAsset(request(runtime, **identity, asset_id=asset["assetId"]), None)
    assert reply.ok
    image, _ = decode(reply.content, "image/png")
    np.testing.assert_array_equal(image, cv2.absdiff(first, second))
    reused = execute(runtime, identity, {}, {
        "imageA": pb.OperatorDebugValue(output_ref=pb.OperatorDebugOutputRef(execution_id=record["executionId"], port="image")),
        "frameA": pb.OperatorDebugValue(output_ref=pb.OperatorDebugOutputRef(execution_id=record["executionId"], port="frame")),
        "imageB": pb.OperatorDebugValue(asset_ref=b)})
    assert reused["status"] == "SUCCEEDED", reused
    assert reused["inputSources"]["frameA"]["captureId"] == record["executionId"]
    assert runtime.loadedDocument is None and runtime.jobRepository.all() == []


def testFrameProvenanceMismatchRejectedBeforeExecution(runtime):
    identity = ready(runtime, operatorId="vision.preprocess.blur")
    image = upload(runtime, identity, np.zeros((10, 12), np.uint8), {"kind": "history", "captureId": "first"})
    frame = upload(runtime, identity, defaultFrame(12, 10).toPayload(), {"kind": "history", "captureId": "other"})
    reply = runtime.PrepareOperatorDebugInputs(request(runtime, **identity, request_id="bad",
        inputs={"image": pb.OperatorDebugValue(asset_ref=image), "frame": pb.OperatorDebugValue(asset_ref=frame)}), None)
    assert not reply.ok and reply.code == "E_INPUT_ORIGIN"
    assert not runtime.operatorDebugManager.sessions[identity["session_id"]].executions


@pytest.mark.parametrize("imagePort,framePort", [("mask", "frame"), ("maskA", "frameA"), ("maskB", "frameB")])
def testMaskCompanionsRejectOtherCapture(tmp_path, imagePort, framePort):
    from emo_master.apps.runtime.operator_debug.data import prepareInputs
    assets = DebugAssets(tmp_path)
    mask = assets.put(np.zeros((10, 12), np.uint8), {"captureId": "first"})
    frame = assets.put(defaultFrame(12, 10).toPayload(), {"captureId": "second"})
    with pytest.raises(DebugError) as error:
        prepareInputs({imagePort: {"assetRef": mask["assetId"]}, framePort: {"assetRef": frame["assetId"]}},
                      {imagePort: "image", framePort: "bbox2d"}, assets, {})
    assert error.value.code == "E_INPUT_ORIGIN"


def testFrozenInputIsIndependentOfCallerAndRepeatedMaterialization(tmp_path):
    from emo_master.apps.runtime.operator_debug.data import materialize, prepareInputs
    assets = DebugAssets(tmp_path)
    value = np.full((8, 8), 12, np.uint8)
    entry = assets.put(value, {"kind": "upload"})
    frozen, _ = prepareInputs({"image": {"assetRef": entry["assetId"]}}, {"image": "image"}, assets, {})
    value[:] = 99
    first = materialize(frozen, assets)
    first["image"][:] = 0
    np.testing.assert_array_equal(materialize(frozen, assets)["image"], np.full((8, 8), 12, np.uint8))


def testAssetOwnershipExpiryAndPathRejection(tmp_path):
    firstRoot, secondRoot = tmp_path / "first", tmp_path / "second"
    firstRoot.mkdir()
    secondRoot.mkdir()
    first, second = DebugAssets(firstRoot), DebugAssets(secondRoot)
    entry = first.put({"items": [1, 2]}, {"kind": "upload"})
    for key in (entry["assetId"], "../first/debug_assets/" + entry["assetId"], "C:/production.db"):
        with pytest.raises(DebugError) as error:
            second.value(key)
        assert error.value.code == "E_DEBUG_RESULT_EXPIRED"


@pytest.mark.parametrize("shape,dtype", [((4, 4, 4), np.uint8), ((4, 4), np.uint16), ((4, 4), np.float32)])
def testUnsupportedImageTypesAreNotSilentlyConverted(shape, dtype):
    with pytest.raises(DebugError, match="uint8"):
        imageBytes(np.zeros(shape, dtype))


def testOversizedPngIsRejectedBeforeDecoding():
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
    header += struct.pack(">IIBBBBB", VALUE_BYTES, 2, 8, 2, 0, 0, 0) + b"\0"*4
    with pytest.raises(DebugError) as error:
        decode(header, "image/png")
    assert error.value.code == "E_DEBUG_LIMIT"


def testChunkOffsetsHashValidationAndBudget(tmp_path):
    assets = DebugAssets(tmp_path, budget=200)
    raw = json.dumps({"value": "x"*30}).encode()
    args = dict(total=len(raw), mime="application/json", sha256=hashlib.sha256(raw).hexdigest(), provenance={})
    accepted = assets.upload(assetId="", offset=0, data=raw[:10], **args)
    with pytest.raises(DebugError, match="offset"):
        assets.upload(assetId=accepted["assetId"], offset=0, data=raw[10:], **args)
    result = assets.upload(assetId=accepted["assetId"], offset=10, data=raw[10:], **args)
    assert result["complete"] and assets.value(result["assetId"]) == {"value": "x"*30}
    with pytest.raises(DebugError, match="budget"):
        assets.put({"value": "x"*90}, {})
    args["sha256"] = "0"*64
    with pytest.raises(DebugError, match="digest"):
        assets.upload(assetId="", offset=0, data=raw, **args)
    assert not assets.pending and not list(assets.root.glob("*.part"))


def openVariables(runtime, operatorId, *, bindings=None):
    payload = project(operatorId)
    payload["schemaVersion"] = "2.4"
    payload["globalVariables"] = {"v": {"name": "Value", "type": "integer", "kind": "variable",
        "initialValue": 7, "lifetime": "persistent"}}
    payload["workflows"]["main"]["nodes"][0]["globalVariableBindings"] = bindings or []
    reply = runtime.OpenOperatorDebugSession(openRequest(runtime, operatorId, project_json=json.dumps(payload)), None)
    assert reply.ok, reply.message
    identity = dict(session_id=reply.session_id, generation=reply.generation)
    waitFor(lambda: runtime.GetOperatorDebugSession(request(runtime, **identity), None).state == "READY")
    return identity, payload


def testParameterBindingsUseFrozenIsolatedValues(runtime):
    identity, _ = openVariables(runtime, "vision.value.number", bindings=[{"parameterPath": ["value"], "variableId": "v"}])
    result = execute(runtime, identity, {"value": 99}, {})
    assert result["outputs"]["value"] == 7
    assert result["rawParams"]["value"] == 99 and result["effectiveParams"]["value"] == 7
    assert result["bindingSources"] == {"v": 7}


def testVariableStateCopyWriteAndResetNeverMutateProduction(runtime):
    identity, payload = openVariables(runtime, "vision.state.variable_write")
    production = ProjectGlobalVariables(runtime.sqliteStore, "draft", payload["globalVariables"])
    production.synchronize()
    production.set("v", 20)
    runtime.loadedProjectId = "draft"
    copy = runtime.CopyOperatorDebugVariables(request(runtime, **identity, request_id="copy"), None)
    assert copy.ok, copy.message
    values = {"after": pb.OperatorDebugValue(inline_json="false")}
    params = {"variableId": "v", "operation": "increment", "delta": 1}
    first = execute(runtime, identity, params, values)
    second = execute(runtime, identity, params, values)
    assert first["outputs"] == {"value": 21} and second["outputs"] == {"value": 22}
    assert production.get("v") == 20
    reset = runtime.ResetOperatorDebugSession(request(runtime, **identity, request_id="reset"), None)
    assert reset.ok
    waitFor(lambda: runtime.GetOperatorDebugSession(request(runtime, **identity), None).generation == 2)
    identity["generation"] = 2
    assert execute(runtime, identity, params, values)["outputs"] == {"value": 8}
    assert production.get("v") == 20


def testCounterIsSessionLocalAndResets(runtime):
    identity = ready(runtime, operatorId="vision.state.counter")
    values = {"increment": pb.OperatorDebugValue(inline_json="true")}
    assert execute(runtime, identity, {"name": "debug-count"}, values)["outputs"] == {"count": 1}
    assert execute(runtime, identity, {"name": "debug-count"}, values)["outputs"] == {"count": 2}
    assert runtime.sqliteStore.listGlobalCounters("draft") == []


def testAssetRequestReplayCannotAppendAgain(runtime):
    identity = ready(runtime)
    raw = b'{"value":1}'
    command = request(runtime, **identity, request_id="upload", content=raw, total_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), mime_type="application/json")
    first = runtime.WriteOperatorDebugAsset(command, None)
    assert first.ok
    assert runtime.WriteOperatorDebugAsset(command, None) == first
    command.content = b'{"value":2}'
    assert runtime.WriteOperatorDebugAsset(command, None).code == "E_DEBUG_REQUEST_CONFLICT"


def testHistoricalImportCopiesCompleteDataAndPreservesInvocation(runtime, tmp_path):
    from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
    from emo_master.apps.runtime.workflow.context import RunContext
    root = tmp_path / "history"
    writer = PreviewSnapshotWriter(root, jobId="job-history", projectRevision=3)
    image = np.full((8, 12), 17, np.uint8)
    context = RunContext.root("job-history", "main", str(root), projectId="draft").forNode("camera").forIteration(4)
    writer.capture(SimpleNamespace(nodeId="camera", outputPorts={"image": "image", "frame": "bbox2d"}),
                   {"image": image, "frame": defaultFrame(12, 8).toPayload()}, context)
    runtime.loadedProjectId = "draft"
    runtime._loadedProjectPreviewKey = "owned-history"
    runtime.previewAssetStore.promote(writer.root, "owned-history")
    identity = ready(runtime, operatorId="vision.preprocess.blur")
    listed = runtime.ListOperatorDebugSources(request(runtime, **identity), None)
    assert listed.ok, listed.message
    sources = json.loads(listed.snapshot_json)["sources"]
    assert len(sources) == 2
    imageSource = next(source for source in sources if source["port"] == "image")
    assert imageSource["companionId"] == next(source["sourceId"] for source in sources if source["port"] == "frame")
    imported = {}
    for source in sources:
        assert source["jobId"] == "job-history" and source["iterationPath"] == [4]
        response = runtime.ImportOperatorDebugSource(request(runtime, **identity,
            request_id=uuid4().hex, asset_id=source["sourceId"]), None)
        assert response.ok, response.message
        imported[source["port"]] = pb.OperatorDebugValue(asset_ref=json.loads(response.snapshot_json)["asset"]["assetId"])
        runtime.previewAssetStore._assets[source["sourceId"]].path.unlink()
    assert execute(runtime, identity, {}, imported)["status"] == "SUCCEEDED"
    expired = runtime.ImportOperatorDebugSource(request(runtime, **identity,
        request_id=uuid4().hex, asset_id=sources[0]["sourceId"]), None)
    assert not expired.ok and expired.code == "E_DEBUG_RESULT_EXPIRED"


def testFailedOutputFreezeRetiresPartialAssets(tmp_path):
    from emo_master.apps.runtime.operator_debug.assets import freezeOutputs
    store = DebugAssets(tmp_path, budget=2000)
    with pytest.raises(DebugError):
        try:
            freezeOutputs({"image": np.zeros((8, 8), np.uint8), "invalid": object()}, store, {})
        except Exception:
            store.discard()
            raise
    assert store.used == 0 and not list(store.root.iterdir())


def testFailedUploadCleanupKeepsQuotaUntilRetirement(tmp_path, monkeypatch):
    from pathlib import Path
    assets = DebugAssets(tmp_path)
    raw = b'{"value":1}'
    def denied(*args, **kwargs):
        raise PermissionError("locked asset")
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(DebugError) as error:
        assets.upload(assetId="", offset=0, total=len(raw), data=raw, mime="application/json",
                      sha256="0"*64, provenance={})
    assert error.value.code == "E_RESOURCE_CLEANUP_FAILED"
    assert assets.used == len(raw) and assets.pending


def testFailedOutputWriteCleanupKeepsQuota(tmp_path, monkeypatch):
    from pathlib import Path
    assets = DebugAssets(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("locked asset")
    monkeypatch.setattr(Path, "write_bytes", denied)
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(DebugError) as error:
        assets.put({"value": 1}, {})
    assert error.value.code == "E_RESOURCE_CLEANUP_FAILED" and assets.used > 0
