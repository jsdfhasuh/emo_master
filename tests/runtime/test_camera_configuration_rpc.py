from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_camera_configuration_session import session, waitUntil  # noqa: F401
from tests.runtime.test_draft_live_preview import draftProject


@pytest.mark.parametrize("transport", ["embedded", "grpc"])
def testConfigurationContractMatchesAcrossTransports(session, tmp_path, transport):  # noqa: F811
    manager, worker, api = session
    service = RuntimeService(dbPath=tmp_path / "rpc.db", workspaceRoot=tmp_path / "rpc-jobs")
    service.livePreviewManager.closeAll()
    service.livePreviewManager = manager
    executor = ThreadPoolExecutor(max_workers=4)
    server = grpc.server(executor)
    runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    client = RuntimeClient(service if transport == "embedded" else runtime_pb2_grpc.RuntimeServiceStub(channel))
    try:
        read = client.configureOperatorSession(worker.sessionId, "", "", "read", {})
        assert read.ok
        identity = json.loads(read.state_json)["identity"]
        assert identity == worker.config.identity
        reply = client.configureOperatorSession(worker.sessionId, identity, "start-1", "start", {"triggerMode": "software"})
        assert reply.ok
        assert api.commands == []
        assert client.configureOperatorSession(worker.sessionId, identity, "trigger-1", "trigger", {}).ok
        waitUntil(lambda: worker.latest.state == "softwareReady" and bool(worker.latest.jpeg))
        assert client.configureOperatorSession(worker.sessionId, identity, "trigger-1", "trigger", {}).ok
        assert api.commands == ["TriggerSoftware"]
        stream = iter(client.streamOperatorPreviewFrames(worker.sessionId))
        frame = next(stream)
        assert frame.jpeg and frame.state == "softwareReady"
        assert frame.block_id > 0 and frame.actual_exposure_us == 8000
        close = client.configureOperatorSession(worker.sessionId, identity, "close-1", "close", {"decision": "restore"})
        assert close.ok and not json.loads(close.state_json)["pending"]
        assert api.enums["TriggerMode"] == "Off"
        cancel = getattr(stream, "cancel", None)
        if callable(cancel):
            cancel()
        stale = client.configureOperatorSession("expired", identity, "write", "apply", {"gainRaw": 1})
        assert not stale.ok and stale.code == "E_CAMERA_SESSION_EXPIRED"
    finally:
        channel.close()
        server.stop(0).wait()
        executor.shutdown(wait=True)
        service.close()


def testStartupCatalogReportsUnfinishedAdjustmentsWithoutDeviceWrites(tmp_path):
    records = tmp_path / "camera-adjustments"
    records.mkdir()
    (records / "unfinished.json").write_text(json.dumps({"pending": True, "identity": "camera-1", "sessionId": "old"}), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        reply = service.ListOperators(runtime_pb2.ListOperatorsRequest(), None)
        assert json.loads(reply.camera_recovery_warnings_json)[0]["identity"] == "camera-1"
        assert service.livePreviewManager._configurationSessions == {}
        client = RuntimeClient(service)
        client.listOperators()
        assert client.cameraRecoveryWarnings[0]["sessionId"] == "old"
    finally:
        service.close()


@pytest.mark.parametrize("transport", ["embedded", "grpc"])
@pytest.mark.parametrize("draft", [False, True])
def testOldRuntimeNeverFallsBackToAcquisition(transport, draft):
    calls = []

    class OldRuntime(runtime_pb2_grpc.RuntimeServiceServicer):
        def OpenOperatorPreviewSession(self, request, context):
            calls.append(request)
            return runtime_pb2.OpenOperatorPreviewSessionReply(ok=True, session_id="unsafe-fallback")

        OpenDraftOperatorPreviewSession = OpenOperatorPreviewSession

    with ThreadPoolExecutor(max_workers=1) as executor:
        server = grpc.server(executor)
        old = OldRuntime()
        runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(old, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                # An embedded old service has no configuration method at all.
                target = (SimpleNamespace(OpenOperatorPreviewSession=old.OpenOperatorPreviewSession,
                          OpenDraftOperatorPreviewSession=old.OpenDraftOperatorPreviewSession)
                          if transport == "embedded" else runtime_pb2_grpc.RuntimeServiceStub(channel))
                client = RuntimeClient(target)
                args = ("draft-project", "main", "camera", "vision.io.fake_camera", {})
                with pytest.raises(RuntimeClientError, match="Runtime") as error:
                    if draft:
                        client.openDraftOperatorPreviewSession(*args, draftProject(), configurationOnly=True)
                    else:
                        client.openOperatorPreviewSession(*args, configurationOnly=True)
                assert error.value.code == "E_PREVIEW_UNSUPPORTED"
                assert calls == []
        finally:
            server.stop(0).wait()


@pytest.fixture
def configurationRuntime(tmp_path, monkeypatch):
    from emo_master.plugins.builtins._huaray_imv import resetImvApiForTests
    from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
    from tests.runtime.test_camera_configuration_session import TriggerApi
    from tests.runtime.test_live_operator_preview import _descriptor
    resetImvApiForTests()
    api = TriggerApi()
    monkeypatch.setattr("emo_master.plugins.builtins.huaray_camera.operator.getImvApi", lambda: api)
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    service.livePreviewManager.operatorRegistry = {"vision.io.fake_camera": _descriptor(HuarayCameraOperator)}
    try:
        yield service, api
    finally:
        service.close()
        resetImvApiForTests()


def configurationRequest(draft=True):
    return runtime_pb2.OpenOperatorPreviewSessionRequest(project_id="draft-project", workflow_id="main",
        node_id="camera", operator_id="vision.io.fake_camera",
        params_json='{"selectionMode":"ip","ipAddress":"192.168.1.10"}',
        project_json=json.dumps(draftProject()) if draft else "")


def testConfigurationDraftConnectIsReadOnlyAndDoesNotLoadProject(configurationRuntime):
    service, api = configurationRuntime
    request = configurationRequest()
    before = deepcopy(request)
    reply = service.OpenCameraConfigurationSession(request, None)
    assert reply.ok, reply.message
    assert request == before
    assert api.writes == [] and api.startStrategies == []
    assert service.loadedDocument is None and service.jobRepository.all() == []
    assert service.livePreviewManager.hasConfigurationSession()
    assert service.CloseOperatorPreviewSession(runtime_pb2.CloseOperatorPreviewSessionRequest(session_id=reply.session_id), None).ok
    assert not service.livePreviewManager.ownsResources()


@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("cancelBeforeOpen", [False, True])
def testCancelledConfigurationConnectReleasesDevice(configurationRuntime, draft, cancelBeforeOpen):
    from emo_master.core.project.models import ProjectDocument
    service, api = configurationRuntime
    if not draft:
        service.loadedDocument = ProjectDocument.model_validate(draftProject())
        service.loadedProjectId = "draft-project"
    checks = []

    def active():
        checks.append(True)
        return not cancelBeforeOpen and len(checks) == 1

    reply = service.OpenCameraConfigurationSession(configurationRequest(draft), SimpleNamespace(is_active=active))
    assert not reply.ok and reply.code == "E_CANCELLED", reply.message
    assert api.writes == [] and api.startStrategies == []
    assert api.openCount == (0 if cancelBeforeOpen else 1)
    assert not service.livePreviewManager.ownsResources()
