"""Use the optional index port on saved pre-extension nodes without migrating them."""
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QSettings

from emo_master.apps.designer.state.flow_graph_model import FlowEdge, FlowGraphModel
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore, portTypes
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.geometry2d import DetectionCollection
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins.integer_add.operator import IntegerAddOperator
from tests.designer.test_main_window_project_save_load import RuntimeClientStub
from tests.designer.test_operator_modes import choose
from tests.plugins.test_collection_operators import _detections
from tests.runtime.test_global_variables import service, variable


def _catalog(indexType="integer"):
    result = []
    for cls in (CollectionSelectOperator, IntegerAddOperator):
        meta = cls.meta
        inputs = portTypes(meta.inputPorts)
        if cls is CollectionSelectOperator:
            if indexType is None:
                inputs.pop("index")
            else:
                inputs["index"] = indexType
        result.append(SimpleNamespace(operatorId=meta.operatorId, displayName=meta.displayName,
            version=meta.version, inputPorts=inputs, outputPorts=portTypes(meta.outputPorts),
            paramSchema=meta.paramSchema))
    return result


class _Runtime(RuntimeClientStub):
    def __init__(self, catalog):
        self.catalog = catalog

    def listOperators(self):
        return self.catalog


@pytest.fixture
def legacySelection(tmp_path):
    store = WorkflowStore()
    other = store.addWorkflow("Other")
    payload = store.toPayload("Saved selection before optional index input")
    workflow = payload["workflows"]["main"]
    workflow["inputs"] = {"detections": "detectionCollection", "text": "string"}
    workflow["outputs"] = {"selected": "detectionCollection"}
    boundaryInput, boundaryOutput = workflow["nodes"]
    boundaryInput["outputPorts"] = dict(workflow["inputs"])
    boundaryOutput["inputPorts"] = dict(workflow["outputs"])
    for nodeId, cls, params in (
        ("select", CollectionSelectOperator, dict(mode="index", index=0, count=3, onOutOfRange="error")),
        ("index", IntegerAddOperator, dict(leftValue=1)),
    ):
        meta = cls.meta
        workflow["nodes"].append(dict(nodeId=nodeId, kind="operator", operatorId=meta.operatorId,
            displayName=meta.displayName, inputPorts=portTypes(meta.inputPorts),
            outputPorts=portTypes(meta.outputPorts), paramSchema=deepcopy(meta.paramSchema), params=params))
    selection = workflow["nodes"][2]
    # Current project format, but the exact saved port shape from before the extension.
    selection["inputPorts"].pop("index")
    selection["globalVariableBindings"] = [dict(parameterPath=["index"], variableId="legacyIndex")]
    payload["globalVariables"] = {"legacyIndex": variable(0, lifetime="job")}
    workflow["edges"] = [
        dict(fromNode=boundaryInput["nodeId"], fromPort="detections", toNode="select", toPort="detections"),
        dict(fromNode="select", fromPort="selectedDetections", toNode=boundaryOutput["nodeId"], toPort="selected"),
    ]
    workflow["layout"]["nodePositions"].update({
        boundaryInput["nodeId"]: dict(x=-650, y=0), boundaryOutput["nodeId"]: dict(x=650, y=0),
        "index": dict(x=-650, y=350), "select": dict(x=0, y=0),
    })
    registry = {cls.meta.operatorId: cls for cls in (CollectionSelectOperator, IntegerAddOperator)}
    payload["dependencies"] = dict(operators=[dict(operatorId=key, version=cls.meta.version)
                                              for key, cls in registry.items()])
    document = ProjectDocument.model_validate(payload)
    assert document.schemaVersion == "2.7"
    WorkflowCompiler(registry).compile(document)
    path = tmp_path / "legacy-selection.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    return path, registry, boundaryInput["nodeId"], other


def _open(ownedDesignerWindow, tmp_path, path, catalog):
    runtime = _Runtime(catalog)
    window = ownedDesignerWindow(runtime, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    assert window.loadProjectSelection(str(path))
    window.operatorCatalogController.applyOperators(catalog, window._classifyOperator)
    window.flowScene.refreshPresentations()
    return window


@pytest.mark.parametrize("indexType", [None, "number", "integer"])
def testLegacyIndexIsOfferedOnlyWhenCatalogSupportsItWithoutDirtyingProject(
    ownedDesignerWindow, tmp_path, legacySelection, indexType,
):
    path, _, _, other = legacySelection
    before = path.read_bytes()
    window = _open(ownedDesignerWindow, tmp_path, path, _catalog(indexType))
    original = deepcopy(window.flowModel.nodes["select"])
    session = window.pageCoordinator.session
    assert not session.dirty
    for workflow in (other, "main"):
        window.activateWorkflow(workflow)
    assert ("index" in window.flowScene._nodeItems["select"].model.inputPorts) == (indexType == "integer")
    assert ("index" in window._connectableNodePorts("select", "input")) == (indexType == "integer")
    if indexType == "integer":
        assert list(window._connectableNodePorts("select", "input")) == ["detections", "index"]
    assert window.flowModel.nodes["select"] == original
    assert not session.dirty and not session._undo
    assert path.read_bytes() == before


def testLegacyIndexConnectionPreservesBindingsAndSurvivesUndoSaveReloadAndRun(
    ownedDesignerWindow, tmp_path, legacySelection, monkeypatch,
):
    path, registry, boundaryInput, other = legacySelection
    before = path.read_bytes()
    window = _open(ownedDesignerWindow, tmp_path, path, _catalog())
    original = deepcopy(window.flowModel.nodes["select"])
    originalEdges = list(window.flowModel.edges)
    session = window.pageCoordinator.session
    # A failed, ordinary incompatible drag must not add a port or an undo record.
    assert window.connectPorts(boundaryInput, "text", "select", "index") is None
    assert window.flowModel.nodes["select"] == original
    assert window.flowModel.edges == originalEdges and not session.dirty and not session._undo
    assert "index" in window._connectableNodePorts("select", "input")
    edge = window.connectPorts("index", "value", "select", "index")
    assert edge is not None
    window.flowScene.renderEdge(edge)  # same completion path as a canvas drag
    connected = FlowEdge("index", "value", "select", "index")
    assert window.flowModel.edges == [*originalEdges, connected]
    node = window.flowModel.nodes["select"]
    assert node.inputPorts == {**original.inputPorts, "index": "integer"}
    assert node.params == original.params and node.globalVariableBindings == original.globalVariableBindings
    assert node.paramSchema == original.paramSchema and node.outputPorts == original.outputPorts
    assert session.dirty and len(session._undo) == 1
    window.pageCoordinator.history()
    assert window.flowModel.nodes["select"] == original and window.flowModel.edges == originalEdges
    assert not session.dirty and "index" in window._connectableNodePorts("select", "input")
    window.pageCoordinator.history(redo=True)
    assert window.flowModel.nodes["select"].inputPorts["index"] == "integer"
    assert connected in window.flowModel.edges
    for workflow in (other, "main"):
        window.activateWorkflow(workflow)
    assert connected in window.flowModel.edges
    copy = tmp_path / "connected.emoproj"
    assert window.saveProjectToDirectory(str(copy)) and window.loadProjectSelection(str(copy))
    document = loadProjectDocument(copy)
    saved = next(n for n in document.workflows["main"].nodes if n.nodeId == "select")
    assert saved.params == original.params
    assert [binding.model_dump() for binding in saved.globalVariableBindings] == original.globalVariableBindings
    assert saved.inputPorts == {**original.inputPorts, "index": "integer"}
    accessor = service(tmp_path, {"legacyIndex": variable(0, lifetime="job")})
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(document), registry, globalVariables=accessor)
    result = runner.run("main", {"detections": _detections().toPayload(), "text": "wrong-type"},
                        RunContext.root("test", "main"), CancellationToken())
    assert DetectionCollection.fromPayload(result.outputs["selected"]).items[0].detectionId == "d2"
    # Disconnect normally: the saved optional port stays, without removing existing bindings.
    with monkeypatch.context() as patch:
        patch.setattr(window.flowScene, "getSelectedNodeIds", lambda: [])
        patch.setattr(window.flowScene, "getSelectedEdgeKeys", lambda: [("index", "value", "select", "index")])
        window.deleteSelectedElements()
    assert window.flowModel.edges == originalEdges
    assert window.flowModel.nodes["select"].globalVariableBindings == original.globalVariableBindings
    edge = window.connectPorts("index", "value", "select", "index")
    assert edge is not None
    window.flowScene.renderEdge(edge)
    _capture(window)
    assert path.read_bytes() == before


def testLegacyIndexAppearsAfterCatalogArrivalAndKeepsEdgesAcrossModeChanges(
    ownedDesignerWindow, tmp_path, legacySelection,
):
    path, _, _, _ = legacySelection
    window = _open(ownedDesignerWindow, tmp_path, path, [])
    assert "index" not in window._connectableNodePorts("select", "input")
    window.runtimeClient.catalog = _catalog()
    window.operatorCatalogController.applyOperators(_catalog(), window._classifyOperator)
    window.flowScene.refreshPresentations()
    assert "index" in window._connectableNodePorts("select", "input")
    window.openNodeParamDialog("select")
    editor = window.nodeParamDialog
    assert editor.applyChanges(), editor._statusLabel.text()
    editor.forceClose()
    # Merely applying parameters must not force an on-disk port upgrade.
    assert "index" not in window.flowModel.nodes["select"].inputPorts
    edge = window.connectPorts("index", "value", "select", "index")
    assert edge is not None
    window.flowScene.renderEdge(edge)
    edges = list(window.flowModel.edges)
    for mode in ("first", "last", "topK", "index"):
        window.openNodeParamDialog("select")
        editor = window.nodeParamDialog
        choose(editor._schemaForm, "mode", mode)
        assert editor.applyChanges(), editor._statusLabel.text()
        editor.forceClose()
        assert window.flowModel.edges == edges
        assert ("index" in window.flowScene._nodeItems["select"].model.inputPortLabels) == (mode != "index")
        assert ("index" in window._connectableNodePorts("select", "input")) == (mode == "index")
    # A late/offline catalog cannot erase a port which was explicitly saved by the user.
    window.operatorCatalogController.applyOperators([], window._classifyOperator)
    window.flowScene.refreshPresentations()
    assert "index" in window._connectableNodePorts("select", "input") and window.flowModel.edges == edges


def testExtensionTypeDoesNotOverrideSavedTypesOrEnableUnspecifiedPorts():
    model = FlowGraphModel()
    source = model.addNode("source", "Source", {}, {"value": "integer"})
    target = model.addNode("target", "Target", {"existing": "string"}, {})
    before = deepcopy(model.toProjectGraph())
    with pytest.raises(ValueError, match="unknown target port"):
        model.connectNodes(source, "value", target, "new")
    with pytest.raises(ValueError, match="port type mismatch"):
        model.connectNodes(source, "value", target, "existing", newInputPortType="integer")
    assert model.toProjectGraph() == before


def _capture(window):
    directory = os.environ.get("EMO_SELECT_PORT_SCREEN_DIR")
    if not directory:
        return
    from PySide2.QtCore import QRectF
    from PySide2.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
    assert QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc") >= 0
    probe = QImage(200, 40, QImage.Format_ARGB32)
    probe.fill(QColor("white"))
    painter = QPainter(probe)
    painter.setFont(QFont("Microsoft YaHei UI", 12))
    painter.setPen(QColor("black"))
    painter.drawText(5, 25, "index 输入")
    painter.end()
    assert any(QColor(probe.pixel(x, y)).value() < 100 for x in range(200) for y in range(40))
    window.flowScene.refreshPresentations()
    item = window.flowScene._nodeItems["select"]
    area = item.sceneBoundingRect().adjusted(-15, -15, 15, 15)
    scale = float(os.environ.get("QT_SCALE_FACTOR", "1"))
    pixels = QImage(int(area.width() * scale), int(area.height() * scale), QImage.Format_ARGB32)
    pixels.fill(QColor("#f5f7fa"))
    painter = QPainter(pixels)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        window.flowScene.render(painter, QRectF(pixels.rect()), area)
    finally:
        painter.end()
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    assert pixels.save(str(path / f"legacy-index-{scale:g}.png"))
