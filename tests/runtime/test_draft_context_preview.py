"""Fresh Runtime pure preview: no LoadProject/StartJob and no cross-window assets."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

import cv2
import grpc
import numpy as np
import pytest

from emo_master.apps.designer.operator_editors import EditorContext, EditorContextError, EditorKey
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from tests.runtime.operator_debug_fixture import project as minimalProject
from tests.runtime.test_operator_debug_rpc import runtime as runtime


def project(operator, version="2.1"):
    payload = minimalProject(operator)
    payload["project"].update(name="未保存的预览草稿", revision=1,
        createdAt="2026-10-10T00:00:00Z", updatedAt="2026-10-10T00:00:00Z")
    payload.update(entryWorkflowId="main", workflowOrder=["main"])
    payload["workflows"]["main"]["name"] = "Main"
    payload["schemaVersion"] = version
    if version in {"2.2", "2.3", "2.4"}:
        payload.update(presentation={}, resources={})
    if version in {"2.3", "2.4"}:
        payload["production"] = {}
    return payload


def context(client, payload, operator):
    return EditorContext(key=EditorKey("draft", "main", "node"), operatorId=operator, version="1",
        previewMode="pure", paramSchema={}, runtimeClient=client, applyParams=lambda *_: pytest.fail("Apply"),
        appendLog=lambda *_: None, getPreviewProject=lambda _: payload, variableDefinitions=lambda: {})


def png():
    image = np.arange(24 * 16, dtype=np.uint8).reshape(16, 24)
    ok, raw = cv2.imencode(".png", image)
    assert ok
    return raw.tobytes()


@pytest.mark.parametrize("version", ["2.1", "2.2", "2.3", "2.4"])
@pytest.mark.parametrize("operator,params", [
    ("vision.preprocess.roi", {"roiType": "bbox", "x": 2.0, "y": 3.0, "width": 8.0, "height": 7.0}),
    ("vision.analysis.histogram", {"bins": 16, "normalization": "counts"})])
def testFreshDraftPurePreviewUploadExecuteAndDownloadWithoutFormalProject(runtime, version, operator, params):
    payload = project(operator, version)
    before = deepcopy(payload)
    editor = context(RuntimeClient(runtime), payload, operator)
    try:
        asset = editor.uploadPreviewImage(png(), "本地.png")
        assert editor.downloadPreviewAsset(asset)[1] == "image/png"
        reply = editor.runPurePreview(params, asset, uuid4().hex)
        assert reply.ok, (reply.code, reply.message)
        outputs = json.loads(reply.outputs_json)
        if operator == "vision.preprocess.roi":
            assert reply.assets
            cropped = next(a for a in reply.assets if a.port == "croppedImage")
            content, mime = editor.downloadPreviewAsset(cropped.asset_id)
            assert mime == "image/png"
            decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)
            assert decoded.shape == (7, 8)
        else:
            assert "histogram" in outputs
        assert payload == before
        assert runtime.loadedDocument is None and not runtime.jobRepository.all()
    finally:
        editor.closePurePreview()
    assert not runtime.previewAssetStore._assets
    assert not list(runtime.previewAssetStore.transientRoot.iterdir())


def testSameIdCopyAndDifferentProjectCannotReuseAnotherWindowImage(runtime):
    payload = project("vision.preprocess.roi")
    first = context(RuntimeClient(runtime), payload, "vision.preprocess.roi")
    second = context(RuntimeClient(runtime), deepcopy(payload), "vision.preprocess.roi")
    try:
        asset = first.uploadPreviewImage(png())
        second.prepareLocalPreview()
        with pytest.raises(EditorContextError, match="当前预览窗口"):
            second.downloadPreviewAsset(asset)
        request = pb.RunOperatorPreviewRequest(project_id="draft", workflow_id="main", node_id="node",
            operator_id="vision.preprocess.roi", params_json="{}", image_asset_id=asset,
            project_json=json.dumps(payload), draft_session_id=second._pureDraftSessionId)
        reply = runtime.RunOperatorPreview(request, None)
        assert not reply.ok and reply.code == "E_PREVIEW_SOURCE_NOT_FOUND"
        assert list(runtime.StreamPreviewAsset(pb.GetPreviewAssetRequest(project_id="draft", asset_id=asset,
            draft_session_id=request.draft_session_id), None)) == []
        request.project_id = "other"
        reply = runtime.RunOperatorPreview(request, None)
        assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"
        assert first.downloadPreviewAsset(asset)[0]
    finally:
        first.closePurePreview()
        second.closePurePreview()


def testDraftDoesNotReplaceStaleLoadedProjectAndUsesUnappliedParams(runtime):
    from emo_master.core.project.models import ProjectDocument
    stale = project("vision.preprocess.roi")
    stale["workflows"]["main"]["nodes"][0]["params"] = {"width": 4}
    runtime.loadedDocument = ProjectDocument.model_validate(stale)
    runtime.loadedProjectId = "draft"
    old = runtime.loadedDocument
    payload = deepcopy(stale)
    payload["workflows"]["main"]["nodes"][0]["params"] = {"width": 6}
    editor = context(RuntimeClient(runtime), payload, "vision.preprocess.roi")
    try:
        asset = editor.uploadPreviewImage(png())
        reply = editor.runPurePreview({"roiType": "bbox", "x": 1, "y": 1, "width": 9, "height": 5}, asset)
        assert reply.ok, reply.message
        assert next(a for a in reply.assets if a.port == "croppedImage").width == 9
        assert payload["workflows"]["main"]["nodes"][0]["params"] == {"width": 6}
        assert runtime.loadedDocument is old and old.workflows["main"].nodes[0].params == {"width": 4}
        assert not runtime.jobRepository.all()
    finally:
        editor.closePurePreview()


@pytest.mark.parametrize("transport", ["sync", "aio"])
def testDraftRpcOverRealGrpcHasNoLegacyFallback(runtime, tmp_path, transport):
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.presentation.service import PresentationService
    pool = None
    if transport == "sync":
        pool = ThreadPoolExecutor(max_workers=4)
        server = grpc.server(pool)
        rpc.add_RuntimeServiceServicer_to_server(runtime, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
    else:
        server = AioRuntimeServer(runtime, PresentationService(runtime, tmp_path / "presentation"))
        port = server.port
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    editor = context(RuntimeClient(rpc.RuntimeServiceStub(channel)), project("vision.analysis.histogram"), "vision.analysis.histogram")
    try:
        asset = editor.uploadPreviewImage(png())
        assert editor.runPurePreview({"bins": 8}, asset).ok
        assert editor.downloadPreviewAsset(asset)[0]
        editor.closePurePreview()
        assert not runtime.previewAssetStore._assets
    finally:
        channel.close()
        if transport == "sync":
            server.stop(0).wait(3)
            pool.shutdown(wait=True)
        else:
            server.close()


def testDraftMethodsRejectMissingOrMismatchedIdentityBeforeAssetCreation(runtime):
    reply = runtime.UploadPreviewImage(iter([pb.PreviewUploadChunk(project_id="draft", content=png(),
        draft_session_id="missing-session")]), None)
    assert not reply.ok and not runtime.previewAssetStore._assets
    reply = runtime.RunOperatorPreview(pb.RunOperatorPreviewRequest(draft_session_id="missing-session"), None)
    assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"


def testReleaseStillWorksAfterInvalidDraftAndRuntimeReconnection(runtime):
    payload = project("vision.preprocess.roi")
    client = RuntimeClient(runtime)
    editor = context(client, payload, "vision.preprocess.roi")
    asset = editor.uploadPreviewImage(png())
    payload["workflows"]["main"]["nodes"].clear()
    with pytest.raises(EditorContextError):
        editor.uploadPreviewImage(png())
    assert runtime.previewAssetStore._assets[asset]
    client.runtimeService = object()
    with pytest.raises(EditorContextError, match="Runtime 连接已改变"):
        editor.downloadPreviewAsset(asset)
    editor.closePurePreview()
    assert not runtime.previewAssetStore._assets


def testPreparedTaskCannotRunAgainstReconnectedRuntimeAndCleanupUsesOriginal(runtime):
    client = RuntimeClient(runtime)
    editor = context(client, project("vision.analysis.histogram"), "vision.analysis.histogram")
    asset = editor.uploadPreviewImage(png())
    task = editor.preparePurePreview({"bins": 8}, asset)
    originalSession = editor._pureDraftSessionId
    client.runtimeService = object()
    try:
        with ThreadPoolExecutor(1) as pool:
            with pytest.raises(EditorContextError, match="Runtime 连接已改变"):
                pool.submit(task).result()
        assert set(runtime.previewAssetStore._assets) == {asset}
        assert originalSession in runtime.pureDraftPreviewSessions.sessions
    finally:
        editor.closePurePreview()
    assert runtime.pureDraftPreviewSessions.sessions == {}
    assert runtime.previewAssetStore._assets == {}
    assert list(runtime.previewAssetStore.transientRoot.iterdir()) == []


def testBackgroundCleanupFailureNeverCallsQtOwnedLogHook(runtime, monkeypatch, caplog):
    editor = context(RuntimeClient(runtime), project("vision.analysis.histogram"), "vision.analysis.histogram")
    editor.uploadPreviewImage(png())
    called = []
    editor._appendLog = lambda *_args: called.append("Qt log hook")
    def failedClose(_session):
        raise RuntimeError("injected cleanup transport failure")
    monkeypatch.setattr(editor._pureRuntimeClient, "closeDraftPurePreviewSession", failedClose)
    with ThreadPoolExecutor(1) as pool:
        pool.submit(editor.closePurePreview).result()
    assert called == []
    assert "injected cleanup transport failure" in caplog.text


def testOldRuntimeRejectsNewDraftRpcInsteadOfCallingLegacy(runtime):
    class OldRuntime(rpc.RuntimeServiceServicer):
        def UploadPreviewImage(self, *_):
            pytest.fail("A draft must never fall back to a legacy loaded project")
    pool = ThreadPoolExecutor(max_workers=2)
    server = grpc.server(pool)
    rpc.add_RuntimeServiceServicer_to_server(OldRuntime(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            from emo_master.apps.designer.services.runtime_client import RuntimeClientError
            editor = context(RuntimeClient(rpc.RuntimeServiceStub(channel)),
                project("vision.preprocess.roi"), "vision.preprocess.roi")
            with pytest.raises(RuntimeClientError, match="更新并重启 Runtime") as caught:
                editor.uploadPreviewImage(png())
            assert caught.value.code == "E_PREVIEW_UNSUPPORTED"
    finally:
        server.stop(0).wait(3)
        pool.shutdown(wait=True)
