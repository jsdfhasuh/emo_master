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
        assert dialog.exportSelection()["wire"] == {"inline": 9}
        dialog.versions.setCurrentIndex(1)
        historical = dialog.exportSelection()
        assert historical["port"] == "value" and historical["wire"] == {"inline": 3}
        assert historical["identity"]["selection"] == "history"
        assert historical["identity"]["rawParams"] == {"value": 3}
        dialog.versions.setCurrentIndex(0)
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
    editor, *_ = editorFor(runtime, "untrusted.operator")
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


def testPlcPrivateDebugExplainsManagedRouteWithoutConnecting(runtime):
    from tests.runtime.plc_debug_server import PlcDebugServer

    with PlcDebugServer() as peer:
        editor, payload, before, calls = editorFor(runtime, "communication.plc.slmp_read")
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        dialog.show()
        try:
            wait(lambda: dialog.state == "FAULTED")
            assert "设备管理" in dialog.status.text()
            assert not dialog.run.isEnabled()
            assert not runtime.operatorDebugManager.opens
            assert peer.connections == 0 and peer.requests == []
            assert payload == before and calls == [] and runtime.loadedDocument is None
            assert not runtime.jobRepository.all()
        finally:
            editor.forceClose()


@pytest.mark.parametrize("encoding", ["binary", "ascii"])
def testManagedPlcDiagnosticUsesCurrentReadControls(runtime, tmp_path, record_property, encoding):
    import json
    from emo_master.apps.designer.ui.managed_plc_debug import ManagedPlcDebugDialog
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from tests.runtime.plc_debug_server import PlcDebugServer
    from tests.runtime.test_managed_slmp import plcProject

    with PlcDebugServer(encoding=encoding) as peer:
        peer.words.update({800: 12, 801: 34, 802: 56})
        path = tmp_path / "managed-plc.emoproj"
        path.write_text(json.dumps(plcProject(peer.port, encoding)), encoding="utf-8")
        before = path.read_bytes()
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        document = runtime.loadedDocument
        snapshot = document.model_dump()
        client = RuntimeClient(runtime)
        declaration = runtime.pluginScanResult.deviceTypes["device.plc.mitsubishi_slmp"]
        definition = declaration.validateDefinition(document.devices.definitions["plc"])
        dialog = ManagedPlcDebugDialog(client, document.project.projectId, "plc", definition)
        controller = dialog.controller
        dialog.show()
        try:
            assert peer.connections == 0
            assert all(not control.isEnabled() for control in controller._endpointControls.values())
            assert not controller.reloadButton.isEnabled()
            controller.addressSpin.setValue(800)
            controller.countSpin.setValue(2)
            controller.dataTypeCombo.setCurrentIndex(controller.dataTypeCombo.findData("uint16"))
            QTest.mouseClick(controller.connectButton, Qt.LeftButton)
            wait(lambda: controller.readButton.isEnabled())
            QTest.mouseClick(controller.readButton, Qt.LeftButton)
            wait(lambda: controller.resultTable.rowCount() == 2 and controller.readButton.isEnabled())
            assert [controller.resultTable.item(row, 0).text() for row in range(2)] == ["D800", "D801"]
            assert [controller.resultTable.item(row, 2).text() for row in range(2)] == ["12", "34"]
            assert "读取完成" in controller.statusLabel.text()
            controller.addressSpin.setValue(801)
            QTest.mouseClick(controller.readButton, Qt.LeftButton)
            wait(lambda: controller.resultTable.item(0, 0).text() == "D801" and controller.readButton.isEnabled())
            assert [controller.resultTable.item(row, 2).text() for row in range(2)] == ["34", "56"]
            assert [request.start for request in peer.requests] == [800, 801]
            assert all(request.command == 0x0401 and request.ascii == (encoding == "ascii") for request in peer.requests)
            assert not controller.writeEnabledCheck.isChecked()
            assert runtime.loadedDocument is document and document.model_dump() == snapshot
            assert path.read_bytes() == before and not runtime.jobRepository.all()
            capture = tmp_path / "managed-plc-read-result.png"
            assert dialog.grab().save(str(capture))
            record_property("screenshot", str(capture))
        finally:
            dialog.reject()
            wait(lambda: not runtime.plcDebugManager.ownsResources())
            client.close()


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
        from PySide2.QtCore import QCoreApplication, QEvent
        window.operatorEditorManager.closeAll()
        def retired():
            # The Runtime ACK reaches Qt after resourcesHeld becomes false.
            # Keep the original deadline, but verify the native owners too.
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            return (not runtime.operatorDebugManager.ownsResources()
                    and not isValid(debug) and not isValid(editor)
                    and window.operatorEditorManager.count() == 0)
        wait(retired)


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
