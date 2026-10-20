"""Normal Designer hydration keeps device drafts offline and lossless."""
from types import SimpleNamespace

from PySide2.QtCore import QSettings

from emo_master.apps.designer.state.project_store import saveProject, loadProject
from tests.core.project.test_devices import declaration, definition, project  # noqa: F401


def testDeviceDraftOpenCopySwitchSaveAndReopen(ownedDesignerWindow, tmp_path, definition):  # noqa: F811
    def forbidden(*args, **kwargs):
        raise AssertionError("device draft must not execute or load a Runtime project")

    payload = project(definition)
    source = tmp_path / "device.emoproj"
    saveProject(source, payload)
    runtime = SimpleNamespace(listOperators=lambda: [], loadProject=forbidden, startJob=forbidden,
                              releaseCommunicationForReplacement=forbidden)
    window = ownedDesignerWindow(runtime, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    assert window.loadProjectDirectory(str(source), confirmed=True)
    assert window.flowModel.nodes["capture"].deviceBindings == {"camera": {"deviceId": "camera"}}
    copiedId = window.workflowController.duplicateNode("capture")
    assert window.flowModel.nodes[copiedId].deviceBindings == window.flowModel.nodes["capture"].deviceBindings
    window.pageCoordinator.showPages()
    window.pageCoordinator.showFlow()
    window.pageCoordinator.sync()
    assert window.saveProjectToDirectory(str(source))
    saved = loadProject(source)
    assert saved["schemaVersion"] == "2.6"
    assert len(saved["devices"]["definitions"]) == 1
    assert window.loadProjectDirectory(str(source), confirmed=True)
    assert window.flowModel.nodes[copiedId].deviceBindings == {"camera": {"deviceId": "camera"}}
    assert window.workflowStore.devices["definitions"]["camera"] == definition.model_dump()
    savedAs = tmp_path / "copy.emoproj"
    assert window.saveProjectToDirectory(str(savedAs))
    assert loadProject(savedAs)["devices"] == saved["devices"]
