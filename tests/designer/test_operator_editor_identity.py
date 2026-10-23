from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtWidgets import QLabel, QWidget

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.device_wizard import DeviceNodeDialog
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.designer.qt_wait import waitForCatalog
from tests.designer.test_operator_editor_manager import _Runtime, _Settings
from tests.runtime.test_builtin_vision_operator_workflows import _edge, _registry


def manifest(directory):
    path = Path("src/emo_master/plugins/builtins") / directory / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def saveBboxProject(tmp_path, definition):
    store = WorkflowStore()
    workflow = store.get("main")
    workflow.inputs = {"detections": definition["inputPorts"]["detections"]}
    workflow.outputs = {"bbox": definition["outputPorts"]["bbox"]}
    inputId = next(node["nodeId"] for node in workflow.nodes if node["kind"] == "workflow_input")
    outputId = next(node["nodeId"] for node in workflow.nodes if node["kind"] == "workflow_output")
    workflow.nodes.insert(1, {
        "nodeId": "person_bbox", "operatorId": definition["operatorId"], "displayName": "当前人物框",
        "params": {}, "paramSchema": deepcopy(definition["paramSchema"]),
        "inputPorts": {key: normalizePortType(spec) for key, spec in definition["inputPorts"].items()},
        "outputPorts": {key: normalizePortType(spec) for key, spec in definition["outputPorts"].items()},
    })
    workflow.edges = [_edge(inputId, "detections", "person_bbox", "detections"),
                      _edge("person_bbox", "bbox", outputId, "bbox")]
    store.dependencies["operators"] = [{"operatorId": definition["operatorId"], "version": definition["version"]}]
    document = ProjectDocument.model_validate(store.toPayload("Operator identity"))
    assert document.schemaVersion == "2.7" and document.devices.definitions == {}
    path = tmp_path / "identity.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    WorkflowCompiler(operatorRegistry=_registry()).compile(document)
    return path


@pytest.mark.parametrize("width", [460, 620])
def testLoadedCustomNodeShowsCatalogIdentityWithoutEditingProject(
    tmp_path, ownedDesignerWindow, designerApplication, width,
):
    definition = manifest("detection_bbox")
    client = SimpleNamespace(listOperators=lambda: [SimpleNamespace(**definition)])
    window = ownedDesignerWindow(client)
    waitForCatalog(window)
    path = saveBboxProject(tmp_path, definition)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    before = deepcopy(window.pageCoordinator.session.payload())
    diskBefore = path.read_bytes()
    window.openNodeParamDialog("person_bbox")
    editor = window.nodeParamDialog
    try:
        editor.resize(width, 720)
        designerApplication.processEvents()
        assert editor.windowTitle() == f"当前人物框 · {definition['displayName']} · person_bbox"
        identity = editor._operatorIdentityLabel
        assert identity.text() == f"原始算子：{definition['displayName']}\n算子 ID：{definition['operatorId']}"
        assert identity.isVisible() and identity.wordWrap()
        assert editor.width() == width
        assert editor.rect().contains(identity.geometry())
        assert identity.geometry().bottom() < editor._schemaForm.parentWidget().parentWidget().geometry().top()
        assert any(label.text() == "无可配置参数" for label in editor.findChildren(QLabel))
        assert editor.collectParams() == {} and not editor.isDirty()
        assert not window.pageCoordinator.session.dirty
        assert window.pageCoordinator.session.payload() == before and path.read_bytes() == diskBefore
        screenshotDir = os.environ.get("EMO_OPERATOR_IDENTITY_SCREEN_DIR")
        if screenshotDir:
            target = Path(screenshotDir)
            target.mkdir(parents=True, exist_ok=True)
            assert editor.grab().save(str(target / f"operator-identity-{width}.png"))
    finally:
        editor.forceClose()


def testReopeningEditorRefreshesIdentityWithoutReplacingDraft(tmp_path):
    manager = OperatorEditorManager(runtimeClient=_Runtime(), settingsStore=_Settings(),
        applyParams=lambda *_: True, appendLog=lambda *_: None, cacheRoot=tmp_path)
    options = dict(projectId="p", workflowId="main", nodeId="number", operatorId="vision.value.number",
        displayName="计数", schema={"properties": {"value": {"type": "integer", "default": 1}}},
        values={"value": 1}, operatorDefinition={})
    editor = manager.open(**options)
    try:
        editor._schemaForm._controls["value"].setValue(9)
        options.update(displayName="数值常量", operatorDefinition={"displayName": "数值常量"})
        assert manager.open(**options) is editor
        assert editor.windowTitle() == "数值常量 · number"
        assert editor._operatorIdentityLabel.isVisible()
        assert editor.collectParams() == {"value": 9} and editor.isDirty()
    finally:
        manager.closeAll()


@pytest.mark.parametrize("failure", ["asset", "bind", "open"])
def testCustomEditorFallbackKeepsOriginalIdentity(tmp_path, monkeypatch, failure):
    manager = OperatorEditorManager(runtimeClient=_Runtime(), settingsStore=_Settings(),
        applyParams=lambda *_: True, appendLog=lambda *_: None, cacheRoot=tmp_path)

    def fail(*_):
        raise RuntimeError("test editor failure")

    if failure != "asset":
        controller = SimpleNamespace(bind=fail if failure == "bind" else lambda *_: None,
            loadParams=lambda *_: None, collectParams=lambda: {}, dispose=lambda: None,
            onOpen=fail if failure == "open" else lambda: None)
        monkeypatch.setattr(manager, "_loadCustomEditor", lambda *_: (QWidget(), controller))
    try:
        editor = manager.open(projectId="p", workflowId="main", nodeId="roi", operatorId="vision.preprocess.roi",
            displayName="人物区域", schema={}, values={}, operatorDefinition={
                "displayName": "ROI 区域", "editorSpec": {"kind": "customUi"}})
        assert editor._schemaForm is not None and editor._controller is None
        assert editor.windowTitle() == "人物区域 · ROI 区域 · roi"
        assert "原始算子：ROI 区域" in editor._operatorIdentityLabel.text()
    finally:
        manager.closeAll()


def testLongCatalogNameWrapsWithoutGrowingWindow(tmp_path, designerApplication):
    manager = OperatorEditorManager(runtimeClient=_Runtime(), settingsStore=_Settings(),
        applyParams=lambda *_: True, appendLog=lambda *_: None, cacheRoot=tmp_path)
    name = "用于检测结果的区域提取与坐标转换" * 4
    editor = manager.open(projectId="p", workflowId="main", nodeId="bbox", operatorId="vision.geometry.detection_bbox",
        displayName="人物区域", schema={}, values={}, operatorDefinition={"displayName": name})
    try:
        editor.resize(460, 720)
        designerApplication.processEvents()
        label = editor._operatorIdentityLabel
        assert name in label.text() and editor.width() == 460
        assert label.height() >= label.heightForWidth(label.width())
        assert editor.rect().contains(label.geometry())
        assert not editor._applyButton.visibleRegion().isEmpty()
    finally:
        manager.closeAll()


def testDeviceBindingDialogShowsOriginalOperatorName(ownedDesignerWindow):
    definition = manifest("huaray_camera")
    client = SimpleNamespace(listOperators=lambda: [SimpleNamespace(**definition)],
                             listDeviceTypes=lambda: ({}, {}, {}))
    window = ownedDesignerWindow(client)
    waitForCatalog(window)
    before = deepcopy(window.pageCoordinator.session.payload())
    node = dict(nodeId="camera", operatorId=definition["operatorId"], displayName="工位一相机",
                params={}, paramSchema={}, deviceBindings={})
    dialog = DeviceNodeDialog(window.pageCoordinator, node, {}, window)
    try:
        assert definition["displayName"] in dialog.windowTitle()
        assert dialog.operatorIdentityLabel.text() == (
            f"原始算子：{definition['displayName']}\n算子 ID：{definition['operatorId']}"
        )
        assert window.pageCoordinator.session.payload() == before
    finally:
        dialog.reject()
