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


def testActualCameraSingleFrameButtonUsesUnsavedCanvasSnapshot(tmp_path):
    from PySide2.QtWidgets import QPushButton

    from emo_master.apps.designer.ui.main_window import MainWindow
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.core.plugin.models import PluginDescriptor
    from tests.designer.qt_wait import waitForCatalog, waitUntil
    from tests.runtime.test_global_counter_grpc import _writeCounterProject
    from tests.runtime.test_live_operator_preview import _FakeLiveCamera

    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    descriptor = service.pluginScanResult.activeOperators["vision.io.huaray_camera"]
    # Use the real built-in camera editor and manifest but never open camera SDK.
    service.livePreviewManager.operatorRegistry = {
        "vision.io.huaray_camera": PluginDescriptor(manifest=descriptor.manifest, operatorClass=_FakeLiveCamera)
    }
    _FakeLiveCamera.instances.clear()
    root = _writeCounterProject(tmp_path, "canvas-project")
    originalFile = (root / "project.json").read_bytes()
    window = MainWindow(RuntimeClient(runtimeService=service))
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
        values = controller.collectParams()
        values["triggerMode"] = "software"
        controller.loadParams(values)
        controller.root.findChild(QPushButton, "singleFrameButton").click()
        waitUntil(lambda: controller.blockIdLabel.text().split("：")[-1].strip().isdigit())
        waitUntil(lambda: controller._sessionId == "")
        assert _FakeLiveCamera.instances[-1].disposed == 1
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
        window.operatorEditorManager.closeAll()
        window.close()
        service.close()
