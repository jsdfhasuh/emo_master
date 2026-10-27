from PySide2.QtCore import QSettings

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.file_list.operator import FileListOperator
from tests.designer.test_main_window_project_save_load import RuntimeClientStub
from tests.runtime.test_image_batch_while import _registry
from tests.runtime.video_files_fixture import fileVideoProject


def testFileListFolderPicker(retainedQtApplication, monkeypatch):
    from PySide2.QtWidgets import QFileDialog, QPushButton
    form = SchemaParamForm()
    try:
        form.setSchema(FileListOperator.meta.paramSchema, {"folderPath": "C:/videos", "patterns": ["*.mp4"]})
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "D:/测试视频")
        form._controls["folderPath"].findChild(QPushButton).click()
        assert form.getValues() == {"folderPath": "D:/测试视频", "patterns": ["*.mp4"], "recursive": False}
    finally:
        form.close()
        form.deleteLater()


def testOpenVideoEditorTracksConnectAndDisconnectWithoutLosingDraft(tmp_path, ownedDesignerWindow, retainedQtApplication, monkeypatch):
    document = fileVideoProject(_registry())
    path = tmp_path / "files.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    window = ownedDesignerWindow(RuntimeClientStub(), settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    assert window.loadProjectSelection(str(path))
    window.activateWorkflow("frame")
    monkeypatch.setattr(window, "_requireOperatorCatalog", lambda: True)
    window.openNodeParamDialog("load")
    editor = window.nodeParamDialog
    retainedQtApplication.processEvents()
    editor._schemaForm._controls["intervalSec"].setValue(.5)
    assert not editor._schemaForm._controls["videoPath"].isEnabled()
    monkeypatch.setattr(window.flowScene, "getSelectedNodeIds", lambda: [])
    monkeypatch.setattr(window.flowScene, "getSelectedEdgeKeys", lambda: [("input", "videoPath", "load", "videoPath")])
    window.deleteSelectedElements()
    retainedQtApplication.processEvents()
    assert editor._schemaForm._controls["videoPath"].isEnabled()
    assert editor.collectParams()["videoPath"] == "old-missing-draft.mp4"
    assert editor.collectParams()["intervalSec"] == .5
    assert window.connectPorts("input", "videoPath", "load", "videoPath") is not None
    retainedQtApplication.processEvents()
    assert not editor._schemaForm._controls["videoPath"].isEnabled()
    assert editor.collectParams()["intervalSec"] == .5


def testConnectedVideoPathPreservesDraftThroughSaveReopenAndUnbind(tmp_path, ownedDesignerWindow):
    document = fileVideoProject(_registry())
    path = tmp_path / "files.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    window = ownedDesignerWindow(RuntimeClientStub(), settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    assert window.loadProjectSelection(str(path))
    window.activateWorkflow("frame")
    node = window.flowModel.nodes["load"]
    assert "videoPath" in node.inputPorts
    form = SchemaParamForm()
    try:
        form.setSchema(window._nodeEditorSchema(node), node.params)
        assert not form._controls["videoPath"].isEnabled()
        assert form.getValues()["videoPath"] == "old-missing-draft.mp4"
        assert form.validationMessage() == ""
        assert window.saveProjectToDirectory(str(path))
        assert window.loadProjectSelection(str(path))
        window.activateWorkflow("frame")
        node = window.flowModel.nodes["load"]
        assert node.params["videoPath"] == "old-missing-draft.mp4"
        window.flowModel.edges = [edge for edge in window.flowModel.edges
                                 if not (edge.toNode == "load" and edge.toPort == "videoPath")]
        form.setSchema(window._nodeEditorSchema(node), node.params)
        assert form._controls["videoPath"].isEnabled()
        assert form.getValues()["videoPath"] == "old-missing-draft.mp4"
        assert loadProjectDocument(path).schemaVersion == "2.7"
    finally:
        form.close()
        form.deleteLater()
