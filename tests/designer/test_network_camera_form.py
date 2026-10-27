from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QLineEdit
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.plugins.builtins.hikvision_audio.operator import HikvisionAudioOperator
from emo_master.plugins.builtins.rtsp_camera.operator import RtspCameraOperator
from tests.designer.test_main_window_project_save_load import RuntimeClientStub
from tests.runtime.network_camera_fixture import cameraProject
from tests.runtime.test_image_batch_while import _registry


@pytest.mark.parametrize("operator,urlKey,url", [
    (RtspCameraOperator, "rtspUrl", "rtsp://camera.invalid/live"),
    (HikvisionAudioOperator, "cameraUrl", "http://camera.invalid"),
])
def testPasswordIsMaskedAndRoundtrips(retainedQtApplication, operator, urlKey, url):
    form = SchemaParamForm()
    try:
        form.setSchema(operator.meta.paramSchema, {urlKey: url, "password": "fixture-secret"})
        assert form._controls["password"].echoMode() == QLineEdit.Password
        assert "fixture-secret" not in form._controls["password"].displayText()
        assert form.getValues()["password"] == "fixture-secret"
        form._controls["passwordEnv"].setText("CAMERA_PASSWORD")
        assert form.getValues()["passwordEnv"] == "CAMERA_PASSWORD"
        assert form.validationMessage() == ""
    finally:
        form.close()
        form.deleteLater()


def testAudioSourceKeepsBothSelections(retainedQtApplication):
    form = SchemaParamForm()
    try:
        form.setSchema(HikvisionAudioOperator.meta.paramSchema, {"cameraUrl": "http://camera.invalid", "audioId": 5})
        form.show()
        retainedQtApplication.processEvents()
        assert not form._controls["audioId"].isHidden()
        assert form._controls["customAudioId"].isHidden()
        control = form._controls["audioSource"]
        control.setCurrentIndex(control.findData("custom"))
        retainedQtApplication.processEvents()
        assert form._controls["audioId"].isHidden()
        assert not form._controls["customAudioId"].isHidden()
        form._controls["customAudioId"].setValue(2)
        values = form.getValues()
        assert values["audioId"] == 5 and values["customAudioId"] == 2
        control.setCurrentIndex(control.findData("builtin"))
        assert form.getValues()["audioId"] == 5
    finally:
        form.close()
        form.deleteLater()


def testDesignerOpenEditSaveReopenWithoutCameraIo(tmp_path, ownedDesignerWindow, retainedQtApplication, monkeypatch):
    from emo_master.plugins.builtins.rtsp_camera import operator as rtsp
    from emo_master.plugins.builtins.hikvision_audio import client as audio
    def forbidden(*args, **kwargs):
        pytest.fail("Opening or editing must not contact cameras")
    monkeypatch.setattr(rtsp.cv2, "VideoCapture", forbidden)
    monkeypatch.setattr(audio.request, "build_opener", forbidden)
    path = tmp_path / "network-camera.emoproj"
    saveProject(path, cameraProject(_registry()).model_dump(mode="json"))
    window = ownedDesignerWindow(RuntimeClientStub(), settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    assert window.loadProjectSelection(str(path))
    assert window.flowModel.nodes["camera"].paramSchema == RtspCameraOperator.meta.paramSchema
    assert window.flowModel.nodes["audio"].inputPorts == {"trigger": "boolean"}
    monkeypatch.setattr(window, "_requireOperatorCatalog", lambda: True)
    window.openNodeParamDialog("audio")
    editor = window.nodeParamDialog
    retainedQtApplication.processEvents()
    assert editor._schemaForm._controls["password"].echoMode() == QLineEdit.Password
    editor._schemaForm._controls["maxVolume"].setValue(25)
    params = editor.collectParams()
    window.flowModel.nodes["audio"].params = params
    assert window.saveProjectToDirectory(str(path))
    assert window.loadProjectSelection(str(path))
    document = loadProjectDocument(path)
    node = next(item for item in document.workflows["main"].nodes if item.nodeId == "audio")
    assert node.params["maxVolume"] == 25
    assert document.schemaVersion == "2.7"
