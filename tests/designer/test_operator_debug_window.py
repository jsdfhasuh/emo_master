from copy import deepcopy
import time
from types import SimpleNamespace

import pytest
import emo_master  # noqa: F401
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication
from shiboken2 import isValid

from emo_master.apps.designer.operator_editors import EditorContext, EditorKey
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.debug_values import InputRow, ValueTree
from tests.runtime.operator_debug_fixture import project
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.designer.test_node_context_menu import canvas as canvas


def wait(predicate, seconds=12):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def editorFor(runtime, operatorId="vision.value.number"):
    payload = project(operatorId)
    before = deepcopy(payload)
    calls = []
    context = EditorContext(key=EditorKey("draft", "main", "node"), operatorId=operatorId,
        version="1", previewMode="none", paramSchema={}, runtimeClient=RuntimeClient(runtime),
        applyParams=lambda *_: calls.append("apply"), appendLog=lambda *_: None,
        getPreviewProject=lambda _: payload, variableDefinitions=lambda: {})
    editor = OperatorWorkspaceWindow(key=context.key, title="未保存算子测试", context=context,
        schema={"type": "object", "properties": {"value": {"type": "number", "default": 0}}}, values={"value": 3})
    editor.show()
    return editor, payload, before, calls


def testNativeWindowExecutesUnappliedDraftAndKeepsTwoResults(runtime):
    editor, payload, before, calls = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(lambda: dialog.run.isEnabled())
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        assert dialog.records[0]["outputs"] == {"value": 3}
        editor._schemaForm.setSchema(editor.context.paramSchema | {"type": "object", "properties": {"value": {"type": "number"}}}, {"value": 9})
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 2 and dialog.run.isEnabled())
        assert [record["outputs"]["value"] for record in dialog.records] == [9, 3]
        assert payload == before and calls == [] and runtime.loadedDocument is None
        assert not runtime.jobRepository.all()
        QTest.mouseClick(dialog.reset, Qt.LeftButton)
        wait(lambda: dialog.run.isEnabled() and dialog.connection.identity["generation"] == 2)
        assert dialog.records == []
        QTest.mouseClick(dialog.end, Qt.LeftButton)
        wait(lambda: not isValid(dialog))
        assert not runtime.operatorDebugManager.ownsResources()
    finally:
        editor.forceClose()


def testEditorCloseRetiresWorkerEvenWhileOpening(runtime):
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    editor.forceClose()
    wait(lambda: dialog.connection.stop.is_set())
    # Observe the actual accepted owner, not just an initial empty slot.
    wait(lambda: bool(runtime.operatorDebugManager.opens))
    wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testUnsupportedOperatorDoesNotStartWorker(runtime):
    editor, *_ = editorFor(runtime, "vision.io.huaray_camera")
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    try:
        wait(lambda: dialog.state == "FAULTED")
        assert "E_DEBUG_UNSUPPORTED" in dialog.status.text()
        assert not runtime.operatorDebugManager.opens
        dialog.close()
        wait(lambda: not isValid(dialog))
    finally:
        editor.forceClose()


def testInputMissingNullAndFalseRemainDistinct():
    row = InputRow("boolean")
    assert row.wire() is None
    row.mode.setCurrentIndex(1)
    assert row.wire() == {"inline": False}
    row.mode.setCurrentIndex(2)
    assert row.wire() == {"inline": None}
    row.deleteLater()


def testNumericInputRejectsBooleanAndNonfinite():
    row = InputRow("number")
    row.mode.setCurrentIndex(1)
    for raw in ("true", "NaN", "1e999"):
        row.text.setText(raw)
        with pytest.raises(ValueError):
            row.wire()
    row.deleteLater()


def testTreePaginatesCompleteStructuredValue():
    tree = ValueTree()
    tree.setValue({"values": list(range(250))})
    root = tree.topLevelItem(0).child(0)
    root.setExpanded(True)
    assert root.childCount() == 101
    tree.more(root.child(100), 0)
    assert root.childCount() == 201
    tree.more(root.child(200), 0)
    assert root.childCount() == 250
    tree.deleteLater()


def testConnectionCannotSilentlyMoveToReconnectedRuntime(runtime):
    from emo_master.apps.designer.services.operator_debug import DebugConnection
    client = RuntimeClient(runtime)
    connection = DebugConnection(client)
    client.runtimeService = SimpleNamespace()
    assert not connection.attached()
    assert connection.client.runtimeService is runtime


def testOutputSelectionQueuesBehindBusyPollAndIgnoresStaleDownload(runtime):
    import threading
    import numpy as np
    from emo_master.apps.runtime.operator_debug.assets import imageBytes
    editor, *_ = editorFor(runtime)
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    gate = threading.Event()
    try:
        wait(lambda: dialog.run.isEnabled())
        dialog.timer.stop()
        raw = imageBytes(np.full((8, 8, 3), 120, np.uint8))
        calls = []
        def download(asset):
            calls.append(asset)
            return dict(asset={"mime": "image/png"}, content=raw)
        dialog.connection.download = download
        dialog.records = [dict(status="SUCCEEDED", outputs={"value": 1},
                               outputAssets={"image": {"assetId": "image", "mime": "image/png"}})]
        dialog.versions.addItem("result")
        dialog.submit("busy-test", lambda: gate.wait(3))
        dialog.outputPorts.setCurrentIndex(1)
        assert dialog.pendingAsset == "image" and calls == []
        gate.set()
        wait(lambda: dialog.pixmap is not None)
        assert calls == ["image"] and not dialog.pixmap.isNull()
        dialog.outputPorts.setCurrentIndex(0)
        dialog.completed("download:image", download("image"), None)
        assert dialog.pixmap is None
        dialog.close()
        wait(lambda: not isValid(dialog))
    finally:
        gate.set()
        editor.forceClose()


def testPreviewInvalidationDoesNotStopResourceOwningDebugger():
    from emo_master.apps.designer.operator_editors.manager import OperatorEditorManager
    calls = []
    window = SimpleNamespace(context=SimpleNamespace(invalidatePreviewSources=lambda: calls.append("preview")),
                             closeDebug=lambda: calls.append("debug"))
    OperatorEditorManager.invalidatePreviewSources(SimpleNamespace(_windows={"node": window}))
    assert calls == ["preview"]


def testUnsavedCanvasNodeContextEntryLeavesDraftHistoryUntouched(runtime, tmp_path, ownedDesignerWindow):
    from tests.designer.qt_wait import waitForCatalog
    from tests.runtime.test_global_counter_grpc import _writeCounterProject
    root = _writeCounterProject(tmp_path, "canvas-project")
    original = (root / "project.json").read_bytes()
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    assert window.loadProjectDirectory(str(root))
    loaded = runtime.loadedDocument
    previous = set(window.flowModel.nodes)
    window.addNodeFromOperatorPayload(window._operatorDefinition("vision.value.number"))
    nodeId, = set(window.flowModel.nodes)-previous
    window.flowScene.clearSelection()
    window.flowScene.setNodeSelected(nodeId)
    window.onNodeSelectionChanged()
    before = deepcopy(window.pageCoordinator.session.payload())
    undoDepth = len(window.pageCoordinator.session._undo)
    menu = window.nodeContextMenu.buildMenu(nodeId)
    action = next(action for action in menu.actions() if action.data() == "operator_debug")
    assert action.isEnabled()
    action.trigger()
    editor = window.nodeParamDialog
    debug = editor._debugWindow
    try:
        wait(lambda: debug.run.isEnabled())
        editor._schemaForm.setSchema(editor.context.paramSchema, {"value": 27})
        QTest.mouseClick(debug.run, Qt.LeftButton)
        wait(lambda: bool(debug.records))
        assert debug.records[0]["outputs"] == {"value": 27}
        assert window.pageCoordinator.session.payload() == before
        assert len(window.pageCoordinator.session._undo) == undoDepth
        assert runtime.loadedDocument is loaded
        assert (root/"project.json").read_bytes() == original
        assert not runtime.jobRepository.all()
        assert window.hasActiveDebugSession()
        assert not window._syncRuntimeProjectBeforeRun()
        window.runtimeController.startJob()
        QApplication.processEvents()
        assert not debug.closing and runtime.operatorDebugManager.ownsResources()
        assert window.pageCoordinator.session.payload() == before
        assert (root/"project.json").read_bytes() == original
    finally:
        window.operatorEditorManager.closeAll()
        wait(lambda: not runtime.operatorDebugManager.ownsResources())


def testDebugActionCannotRunInPageWorkspaceOrOnControlNodes(canvas, monkeypatch):
    from tests.designer.test_designer_shortcuts import makePage
    window, (nodeId, _), _client = canvas
    calls = []
    monkeypatch.setattr(window, "openNodeParamDialog", lambda *args: calls.append(args))
    window.flowScene.setNodeSelected(nodeId)
    node = window.flowModel.nodes[nodeId]
    original = node.kind
    node.kind = "subflow"
    window.designerActions.invoke("operator_debug")
    assert not calls
    node.kind = original
    makePage(window, monkeypatch)
    window.designerActions.invoke("operator_debug")
    assert not calls
