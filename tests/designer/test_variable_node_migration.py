"""Variable insertion must stay a valid, undoable project editing command."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.operator_editors.controller_protocol import EditorKey
from emo_master.apps.designer.state import global_variables
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.global_variables import VariableError
from emo_master.core.project.models import ProjectDocument
from emo_master.plugins.builtins.variable_read.operator import ReadVariableOperator
from emo_master.plugins.builtins.variable_write.operator import WriteVariableOperator


OPERATORS = [ReadVariableOperator, WriteVariableOperator]
ENTRIES = ["drop", "bubble", "library"]


@pytest.fixture(autouse=True)
def approveExplicitMigration(monkeypatch):
    from PySide2.QtWidgets import QMessageBox
    original = QMessageBox.question
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, *args, **kwargs:
        QMessageBox.Yes if title == "升级变量工程" else original(parent, title, *args, **kwargs))


def _window():
    return MainWindow(SimpleNamespace(
        listOperators=lambda: [],
        loadProject=lambda path: SimpleNamespace(ok=True, message="ok"),
    ))


def _payload(operator):
    meta = operator.meta
    return dict(operatorId=meta.operatorId, displayName=meta.displayName,
                inputPorts={key: normalizePortType(value) for key, value in meta.inputPorts.items()},
                outputPorts={key: normalizePortType(value) for key, value in meta.outputPorts.items()},
                paramSchema=deepcopy(meta.paramSchema))


def _insert(window, operator, entry):
    payload = _payload(operator)
    if entry == "drop":
        window.addNodeFromOperatorDrop(payload, 160., 240.)
    elif entry == "bubble":
        window.addNodeFromOperatorPayload(payload)
    else:
        window.addNodeFromOperator(SimpleNamespace(data=lambda role: payload))


def _definitions():
    return {
        "count": dict(name="count", type="integer", lifetime="job", kind="variable", initialValue=3),
        "fixed": dict(name="fixed", type="integer", lifetime="persistent", kind="constant", initialValue=7),
    }


@pytest.mark.parametrize("operator", OPERATORS)
@pytest.mark.parametrize("entry", ENTRIES)
def testFirstVariableNodeMigratesBeforeInsertionAndSavesWithHistory(designerApplication, tmp_path, operator, entry):
    window = _window()
    path = tmp_path / "draft.emoproj"
    assert window.saveProjectToDirectory(str(path))
    session = window.pageCoordinator.session
    before = session.payload()
    assert before["schemaVersion"] == "2.1"
    assert "globalVariables" not in before
    beforeNodes = set(window.flowModel.nodes)
    undoDepth = len(session._undo)

    _insert(window, operator, entry)

    nodeId = window.flowModel.selectedNodeId
    assert nodeId not in beforeNodes
    assert window.flowModel.nodes[nodeId].operatorId == operator.meta.operatorId
    assert session.document().schemaVersion == "2.4"
    assert session.payload()["globalVariables"] == {}
    assert session.dirty
    assert len(session._undo) == undoDepth + 1  # Migration and insertion are one edit.
    if entry == "drop":
        assert window.flowScene.getNodePositions()[nodeId] == (160., 240.)
    after = session.payload()

    window.pageCoordinator.history()
    assert session.payload() == before
    assert set(window.flowModel.nodes) == beforeNodes
    assert session.document().schemaVersion == "2.1"
    assert not session.dirty
    window.pageCoordinator.history(redo=True)
    assert session.payload() == after
    assert nodeId in window.flowModel.nodes
    assert session.document().schemaVersion == "2.4"

    assert window.saveProjectToDirectory(str(path))
    saved = ProjectDocument.model_validate_json(path.read_text(encoding="utf-8"))
    assert saved.schemaVersion == "2.4"
    assert any(node.nodeId == nodeId for node in saved.workflows["main"].nodes)
    assert json.loads(path.with_suffix(".emoproj.bak").read_text(encoding="utf-8"))["schemaVersion"] == "2.1"
    assert not session.dirty

    reopened = _window()
    assert reopened.loadProjectDirectory(str(path))
    assert reopened.pageCoordinator.session.document().schemaVersion == "2.4"
    assert reopened.flowModel.nodes[nodeId].operatorId == operator.meta.operatorId

    # Saving must not destroy the ability to undo/redo the combined edit.
    window.pageCoordinator.history()
    assert nodeId not in window.flowModel.nodes
    assert session.document().schemaVersion == "2.1"
    window.pageCoordinator.history(redo=True)
    assert nodeId in window.flowModel.nodes
    assert session.document().schemaVersion == "2.4"
    assert window.saveProjectToDirectory(str(path))


@pytest.mark.parametrize("operator", OPERATORS)
def testUnconfiguredVariableNodeCopyRemainsUndoable(designerApplication, operator):
    window = _window()
    _insert(window, operator, "drop")
    session = window.pageCoordinator.session
    before = session.payload()
    original = window.flowModel.selectedNodeId
    duplicate = window.duplicateSelectedNode()
    assert duplicate and duplicate != original
    assert window.flowModel.nodes[duplicate].params == {}
    assert session.document().schemaVersion == "2.4"
    assert session.payload()["globalVariables"] == {}
    window.pageCoordinator.history()
    assert session.payload() == before
    assert duplicate not in window.flowModel.nodes
    window.pageCoordinator.history(redo=True)
    assert duplicate in window.flowModel.nodes
    assert session.document().schemaVersion == "2.4"


@pytest.mark.parametrize("operator", OPERATORS)
def testExistingDefinitionsAndVariableCopiesRemainValid(designerApplication, operator):
    window = _window()
    window._editGlobalVariableDefinitions(_definitions())
    session = window.pageCoordinator.session
    before = session.payload()
    definitions = deepcopy(before["globalVariables"])
    _insert(window, operator, "drop")
    nodeId = window.flowModel.selectedNodeId
    key = EditorKey(window._currentProjectId(), window.activeWorkflowId, nodeId)
    params = {"variableId": "count"}
    if operator is WriteVariableOperator:
        params["operation"] = "increment"
    assert window._applyEditorConfiguration(key, params, [])
    configured = session.payload()

    window.flowModel.selectNode(nodeId)
    window.flowScene.setNodeSelected(nodeId)
    duplicate = window.duplicateSelectedNode()
    assert duplicate and duplicate != nodeId
    assert window.flowModel.nodes[duplicate].params == window.flowModel.nodes[nodeId].params
    assert session.document().schemaVersion == "2.4"
    assert session.payload()["globalVariables"] == definitions
    window.pageCoordinator.history()
    assert session.payload() == configured
    window.pageCoordinator.history(redo=True)
    assert duplicate in window.flowModel.nodes

    beforeWorkflowCopy = session.payload()
    workflowId = window.duplicateCurrentWorkflow()
    assert workflowId and workflowId != "main"
    copied = session.document().workflows[workflowId]
    assert len([node for node in copied.nodes if node.operatorId == operator.meta.operatorId]) == 2
    assert session.payload()["globalVariables"] == definitions
    window.pageCoordinator.history()
    assert session.payload() == beforeWorkflowCopy
    window.pageCoordinator.history(redo=True)
    assert workflowId in session.document().workflows
    assert session.payload()["globalVariables"] == definitions


@pytest.mark.parametrize("operator", OPERATORS)
def testVariableInsertionPreservesConstantProtection(designerApplication, operator):
    window = _window()
    window._editGlobalVariableDefinitions(_definitions())
    _insert(window, operator, "drop")
    nodeId = window.flowModel.selectedNodeId
    node = window.flowModel.nodes[nodeId]
    choices = window._nodeEditorSchema(node)["properties"]["variableId"]["enum"]
    key = EditorKey(window._currentProjectId(), window.activeWorkflowId, nodeId)
    session = window.pageCoordinator.session
    before = session.payload()
    if operator is ReadVariableOperator:
        assert "fixed" in choices
        assert window._applyEditorConfiguration(key, {"variableId": "fixed"}, [])
    else:
        assert "fixed" not in choices
        with pytest.raises(VariableError, match="constants cannot be written"):
            window._applyEditorConfiguration(key, {"variableId": "fixed"}, [])
        assert session.payload() == before
    assert session.document().schemaVersion == "2.4"
    assert session.payload()["globalVariables"] == before["globalVariables"]


@pytest.mark.parametrize("operator", OPERATORS)
@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("blocked", ["running", "pages", "closed"])
def testVariableInsertionCannotBypassEditingGuard(designerApplication, monkeypatch, operator, entry, blocked):
    window = _window()
    session = window.pageCoordinator.session
    before = session.payload()
    positions = window.flowScene.getNodePositions()
    undoDepth = len(session._undo)
    with monkeypatch.context() as guard:
        if blocked == "running":
            guard.setattr(window, "isJobRunning", True)
        elif blocked == "pages":
            guard.setattr(window.pageCoordinator, "pageActive", lambda: True)
        else:
            guard.setattr(window.runtimeController, "_closed", True)
        _insert(window, operator, entry)
    assert session.payload() == before
    assert window.flowScene.getNodePositions() == positions
    assert len(session._undo) == undoDepth
    assert session.document().schemaVersion == "2.1"


@pytest.mark.parametrize("operator", OPERATORS)
def testRejectedMigrationDoesNotInsertOrDirtyDraft(designerApplication, monkeypatch, operator):
    window = _window()
    session = window.pageCoordinator.session
    before = session.payload()
    positions = window.flowScene.getNodePositions()
    undoDepth = len(session._undo)
    messages = []
    monkeypatch.setattr(window, "appendRuntimeLog", lambda level, text: messages.append((level, text)))

    def reject(store):
        raise ValueError("migration unavailable")

    monkeypatch.setattr(global_variables, "enableVariables", reject)
    _insert(window, operator, "drop")
    assert session.payload() == before
    assert window.flowScene.getNodePositions() == positions
    assert len(session._undo) == undoDepth
    assert session.document().schemaVersion == "2.1"
    assert not session.dirty
    assert any(level == "ERROR" and "migration unavailable" in text for level, text in messages)
    assert "migration unavailable" in window.statusBar().currentMessage()


def testOrdinaryInsertionAndLoadDoNotUpgradeLegacyProject(designerApplication, tmp_path):
    window = _window()
    window.addNodeFromOperatorDrop({"operatorId": "test.ordinary", "displayName": "Ordinary"}, 100., 120.)
    assert window.pageCoordinator.session.document().schemaVersion == "2.1"
    path = tmp_path / "legacy.emoproj"
    assert window.saveProjectToDirectory(str(path))
    reopened = _window()
    assert reopened.loadProjectDirectory(str(path))
    assert reopened.pageCoordinator.session.document().schemaVersion == "2.1"
    assert "globalVariables" not in reopened.pageCoordinator.session.payload()
