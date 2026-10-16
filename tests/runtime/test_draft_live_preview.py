"""Draft camera context regressions; synthetic camera only, never real hardware."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_live_operator_preview import _FakeLiveCamera, _descriptor


def draftProject(version="2.1"):
    payload = {
        "schemaVersion": version,
        "project": {"projectId": "draft-project", "name": "未保存相机草稿", "revision": 1,
                    "createdAt": "2026-10-08T00:00:00Z", "updatedAt": "2026-10-08T00:00:00Z"},
        "entryWorkflowId": "main", "workflowOrder": ["main"],
        "workflows": {"main": {"name": "Main", "nodes": [
            {"nodeId": "camera", "operatorId": "vision.io.fake_camera",
             "outputPorts": {"image": "image"}, "params": {"triggerMode": "hardware"}},
            # An unrelated incomplete operator must not block camera preview.
            {"nodeId": "unfinished", "operatorId": "vision.io.image_saver", "params": {}},
        ], "edges": []}},
    }
    if version in {"2.2", "2.3"}:
        payload.update(presentation={}, resources={})
    if version == "2.3":
        payload["production"] = {}
    return payload


def draftRequest(payload=None, **changes):
    values = dict(project_id="draft-project", workflow_id="main", node_id="camera",
                  operator_id="vision.io.fake_camera", params_json='{"triggerMode":"software"}',
                  project_json=json.dumps(payload if payload is not None else draftProject(), ensure_ascii=False))
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    _FakeLiveCamera.instances.clear()
    service.livePreviewManager.operatorRegistry = {"vision.io.fake_camera": _descriptor()}
    try:
        yield service
    finally:
        service.close()


@pytest.mark.parametrize("version", ["2.1", "2.2", "2.3"])
def testDraftPreviewAcceptsUnloadedProjectWithoutSavingOrCreatingJob(runtime, version):
    payload = draftProject(version)
    before = deepcopy(payload)
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(payload), None)
    assert reply.ok, reply.message
    assert runtime.loadedDocument is None and runtime.loadedProjectId == ""
    assert runtime.jobRepository.all() == []
    assert payload == before
    frame = next(runtime.StreamOperatorPreviewFrames(SimpleNamespace(session_id=reply.session_id), None))
    assert frame.jpeg.startswith(b"\xff\xd8")
    instance = _FakeLiveCamera.instances[-1]
    assert instance.seenParams[0]["triggerMode"] == "freeRun"
    assert payload["workflows"]["main"]["nodes"][0]["params"]["triggerMode"] == "hardware"
    assert runtime.CloseOperatorPreviewSession(SimpleNamespace(session_id=reply.session_id), None).ok
    assert instance.disposed == 1


def testDraftPreviewDoesNotOverwriteStaleLoadedProject(runtime):
    old = draftProject()
    old["workflows"]["main"]["nodes"] = []
    document = ProjectDocument.model_validate(old)
    runtime.loadedDocument = document
    runtime.loadedPayload = document.model_dump(mode="json")
    runtime.loadedProjectId = document.project.projectId
    originalPayload = deepcopy(runtime.loadedPayload)
    request = draftRequest()
    legacy = runtime.OpenOperatorPreviewSession(request, None)
    assert not legacy.ok and legacy.message == "node is not part of the workflow"
    reply = runtime.OpenDraftOperatorPreviewSession(request, None)
    assert reply.ok, reply.message
    assert runtime.loadedDocument is document
    assert runtime.loadedPayload == originalPayload
    assert runtime.jobRepository.all() == []


@pytest.mark.parametrize("changes", [
    {"project_id": "wrong-project"}, {"workflow_id": "wrong-workflow"},
    {"node_id": "deleted-camera"}, {"operator_id": "vision.io.other-camera"},
    {"project_json": ""}, {"project_json": "{"}, {"project_json": "[]"},
])
def testDraftPreviewRejectsInvalidContextBeforeOpeningCamera(runtime, changes):
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(**changes), None)
    assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"
    assert _FakeLiveCamera.instances == []


@pytest.mark.parametrize("kind", ["workflow_input", "subflow"])
def testDraftPreviewRejectsNonOperatorNode(runtime, kind):
    payload = draftProject()
    payload["workflows"]["main"]["nodes"][0]["kind"] = kind
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(payload), None)
    assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"
    assert _FakeLiveCamera.instances == []


def testDraftPreviewRejectsDuplicateNodeIdentity(runtime):
    payload = draftProject()
    nodes = payload["workflows"]["main"]["nodes"]
    nodes.append(deepcopy(nodes[0]))
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(payload), None)
    assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"
    assert _FakeLiveCamera.instances == []


def testDraftPreviewHasBoundedSnapshotSize(runtime):
    payload = draftProject()
    payload["project"]["name"] = "x" * (1024 * 1024)
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(payload), None)
    assert not reply.ok and reply.code == "E_PREVIEW_CONTEXT_INVALID"
    assert _FakeLiveCamera.instances == []


@pytest.mark.parametrize("params", ["{", "[]"])
def testDraftPreviewRejectsInvalidTemporaryParameters(runtime, params):
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(params_json=params), None)
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert _FakeLiveCamera.instances == []


@pytest.mark.parametrize("terminalButOwned", [False, True])
def testDraftPreviewRejectsAnyRuntimeJobStillOwningDevices(runtime, monkeypatch, terminalButOwned):
    with monkeypatch.context() as patch:
        patch.setattr(runtime.jobRepository, "all", lambda: [
            SimpleNamespace(projectId="other-project", jobId="other-job", isTerminal=terminalButOwned)])
        patch.setattr(runtime.jobSupervisor, "ownsJobResources", lambda _job: terminalButOwned)
        reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(), None)
    assert not reply.ok and reply.code == "E_RESOURCE_BUSY"
    assert _FakeLiveCamera.instances == []


def testCanceledDraftOpenReleasesCamera(runtime):
    checks = []

    def active():
        checks.append(True)
        return len(checks) == 1

    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(), SimpleNamespace(is_active=active))
    assert not reply.ok and reply.code == "E_CANCELLED"
    assert runtime.livePreviewManager._sessions == {}
    assert _FakeLiveCamera.instances[-1].disposed == 1


def testLoadProjectClosesDraftPreviewEvenWithoutPreviouslyLoadedProject(runtime, tmp_path):
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(), None)
    assert reply.ok, reply.message
    payload = draftProject()
    payload["workflows"]["main"]["nodes"] = [
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "output", "kind": "workflow_output"},
    ]
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
    assert loaded.ok, loaded.message
    assert runtime.livePreviewManager._sessions == {}
    assert _FakeLiveCamera.instances[-1].disposed == 1


def testDraftPreviewCannotOpenWhileRuntimeIsClosing(runtime):
    runtime._closing = True
    try:
        with pytest.raises(RuntimeError, match="E_RUNTIME_CLOSING"):
            runtime.OpenDraftOperatorPreviewSession(draftRequest(), None)
    finally:
        runtime._closing = False


@pytest.mark.parametrize("transport", ["sync", "aio"])
def testDraftPreviewUsesRealLoopbackGrpc(runtime, tmp_path, transport):
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
            reply = client.openDraftOperatorPreviewSession(
                "draft-project", "main", "camera", "vision.io.fake_camera", {}, draftProject("2.3"))
            assert reply.ok, reply.message
            stream = client.streamOperatorPreviewFrames(reply.session_id)
            try:
                assert next(stream).jpeg.startswith(b"\xff\xd8")
            finally:
                stream.cancel()
            assert client.closeOperatorPreviewSession(reply.session_id).ok
            assert runtime.loadedDocument is None
            assert runtime.jobRepository.all() == []
    finally:
        if transport == "sync":
            server.stop(0).wait()
            pool.shutdown(wait=True)
        else:
            server.close()
    assert _FakeLiveCamera.instances[-1].disposed == 1


def testDraftPreviewStillRequiresTrustedLiveOperator(runtime):
    runtime.livePreviewManager.operatorRegistry = {}
    reply = runtime.OpenDraftOperatorPreviewSession(draftRequest(), None)
    assert not reply.ok and reply.code == "E_PREVIEW_SESSION_OPEN_FAILED"
    assert _FakeLiveCamera.instances == []


def testNormalJobClosesDraftSessionFromDifferentProject(runtime, tmp_path):
    from tests.runtime.test_global_counter_grpc import _writeCounterProject
    from tests.runtime.runtime_test_utils import waitForTerminal

    root = _writeCounterProject(tmp_path, "normal-project")
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None).ok
    preview = runtime.OpenDraftOperatorPreviewSession(draftRequest(), None)
    assert preview.ok, preview.message
    started = runtime.StartJob(pb.StartJobRequest(project_id="normal-project", workflow_id="main",
                                                 inputs_json='{"increment":true}'), None)
    assert started.ok, started.message
    assert runtime.livePreviewManager._sessions == {}
    assert _FakeLiveCamera.instances[-1].disposed == 1
    terminal = waitForTerminal(runtime, started.job_id)
    assert terminal.status == "COMPLETED", terminal.message
