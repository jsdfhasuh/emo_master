from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.operator_editors import EditorContext, EditorContextError, EditorKey
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_draft_pure_preview import ROI, HISTOGRAM, PARAMS, draft, imageBytes


KEY = EditorKey("same-project", "main", "draft-node")


def context(runtime, provider):
    return EditorContext(key=KEY, operatorId=ROI, version="1.1.0", previewMode="pure",
        paramSchema={}, runtimeClient=runtime, applyParams=lambda *_args: True,
        appendLog=lambda *_args: None, getPreviewProject=provider)


@pytest.mark.parametrize("version", ["2.1", "2.4"])
def testFreshDraftLifecycleCapturesOnCallerThreadAndKeepsUnsavedParamsSeparate(tmp_path, version):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db")
    payload = draft(version)
    baseline = deepcopy(payload)
    mainThread = threading.get_ident()
    calls = []

    def provider(_key):
        calls.append(threading.get_ident())
        assert threading.get_ident() == mainThread
        return payload

    editor = context(RuntimeClient(runtimeService=runtime), provider)
    try:
        asset = editor.uploadPreviewImage(imageBytes(), "local.png")
        session = editor._pureDraftSessionId
        invocation = editor.preparePurePreview(dict(PARAMS[ROI], width=5.0), asset)
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(invocation).result()
        assert result.ok, result.message
        assert calls and set(calls) == {mainThread}
        assert editor.downloadPreviewAsset(result.assets[0].asset_id)[0]
        assert editor._pureDraftSessionId == session and payload == baseline
        payload["project"]["name"] = "changed draft"
        with pytest.raises(EditorContextError, match="工程草稿已变更"):
            editor.preparePurePreview(PARAMS[ROI], asset)
        assert session not in runtime.pureDraftPreviewSessions.sessions
        asset2 = editor.uploadPreviewImage(imageBytes(), "second.png")
        assert asset2 != asset and editor._pureDraftSessionId != session
        with pytest.raises(EditorContextError, match="不属于"):
            editor.downloadPreviewAsset(asset)
        assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    finally:
        editor.closePurePreview()
        runtime.close()


@pytest.mark.parametrize("version", ["2.1", "2.4"])
def testDraftProviderNeverListsOldProjectSnapshots(version):
    runtime = SimpleNamespace(listNodePreviewSourcesWithMetadata=lambda *_args, **_kwargs: pytest.fail("old project snapshots"))
    listing = context(runtime, lambda _key: draft(version)).listPreviewSourcesWithMetadata()
    assert listing.sources == () and "运行结果检查" in listing.message


def testWithoutDraftProviderKeepsLegacyLoadedTransportAndSourceListing():
    # This originally selected legacy transport by schema 2.1. Real default-new
    # projects are also 2.1 and must preview unsaved canvas nodes, so the explicit
    # compatibility boundary is now the absence of a current-draft provider.
    # Retain the old three-argument upload assertion and exercise all old calls.
    sent, listings, runs, reads = [], [], [], []
    source = SimpleNamespace(sourceId="old")
    runtime = SimpleNamespace(
        uploadPreviewImage=lambda *args: sent.append(args) or SimpleNamespace(ok=True, asset_id="old"),
        listNodePreviewSources=lambda *args: listings.append(args) or [source],
        runOperatorPreview=lambda *args: runs.append(args) or SimpleNamespace(ok=True),
        downloadPreviewAsset=lambda *args: reads.append(args) or (b"legacy", "image/png"))
    editor = context(runtime, None)
    assert editor.uploadPreviewImage(imageBytes(), "old.png") == "old"
    assert len(sent[0]) == 3 and not editor._pureDraftSessionId
    assert editor.listPreviewSources() == [source]
    assert listings == [(KEY.projectId, KEY.workflowId, KEY.nodeId)]
    assert editor.runPurePreview(PARAMS[ROI], "old").ok
    assert runs[0][:4] == (KEY.projectId, KEY.workflowId, KEY.nodeId, ROI)
    assert editor.downloadPreviewAsset("old") == (b"legacy", "image/png")
    assert reads == [("old", KEY.projectId)]


@pytest.mark.parametrize("version", ["2.1", "2.4"])
def testUnsupportedRuntimeIsDetectedBeforeFilePicker(monkeypatch, version):
    from PySide2.QtWidgets import QFileDialog
    from emo_master.plugins.builtins._editor_support import PurePreviewControllerBase
    errors = []
    editor = context(RuntimeClient(runtimeService=SimpleNamespace()), lambda _key: draft(version))
    editor.bindWindowHooks(lambda: None, lambda _message: None, errors.append)
    controller = PurePreviewControllerBase()
    controller.context = editor
    controller.sourceCombo = object()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: pytest.fail("file picker must not open"))
    try:
        controller._chooseLocalImage()
        assert errors and "更新并重启 Runtime" in errors[0]
    finally:
        controller._executor.shutdown(wait=True)


def testOldRemoteRuntimeDoesNotSilentlyUseLoadedProject():
    with ThreadPoolExecutor(1) as pool:
        server = grpc.server(pool)
        rpc.add_RuntimeServiceServicer_to_server(rpc.RuntimeServiceServicer(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                client = RuntimeClient(runtimeService=rpc.RuntimeServiceStub(channel))
                with pytest.raises(RuntimeClientError, match="更新并重启 Runtime"):
                    context(client, lambda _key: draft()).prepareLocalPreview()
        finally:
            server.stop(0).wait()


@pytest.mark.parametrize("operator", [ROI, HISTOGRAM])
@pytest.mark.parametrize("version", ["2.1", "2.2", "2.3", "2.4"])
def testActualLocalImageButtonWithFreshRuntimeAndUnsavedNode(tmp_path, ownedDesignerWindow, monkeypatch, operator, version):
    from PySide2.QtWidgets import QFileDialog
    from tests.designer.qt_wait import waitForCatalog, waitUntil
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    root = tmp_path / "project"
    root.mkdir()
    payload = draft(version, operator)
    # Legacy file opening compiles its saved workflow. Match the application's
    # default empty 2.1 project with explicit interface nodes; modern drafts do
    # not need a compilable persisted graph merely to open an editor.
    payload["workflows"]["main"]["nodes"] = ([
        dict(nodeId="__workflow_input__", kind="workflow_input"),
        dict(nodeId="__workflow_output__", kind="workflow_output"),
    ] if version == "2.1" else [])
    path = root / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    originalFile = path.read_bytes()
    image = tmp_path / "preview.png"
    image.write_bytes(imageBytes())
    window = ownedDesignerWindow(RuntimeClient(runtimeService=service))
    try:
        waitForCatalog(window)
        assert window.loadProjectDirectory(str(root))
        loadedBefore = service.loadedDocument
        assert (loadedBefore is not None) == (version == "2.1")
        oldNodes = set(window.flowModel.nodes)
        window.addNodeFromOperatorPayload(window._operatorDefinition(operator))
        nodeId, = set(window.flowModel.nodes) - oldNodes
        window.openNodeParamDialog(nodeId)
        editor = window.nodeParamDialog
        controller = editor._controller
        assert controller is not None
        controller.loadParams(PARAMS[operator])
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(image), ""))
        controller.localImageButton.click()
        waitUntil(lambda: controller._future is not None and controller._future.done())
        waitUntil(lambda: controller._results.empty())
        assert controller.currentAssetId and editor.context._pureDraftSessionId
        result = controller._future.result()
        assert result.ok, result.message
        sessionId = editor.context._pureDraftSessionId
        if operator == ROI:
            assert next(row.width for row in result.assets if row.port == "croppedImage") == 8
            controller.controls["width"].setValue(5.0)
        else:
            assert len(controller.chart._histogram["binEdges"]) == 9
            controller.binsSpin.setValue(4)
        previous = controller._future
        waitUntil(lambda: controller._future is not previous and controller._future.done())
        assert controller._future.result().ok
        assert editor.context._pureDraftSessionId == sessionId
        assert service.loadedDocument is loadedBefore and service.jobRepository.all() == []
        if loadedBefore is not None:
            assert all(node.nodeId != nodeId for node in loadedBefore.workflows["main"].nodes)
        assert path.read_bytes() == originalFile
        window.operatorEditorManager.closeAll()
        assert service.pureDraftPreviewSessions.sessions == {}
    finally:
        window.operatorEditorManager.closeAll()
        window.close()
        service.close()


def testLateStructuredReplyCannotDisplayAfterCanvasDraftChanges(tmp_path):
    from emo_master.plugins.builtins._editor_support import PurePreviewControllerBase
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db")
    payload = draft()
    errors, displayed = [], []
    editor = context(RuntimeClient(runtimeService=runtime), lambda _key: payload)
    editor.bindWindowHooks(lambda: None, lambda _message: None, errors.append)
    controller = PurePreviewControllerBase()
    controller.context = editor
    controller.handlePreviewResult = lambda outputs, assets: displayed.append((outputs, assets))
    try:
        asset = editor.uploadPreviewImage(imageBytes())
        session = editor._pureDraftSessionId
        invocation = editor.preparePurePreview(PARAMS[ROI], asset)
        # Complete accepted work, but keep its reply queued until the canvas edits.
        with ThreadPoolExecutor(1) as pool:
            reply = pool.submit(invocation).result()
        assert reply.ok
        controller.currentAssetId = asset
        controller._results.put((controller._generation, reply, None))
        payload["workflows"]["main"]["nodes"].append(dict(nodeId="later-node", operatorId=HISTOGRAM))
        controller._pollResults()
        assert errors and "工程草稿已变更" in errors[-1]
        assert all(not outputs and not assets for outputs, assets in displayed)
        assert not controller.currentAssetId
        assert session not in runtime.pureDraftPreviewSessions.sessions
    finally:
        controller.disposePreviewBase()
        runtime.close()


def testSameIdProjectCopySwitchClosesOldEditorSession(tmp_path, ownedDesignerWindow):
    from tests.designer.qt_wait import waitForCatalog
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    paths = []
    for copy in ["first", "second"]:
        root = tmp_path / copy
        root.mkdir()
        path = root / "project.json"
        payload = draft()
        payload["workflows"]["main"]["nodes"] = payload["workflows"]["main"]["nodes"][:1]
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(root)
    window = ownedDesignerWindow(RuntimeClient(runtimeService=service))
    try:
        waitForCatalog(window)
        assert window.loadProjectDirectory(str(paths[0]))
        window.openNodeParamDialog("draft-node")
        first = window.nodeParamDialog.context
        oldAsset = first.uploadPreviewImage(imageBytes())
        oldSession = first._pureDraftSessionId
        assert window.loadProjectDirectory(str(paths[1]))
        assert oldSession not in service.pureDraftPreviewSessions.sessions
        assert service.previewAssetStore.resolve(oldAsset) is None
        window.openNodeParamDialog("draft-node")
        second = window.nodeParamDialog.context
        newAsset = second.uploadPreviewImage(imageBytes())
        assert second._pureDraftSessionId != oldSession
        assert second.downloadPreviewAsset(newAsset)[0]
        with pytest.raises(EditorContextError, match="不属于"):
            second.downloadPreviewAsset(oldAsset)
    finally:
        window.operatorEditorManager.closeAll()
        window.close()
        service.close()


@pytest.mark.parametrize("operator", [ROI, HISTOGRAM])
def testDefaultNew21ProjectPreviewsUnsavedNodeAndAfterSaveWithoutRuntimeReload(tmp_path, ownedDesignerWindow, monkeypatch, operator):
    from PySide2.QtWidgets import QFileDialog
    from tests.designer.qt_wait import waitForCatalog, waitUntil
    service = RuntimeService(dbPath=tmp_path / "runtime.db")
    window = ownedDesignerWindow(RuntimeClient(runtimeService=service))
    projectPath = tmp_path / "default-new.emoproj"
    imagePath = tmp_path / "sample.png"
    imagePath.write_bytes(imageBytes())
    try:
        waitForCatalog(window)
        monkeypatch.setattr(window, "_chooseProjectFile", lambda _title: str(projectPath))
        window.newProjectAction()
        assert json.loads(projectPath.read_text())["schemaVersion"] == "2.1"
        assert window.activeWorkflowId == "main"
        # Mirror the real default-new workflow with existing unrelated nodes.
        for existing in ("vision.value.number", "vision.segment.in_range"):
            window.addNodeFromOperatorPayload(window._operatorDefinition(existing))
        window.saveProjectAction()
        savedBefore = projectPath.read_bytes()
        loadedBefore = service.loadedDocument
        assert loadedBefore is not None
        # No preview or later explicit Ctrl+S may silently reload that snapshot.
        monkeypatch.setattr(service, "LoadProject", lambda *_args, **_kwargs: pytest.fail("preview must not LoadProject"))
        oldNodes = set(window.flowModel.nodes)
        window.addNodeFromOperatorPayload(window._operatorDefinition(operator))
        nodeId, = set(window.flowModel.nodes) - oldNodes
        assert all(node.nodeId != nodeId for node in loadedBefore.workflows["main"].nodes)
        assert nodeId not in {node["nodeId"] for node in json.loads(savedBefore)["workflows"]["main"]["nodes"]}
        window.openNodeParamDialog(nodeId)
        editor = window.nodeParamDialog
        controller = editor._controller
        assert controller is not None
        controller.loadParams(PARAMS[operator])
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(imagePath), ""))

        def chooseImage():
            previous = controller._future
            controller.localImageButton.click()
            waitUntil(lambda: controller._future is not previous and controller._future.done())
            waitUntil(lambda: controller._results.empty())
            reply = controller._future.result()
            assert reply.ok, reply.message
            return reply

        result = chooseImage()
        firstSession = editor.context._pureDraftSessionId
        assert firstSession and controller.currentAssetId
        assert service.loadedDocument is loadedBefore and service.jobRepository.all() == []
        assert projectPath.read_bytes() == savedBefore
        if operator == ROI:
            assert next(row.width for row in result.assets if row.port == "croppedImage") == 8
        else:
            assert len(controller.chart._histogram["binEdges"]) == 9
        # Explicit save changes the draft fingerprint, but still never reloads
        # Runtime. Reselecting the image must open a new isolated draft session.
        window.saveProjectAction()
        savedAfter = projectPath.read_bytes()
        assert savedAfter != savedBefore
        with pytest.raises(EditorContextError, match="工程草稿已变更"):
            editor.context.validatePurePreviewResult()
        assert firstSession not in service.pureDraftPreviewSessions.sessions
        assert chooseImage().ok
        assert editor.context._pureDraftSessionId != firstSession
        assert service.loadedDocument is loadedBefore and service.jobRepository.all() == []
        assert projectPath.read_bytes() == savedAfter
    finally:
        window.operatorEditorManager.closeAll()
        window.close()
        service.close()
