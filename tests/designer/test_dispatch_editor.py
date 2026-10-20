from copy import deepcopy

from emo_master.apps.designer.ui.dispatch_editor import DispatchEditor
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_frame_dispatch import dispatchProject


def testStructuredMappingAndCancelDoNotMutateDraft(designerApplication):
    payload = dispatchProject()
    before = deepcopy(payload)
    dialog = DispatchEditor(payload, "main", "dispatch", nodeId="dispatch")
    try:
        assert len(dialog.rows) == 2
        dialog.rows[0][4].item(0, 2).setText("leftImage")
        candidate = dialog.candidate()
        node = next(n for n in candidate["workflows"]["main"]["nodes"] if n["nodeId"] == "dispatch")
        assert node["inputPorts"] == {"leftImage": "image", "image": "image"}
        assert node["params"]["targets"][0]["inputs"] == {"image": "leftImage"}
        assert payload == before
        dialog.reject()
        assert dialog.resultPayload is None
    finally:
        dialog.close()


def testJoinEditorAndDependencyPreservation(designerApplication):
    payload = dispatchProject()
    dialog = DispatchEditor(payload, "main", "join", nodeId="join")
    try:
        candidate = dialog.candidate()
        ProjectDocument.model_validate(candidate)
        store = WorkflowStore(candidate)
        assert store.referencesTo("consumer") == ["main"]
        refs = store.getWorkflowDependencyReferences()
        assert len([r for r in refs if r["relation"] == "dispatch"]) == 2
        store.captureActiveGraph(store.get("main").graph(), {})
        restored = WorkflowStore(store.toPayload())
        node = next(n for n in restored.get("main").nodes if n["nodeId"] == "dispatch")
        assert node["params"]["targets"] == payload["workflows"]["main"]["nodes"][3]["params"]["targets"]
    finally:
        dialog.close()


def testMainWindowDispatchEntryUndoAndStaleDraftFence(ownedDesignerWindow, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide2.QtCore import QSettings
    from emo_master.apps.designer.state.project_store import saveProject
    window = ownedDesignerWindow(SimpleNamespace(listOperators=lambda: []),
        settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    payload = dispatchProject()
    flow = payload["workflows"]["main"]
    flow["nodes"] = [n for n in flow["nodes"] if n.get("kind") not in {"dispatch", "join"}]
    flow["edges"] = [edge for edge in flow["edges"] if edge["toNode"] not in {"dispatch", "join"}]
    path = tmp_path / "project.emoproj"
    saveProject(path, payload)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    window.pageCoordinator.sync()
    session = window.pageCoordinator.session
    before = session.payload()
    def accept(dialog):
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(DispatchEditor, "exec_", accept)
    nodeId = window._addSystemNodeFromLibrary("dispatch", 320, 240)
    assert nodeId and window.flowModel.nodes[nodeId].kind == "dispatch"
    assert session.workflows.get("main").layout["nodePositions"][nodeId] == dict(x=320.0, y=240.0)
    assert session.undo() and session.payload() == before
    window.workflowController._renderActive()
    def changeWhileOpen(dialog):
        with session.transaction():
            session.workflows.get("main").name = "Concurrent draft"
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(DispatchEditor, "exec_", changeWhileOpen)
    assert window.editDispatchNode("dispatch") is None
    assert session.workflows.get("main").name == "Concurrent draft"
    assert all(n["kind"] != "dispatch" for n in session.workflows.get("main").nodes)
