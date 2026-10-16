from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.operator_editors import EditorContext, EditorContextError, EditorKey, OperatorEditorManager
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from tests.runtime.test_draft_live_preview import draftProject


KEY = EditorKey("draft-project", "main", "camera")


def context(runtime, provider=None):
    return EditorContext(key=KEY, operatorId="vision.io.fake_camera", version="1.0", previewMode="live",
                         paramSchema={}, runtimeClient=runtime, applyParams=lambda *_args: True,
                         appendLog=lambda *_args: None, getPreviewProject=provider)


def testContextSendsFreshDraftAndKeepsPreviewParametersSeparate():
    payload = draftProject()
    requests = []

    def openDraft(project, workflow, node, operator, params, projectPayload):
        requests.append((project, workflow, node, operator, params, deepcopy(projectPayload)))
        return SimpleNamespace(ok=True, session_id=f"session-{len(requests)}")

    editor = context(SimpleNamespace(openDraftOperatorPreviewSession=openDraft), lambda key: payload if key == KEY else None)
    assert editor.openLivePreview({"triggerMode": "software"}) == "session-1"
    payload["project"]["name"] = "updated draft"
    assert editor.openLivePreview({"triggerMode": "freeRun"}) == "session-2"
    assert requests[0][:4] == (KEY.projectId, KEY.workflowId, KEY.nodeId, "vision.io.fake_camera")
    assert requests[0][4] == {"triggerMode": "software"}
    assert requests[1][5]["project"]["name"] == "updated draft"
    assert payload["workflows"]["main"]["nodes"][0]["params"] == {"triggerMode": "hardware"}


def testContextNeverFallsBackToStaleLoadedWorkflowWhenDraftRpcUnavailable():
    runtime = SimpleNamespace(openOperatorPreviewSession=lambda *_args: pytest.fail("stale fallback"))
    with pytest.raises(EditorContextError, match="Runtime"):
        context(runtime, lambda _key: draftProject()).openLivePreview({})


def testContextWithoutDraftProviderRetainsLegacyCompatibility():
    runtime = SimpleNamespace(openOperatorPreviewSession=lambda *_args: SimpleNamespace(ok=True, session_id="legacy"))
    assert context(runtime).openLivePreview({}) == "legacy"


def testConfigurationFreezesDraftOnCallingThreadAndPinsRuntime():
    import threading
    payload = draftProject()
    params = {"ipAddress": "192.168.1.10"}
    calls = []
    thread = threading.get_ident()
    originalService = object()

    def provider(_key):
        assert threading.get_ident() == thread
        return payload

    def openDraft(*args, **kwargs):
        assert threading.get_ident() != thread
        calls.append((args, kwargs))
        return SimpleNamespace(ok=True, session_id="configuration")

    runtime = SimpleNamespace(runtimeService=originalService, openDraftOperatorPreviewSession=openDraft)
    editor = context(runtime, provider)
    operation = editor.prepareCameraConfiguration(params)
    payload["project"]["name"] = "changed after dispatch"
    params["ipAddress"] = "192.168.1.20"
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(operation).result() == "configuration"
    assert calls[0][0][4] == {"ipAddress": "192.168.1.10"}
    assert calls[0][0][5]["project"]["name"] != payload["project"]["name"]
    assert calls[0][1] == {"configurationOnly": True}
    runtime.runtimeService = object()
    with pytest.raises(EditorContextError, match="Runtime"):
        editor.prepareCameraConfiguration(params)


def testRuntimeClientReportsOldRuntimeWithoutSilentlyStartingLegacyPreview():
    client = RuntimeClient(runtimeService=SimpleNamespace())
    with pytest.raises(RuntimeClientError, match="Runtime"):
        client.openDraftOperatorPreviewSession(KEY.projectId, KEY.workflowId, KEY.nodeId,
                                              "vision.io.fake_camera", {}, draftProject())


def testOldRemoteRuntimeReportsUnsupportedInsteadOfStaleWorkflowError():
    with ThreadPoolExecutor(max_workers=1) as pool:
        server = grpc.server(pool)
        # The default servicer deliberately implements no draft RPC.
        rpc.add_RuntimeServiceServicer_to_server(rpc.RuntimeServiceServicer(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                client = RuntimeClient(runtimeService=rpc.RuntimeServiceStub(channel))
                with pytest.raises(RuntimeClientError, match="更新并重启 Runtime") as error:
                    client.openDraftOperatorPreviewSession(KEY.projectId, KEY.workflowId, KEY.nodeId,
                                                          "vision.io.fake_camera", {}, draftProject())
                assert error.value.code == "E_PREVIEW_UNSUPPORTED"
        finally:
            server.stop(0).wait()


def testEditorManagerWiresDraftSnapshotProvider(tmp_path):
    calls = []
    runtime = SimpleNamespace(openDraftOperatorPreviewSession=lambda *_args: SimpleNamespace(ok=True, session_id="draft"))
    settings = SimpleNamespace(value=lambda _key, default=None: default, setValue=lambda *_args: None)
    manager = OperatorEditorManager(runtimeClient=runtime, settingsStore=settings,
                                    applyParams=lambda *_args: True, appendLog=lambda *_args: None,
                                    cacheRoot=tmp_path, getPreviewProject=lambda key: calls.append(key) or draftProject())
    try:
        window = manager.open(projectId=KEY.projectId, workflowId=KEY.workflowId, nodeId=KEY.nodeId,
                              operatorId="vision.io.fake_camera", displayName="Camera", schema={}, values={},
                              operatorDefinition={"editorSpec": {"previewMode": "live"}})
        assert window.context.openLivePreview({}) == "draft"
        assert calls == [KEY]
    finally:
        manager.closeAll()


def testActualCameraSingleFrameButtonUsesUnsavedCanvasSnapshot(tmp_path, monkeypatch):
    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.plugins.builtins._huaray_imv import resetImvApiForTests
    from tests.designer.qt_wait import waitForCatalog, waitUntil
    from tests.runtime.test_global_counter_grpc import _writeCounterProject
    from tests.runtime.test_camera_configuration_session import TriggerApi

    resetImvApiForTests()
    api = TriggerApi()
    monkeypatch.setattr("emo_master.plugins.builtins.huaray_camera.operator.getImvApi", lambda: api)
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    root = _writeCounterProject(tmp_path, "canvas-project")
    originalFile = (root / "project.json").read_bytes()
    window = MainWindow(RuntimeClient(runtimeService=service))
    editor = controller = None
    try:
        waitForCatalog(window)
        assert window.loadProjectDirectory(str(root))
        loadedDocument = service.loadedDocument
        projectMetadata = deepcopy(window.workflowStore.project)
        before = set(window.flowModel.nodes)
        window.addNodeFromOperatorPayload(window._operatorDefinition("vision.io.huaray_camera"))
        nodeId, = set(window.flowModel.nodes) - before
        originalParams = deepcopy(window.flowModel.nodes[nodeId].params)
        window.openNodeParamDialog(nodeId)
        editor = window.nodeParamDialog
        controller = editor._controller
        assert controller is not None
        controller.form._controls["ipAddress"].setText("192.168.1.10")
        controller.connectButton.click()
        waitUntil(lambda: controller._sessionId and controller._pending is None)
        assert api.writes == [] and api.startStrategies == []
        controller.singleFrameButton.click()
        waitUntil(lambda: controller.blockIdLabel.text().split("：")[-1].strip().isdigit())
        waitUntil(lambda: controller._pending is None and controller._snapshot.get("state") == "connected")
        assert controller._sessionId and api.openCount == 1
        assert service.loadedDocument is loadedDocument
        assert all(node.nodeId != nodeId for node in loadedDocument.workflows["main"].nodes)
        assert service.jobRepository.all() == []
        assert window.flowModel.nodes[nodeId].params == originalParams
        assert window.workflowStore.project == projectMetadata
        assert (root / "project.json").read_bytes() == originalFile
        assert window.pageCoordinator.session.dirty
        with pytest.raises(EditorContextError, match="工程已关闭"):
            window._livePreviewProject(EditorKey("closed-project", "main", nodeId))
    finally:
        if controller is not None:
            waitUntil(lambda: controller._pending is None)
            if controller._sessionId:
                assert controller._modalAction("close", {"decision": "restore"})["ok"]
                controller._finishSession()
        if editor is not None:
            editor.forceClose()
        window.operatorEditorManager.closeAll()
        window.close()
        service.close()
        resetImvApiForTests()
