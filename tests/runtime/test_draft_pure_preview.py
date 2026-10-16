"""Local-image pure preview owns a draft session, never a loaded Job/project."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace

import cv2
import grpc
import numpy as np
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.preview.pure_draft import DraftPreviewError


ROI = "vision.preprocess.roi"
HISTOGRAM = "vision.analysis.histogram"
PARAMS = {ROI: dict(roiType="bbox", x=2.0, y=3.0, width=8.0, height=7.0),
          HISTOGRAM: dict(bins=8, normalization="counts")}


def draft(version="2.4", operator=ROI):
    payload = dict(schemaVersion=version,
        project=dict(projectId="same-project", name="Unsaved draft", revision=1,
                     createdAt="2026-10-10T00:00:00Z", updatedAt="2026-10-10T00:00:00Z"),
        entryWorkflowId="main", workflowOrder=["main"],
        workflows={"main": dict(name="Main", nodes=[dict(nodeId="draft-node", operatorId=operator, params={}),
            dict(nodeId="unfinished", operatorId="vision.io.image_saver", params={})], edges=[])})
    if version in {"2.2", "2.3", "2.4"}:
        payload.update(presentation={}, resources={})
    if version in {"2.3", "2.4"}:
        payload["production"] = {}
    return payload


def imageBytes():
    ok, data = cv2.imencode(".png", np.full((20, 24, 3), 100, dtype=np.uint8))
    assert ok
    return data.tobytes()


def openSession(runtime, payload=None, **changes):
    payload = payload if payload is not None else draft()
    values = dict(project_id=payload["project"]["projectId"], workflow_id="main", node_id="draft-node",
                  operator_id=payload["workflows"]["main"]["nodes"][0]["operatorId"], project_json=json.dumps(payload))
    values.update(changes)
    return runtime.OpenDraftPurePreviewSession(pb.OpenOperatorPreviewSessionRequest(**values), None)


def upload(runtime, session, *, projectId="same-project"):
    return runtime.UploadPreviewImage(iter([pb.PreviewUploadChunk(upload_id="one", filename="sample.png",
        project_id=projectId, draft_session_id=session, content=imageBytes())]), None)


def execute(runtime, session, asset, payload=None, params=None, **changes):
    payload = payload if payload is not None else draft()
    operator = payload["workflows"]["main"]["nodes"][0]["operatorId"]
    values = dict(project_id=payload["project"]["projectId"], workflow_id="main", node_id="draft-node",
        operator_id=operator, image_asset_id=asset, params_json=json.dumps(params or PARAMS[operator]),
        project_json=json.dumps(payload), draft_session_id=session)
    values.update(changes)
    return runtime.RunOperatorPreview(pb.RunOperatorPreviewRequest(**values), None)


def download(runtime, session, asset, projectId="same-project"):
    return list(runtime.StreamPreviewAsset(pb.GetPreviewAssetRequest(
        project_id=projectId, asset_id=asset, draft_session_id=session), None))


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        yield service
    finally:
        service.close()


@pytest.mark.parametrize("version", ["2.1", "2.2", "2.3", "2.4"])
@pytest.mark.parametrize("operator", [ROI, HISTOGRAM])
def testFreshRuntimeUploadsExecutesDownloadsWithoutLoadingSavingOrJobs(runtime, version, operator):
    payload = draft(version, operator)
    before = deepcopy(payload)
    opened = openSession(runtime, payload)
    assert opened.ok, opened.message
    uploaded = upload(runtime, opened.session_id)
    assert uploaded.ok, uploaded.message
    assert download(runtime, opened.session_id, uploaded.asset_id)
    result = execute(runtime, opened.session_id, uploaded.asset_id, payload)
    assert result.ok, result.message
    if operator == ROI:
        cropped = next(asset for asset in result.assets if asset.port == "croppedImage")
        assert (cropped.width, cropped.height) == (8, 7)
        assert download(runtime, opened.session_id, cropped.asset_id)
    else:
        assert len(json.loads(result.outputs_json)["histogram"]["binEdges"]) == 9
    assert runtime.loadedDocument is None and runtime.loadedProjectId == ""
    assert runtime.jobRepository.all() == []
    assert payload == before
    assert runtime.CloseDraftPurePreviewSession(pb.CloseOperatorPreviewSessionRequest(session_id=opened.session_id), None).ok
    assert runtime.previewAssetStore.resolve(uploaded.asset_id) is None
    with pytest.raises(DraftPreviewError):
        download(runtime, opened.session_id, uploaded.asset_id)
    assert not execute(runtime, opened.session_id, uploaded.asset_id, payload).ok


def testTemporaryParametersReuseImageButAppliedDraftChangesInvalidateSession(runtime):
    session = openSession(runtime).session_id
    asset = upload(runtime, session).asset_id
    first = execute(runtime, session, asset)
    second = execute(runtime, session, asset, params=dict(PARAMS[ROI], width=5.0))
    assert first.ok and second.ok
    assert next(row.width for row in second.assets if row.port == "croppedImage") == 5
    assert all(runtime.previewAssetStore.resolve(row.asset_id) is None for row in first.assets)
    assert download(runtime, session, asset)
    payload = draft()
    payload["workflows"]["main"]["nodes"][0]["params"] = dict(PARAMS[ROI], width=5.0)
    changed = execute(runtime, session, asset, payload)
    assert not changed.ok and "草稿已变更" in changed.message
    assert runtime.previewAssetStore.resolve(asset) is None
    with pytest.raises(DraftPreviewError):
        download(runtime, session, asset)


@pytest.mark.parametrize("version", ["2.1", "2.4"])
def testIdenticalCopiesAndDifferentProjectsNeverShareAssets(runtime, version):
    payload = draft(version)
    first = openSession(runtime, payload).session_id
    copy = openSession(runtime, deepcopy(payload)).session_id
    assert first != copy
    asset = upload(runtime, first).asset_id
    assert download(runtime, copy, asset) == []
    assert not execute(runtime, copy, asset, payload).ok
    other = draft(version)
    other["project"]["projectId"] = "other-project"
    foreign = openSession(runtime, other).session_id
    assert not execute(runtime, foreign, asset, other).ok
    with pytest.raises(DraftPreviewError):
        download(runtime, first, asset, "other-project")
    # Dropping session identity cannot turn a draft asset into a loaded one.
    assert download(runtime, "", asset) == []
    assert not execute(runtime, "", asset, payload).ok


@pytest.mark.parametrize("field,value", [("project_id", "other"), ("workflow_id", "other"),
    ("node_id", "deleted"), ("operator_id", HISTOGRAM), ("project_json", "{}"), ("job_id", "old-job")])
def testRunRequiresExactSessionContext(runtime, field, value):
    session = openSession(runtime).session_id
    asset = upload(runtime, session).asset_id
    reply = execute(runtime, session, asset, **{field: value})
    assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"


@pytest.mark.parametrize("changes", [{"project_id": "wrong"}, {"node_id": "deleted"},
    {"operator_id": "vision.io.huaray_camera"}, {"project_json": "[]"}, {"project_json": "{"},
    {"project_json": "x" * (769 * 1024)}])
def testInvalidDraftNeverCreatesSessionOrAssets(runtime, changes):
    assert not openSession(runtime, **changes).ok
    assert runtime.pureDraftPreviewSessions.sessions == {}
    assert runtime.jobRepository.all() == []


def testBoundedSessionsAssetsAndExpiryReclaimPrivateImages(runtime):
    manager = runtime.pureDraftPreviewSessions
    now = [0.0]
    manager.clock = lambda: now[0]
    sessions = [openSession(runtime).session_id for _ in range(manager.maxSessions)]
    assert not openSession(runtime).ok
    session = sessions[0]
    manager.maxAssets = 1
    asset = upload(runtime, session).asset_id
    rejected = upload(runtime, session)
    assert not rejected.ok and "上限" in rejected.message
    assert len(runtime.previewAssetStore._assets) == 1
    now[0] = manager.ttlSeconds + 1
    assert not execute(runtime, session, asset).ok
    assert manager.sessions == {} and runtime.previewAssetStore.resolve(asset) is None
    assert openSession(runtime).ok


def testChunkIdentityCannotChangeOrDropSession(runtime):
    session = openSession(runtime).session_id
    data = imageBytes()
    for changed in [dict(draft_session_id=""), dict(project_id="other"), dict(upload_id="two")]:
        values = dict(project_id="same-project", draft_session_id=session, upload_id="one")
        second = dict(values, **changed)
        reply = runtime.UploadPreviewImage(iter([pb.PreviewUploadChunk(**values, content=data[:10]),
            pb.PreviewUploadChunk(**second, content=data[10:])]), None)
        assert not reply.ok
    assert runtime.previewAssetStore._assets == {}


def testCanceledOpenAndUploadRetainNothing(runtime):
    request = pb.OpenOperatorPreviewSessionRequest(project_id="same-project", workflow_id="main",
        node_id="draft-node", operator_id=ROI, project_json=json.dumps(draft()))
    cancelled = SimpleNamespace(is_active=lambda: False)
    assert not runtime.OpenDraftPurePreviewSession(request, cancelled).ok
    assert runtime.pureDraftPreviewSessions.sessions == {}
    session = openSession(runtime).session_id
    response = runtime.UploadPreviewImage(iter([pb.PreviewUploadChunk(project_id="same-project",
        draft_session_id=session, content=imageBytes())]), cancelled)
    assert not response.ok
    assert runtime.previewAssetStore._assets == {}


def testDraftVariablesUseCopyInitialValuesWithoutReadingOrSynchronizingDatabase(runtime, monkeypatch):
    payload = draft(operator=HISTOGRAM)
    payload["globalVariables"] = {"bins": dict(name="Bins", type="integer", initialValue=4, lifetime="persistent")}
    payload["workflows"]["main"]["nodes"][0]["globalVariableBindings"] = [dict(parameterPath=["bins"], variableId="bins")]
    session = openSession(runtime, payload).session_id
    asset = upload(runtime, session).asset_id
    with monkeypatch.context() as patch:
        patch.setattr(runtime.sqliteStore, "_connect", lambda: pytest.fail("draft preview must not read another copy's variable state"))
        response = execute(runtime, session, asset, payload)
    assert response.ok, response.message
    assert len(json.loads(response.outputs_json)["histogram"]["binEdges"]) == 5


@pytest.mark.parametrize("transport", ["sync", "aio"])
def testLifecycleOverRealGrpc(runtime, tmp_path, transport):
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.presentation.service import PresentationService
    pool = None
    if transport == "sync":
        pool = ThreadPoolExecutor(max_workers=2)
        server = grpc.server(pool)
        rpc.add_RuntimeServiceServicer_to_server(runtime, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
    else:
        server = AioRuntimeServer(runtime, PresentationService(runtime, tmp_path / "presentation"))
        port = server.port
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = RuntimeClient(runtimeService=rpc.RuntimeServiceStub(channel))
            opened = client.openDraftPurePreviewSession("same-project", "main", "draft-node", ROI, draft())
            assert opened.ok, opened.message
            uploaded = client.uploadPreviewImage(imageBytes(), "sample.png", "same-project", draftSessionId=opened.session_id)
            assert uploaded.ok, uploaded.message
            result = client.runOperatorPreview("same-project", "main", "draft-node", ROI, PARAMS[ROI],
                uploaded.asset_id, projectPayload=draft(), draftSessionId=opened.session_id)
            assert result.ok, result.message
            content, mime = client.downloadPreviewAsset(result.assets[0].asset_id, "same-project", draftSessionId=opened.session_id)
            assert content and mime == "image/png"
            assert client.closeDraftPurePreviewSession(opened.session_id).ok
            assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    finally:
        if transport == "sync":
            server.stop(0).wait()
            pool.shutdown(wait=True)
        else:
            server.close()


def testFailedUnlinkIsTrackedAndBlocksAllocationsUntilRecovery(runtime, monkeypatch):
    from pathlib import Path
    session = openSession(runtime).session_id
    asset = upload(runtime, session).asset_id
    stored = runtime.previewAssetStore.resolve(asset)
    original = Path.unlink

    def failOwned(path, *args, **kwargs):
        if path == stored.path:
            raise PermissionError("simulated Windows sharing violation")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", failOwned)
        runtime.CloseDraftPurePreviewSession(pb.CloseOperatorPreviewSessionRequest(session_id=session), None)
        assert runtime.previewAssetStore.resolve(asset) is None
        assert stored.path in runtime.previewAssetStore._pendingTransientCleanup
        replacement = openSession(runtime).session_id
        for _ in range(3):
            denied = upload(runtime, replacement)
            assert not denied.ok and "cleanup pending" in denied.message
        assert list(runtime.previewAssetStore.transientRoot.iterdir()) == [stored.path]
    assert upload(runtime, replacement).ok
    assert not stored.path.exists()
    assert runtime.previewAssetStore._pendingTransientCleanup == set()


def testDraftPreviewPreservesUnrelatedLoadedDocumentAndAssets(runtime):
    from emo_master.core.project.models import ProjectDocument
    loaded = ProjectDocument.model_validate(draft())
    runtime.loadedDocument = loaded
    runtime.loadedProjectId = loaded.project.projectId
    runtime._loadedProjectPreviewKey = "old-copy-key"
    oldAsset = runtime.previewAssetStore.addUploadedImage(imageBytes(), projectKey="old-copy-key")
    opened = openSession(runtime)
    assert opened.ok
    assert download(runtime, opened.session_id, oldAsset.assetId) == []
    assert not execute(runtime, opened.session_id, oldAsset.assetId).ok
    newAsset = upload(runtime, opened.session_id).asset_id
    assert execute(runtime, opened.session_id, newAsset).ok
    assert runtime.loadedDocument is loaded and runtime._loadedProjectPreviewKey == "old-copy-key"
    assert runtime.previewAssetStore.resolve(oldAsset.assetId) is oldAsset
    assert runtime.jobRepository.all() == []


def testRuntimeBusyAdmissionStillPrecedesDraftSession(runtime, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(runtime.operatorDebugManager, "ownsResources", lambda: True)
        response = openSession(runtime)
    assert not response.ok and response.code == "E_RESOURCE_BUSY"
    assert runtime.pureDraftPreviewSessions.sessions == {}


def testExpiredSessionRejectsUploadAndDownloadEvenWithUnchangedDraft(runtime):
    manager = runtime.pureDraftPreviewSessions
    now = [1.0]
    manager.clock = lambda: now[0]
    session = openSession(runtime).session_id
    asset = upload(runtime, session).asset_id
    now[0] += manager.ttlSeconds
    with pytest.raises(DraftPreviewError, match="会话已失效"):
        download(runtime, session, asset)
    assert not upload(runtime, session).ok
    assert not execute(runtime, session, asset).ok
    assert runtime.previewAssetStore.resolve(asset) is None


def testRuntimeReplacementRejectsOldSessionAndAsset(tmp_path):
    first = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        session = openSession(first).session_id
        asset = upload(first, session).asset_id
    finally:
        first.close()
    second = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not upload(second, session).ok
        assert not execute(second, session, asset).ok
        with pytest.raises(DraftPreviewError):
            download(second, session, asset)
        assert openSession(second).session_id != session
    finally:
        second.close()


def testByteBudgetRejectsAndReclaimsUnpublishedUpload(runtime):
    manager = runtime.pureDraftPreviewSessions
    session = openSession(runtime).session_id
    limit = manager.maxBytes
    manager.maxBytes = 1  # Exercise the byte-limit branch without allocating 128 MiB.
    reply = upload(runtime, session)
    assert not reply.ok and "上限" in reply.message
    assert runtime.previewAssetStore._assets == {}
    assert list(runtime.previewAssetStore.transientRoot.iterdir()) == []
    manager.maxBytes = limit
    assert upload(runtime, session).ok


def testRuntimeCloseRetriesOnlyProvenPendingTransientUnlink(runtime, monkeypatch):
    from pathlib import Path
    from emo_master.apps.runtime.preview.store import PreviewTransientCleanupPending
    session = openSession(runtime).session_id
    assetId = upload(runtime, session).asset_id
    asset = runtime.previewAssetStore.resolve(assetId)
    unlink = Path.unlink

    def lockedFile(path, *args, **kwargs):
        if path == asset.path:
            raise PermissionError("simulated Windows sharing violation")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", lockedFile)
        for _ in range(2):
            with pytest.raises(PreviewTransientCleanupPending):
                runtime.close()
            assert not runtime._closed and runtime._runtimeDataLock._acquired
            assert runtime._closeStages["preview-assets"] == "TRANSIENT_CLEANUP_PENDING"
            assert asset.path in runtime.previewAssetStore._pendingTransientCleanup
    runtime.close()
    assert runtime._closed and not runtime._runtimeDataLock._acquired
    assert not asset.path.exists()
    assert runtime._closeStages["preview-assets"] == "DONE"


def testKnownCleanupRetryDoesNotAuthorizeLaterUnknownDisposalRetry(runtime, monkeypatch):
    from pathlib import Path
    from emo_master.apps.runtime.preview.store import PreviewTransientCleanupPending
    session = openSession(runtime).session_id
    asset = runtime.previewAssetStore.resolve(upload(runtime, session).asset_id)
    unlink = Path.unlink

    def lockedFile(path, *args, **kwargs):
        if path == asset.path:
            raise PermissionError("simulated Windows sharing violation")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", lockedFile)
        with pytest.raises(PreviewTransientCleanupPending):
            runtime.close()
    calls = []

    def unknownDisposal():
        calls.append("close")
        raise OSError("unknown disposal after known cleanup failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runtime.previewAssetStore, "close", unknownDisposal)
            with pytest.raises(OSError, match="unknown disposal"):
                runtime.close()
            with pytest.raises(RuntimeError, match="unknown outcome"):
                runtime.close()
            assert calls == ["close"]
            assert not runtime._closed and runtime._runtimeDataLock._acquired
    finally:
        # Resolve the injected unknown outcome only for test-fixture shutdown.
        runtime._closeStages.pop("preview-assets")
        runtime.close()
