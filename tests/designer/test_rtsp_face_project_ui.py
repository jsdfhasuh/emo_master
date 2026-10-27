import os
from pathlib import Path
import shutil

import pytest
from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QLineEdit

from emo_master.apps.designer.state.project_store import loadProjectDocument
from tests.designer.test_main_window_project_save_load import RuntimeClientStub
from tests.runtime.test_rtsp_face_project import fixtureProject


def testFaceProjectOpensSavesReopensWithMaskedCredential(tmp_path, ownedDesignerWindow, retainedQtApplication, monkeypatch):
    path, document, _, _ = fixtureProject(tmp_path)
    window = ownedDesignerWindow(RuntimeClientStub(), settingsStore=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    assert window.loadProjectSelection(str(path))
    assert window.flowModel.nodes["camera_loop"].loop["unlimited"] is True
    window.activateWorkflow("camera_once")
    assert window.flowModel.nodes["orient"].params["mode"] == "both"
    assert window.flowModel.nodes["detect"].targetWorkflowId == "detect"
    monkeypatch.setattr(window, "_requireOperatorCatalog", lambda: True)
    window.openNodeParamDialog("source")
    retainedQtApplication.processEvents()
    control = window.nodeParamDialog._schemaForm._controls["password"]
    assert control.echoMode() == QLineEdit.Password
    assert "fixture-only" not in control.displayText()
    assert window.saveProjectToDirectory(str(path))
    assert window.loadProjectSelection(str(path))
    reloaded = loadProjectDocument(path)
    assert reloaded.schemaVersion == "2.7"
    assert reloaded.resources == document.resources
    assert reloaded.presentation == document.presentation
    assert next(n for n in reloaded.workflows["camera_once"].nodes if n.nodeId == "source").params["password"] == "fixture-only"


@pytest.mark.skipif(not os.environ.get("EMO_FACE_ACCEPTANCE_PROJECT"), reason="explicit local bundle path required")
def testActualDeliveryOpensInNativeDesigner(tmp_path, retainedQtApplication):
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from tests.qt_widget_owner import OwnedDesignerWindows
    path = Path(os.environ["EMO_FACE_ACCEPTANCE_PROJECT"])
    document = loadProjectDocument(path)
    # Copy only declared resources for save/reopen, never overwrite the delivery.
    for resource in document.resources.items.values():
        destination = tmp_path / resource.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path.parent / resource.path, destination)
    service = RuntimeService()
    client = RuntimeClient(runtimeService=service, ownedRuntimeService=service)
    try:
        with OwnedDesignerWindows(retainedQtApplication) as owner:
            window = owner(client, settingsStore=QSettings(str(tmp_path / "actual-ui.ini"), QSettings.IniFormat))
            assert window.loadProjectSelection(str(path))
            assert window.flowModel.nodes["camera_loop"].loop["unlimited"] is True
            window.activateWorkflow("camera_once")
            assert window.flowModel.nodes["orient"].params["mode"] == "both"
            window.show()
            window.resize(1440, 900)
            retainedQtApplication.processEvents()
            assert window.grab().save(str(path.parent / "verification/designer-while-offscreen.png"))
            saved = tmp_path / path.name
            assert window.saveProjectToDirectory(str(saved))
            assert window.loadProjectSelection(str(saved))
            reloaded = loadProjectDocument(saved)
            assert reloaded.resources == document.resources
            assert reloaded.presentation == document.presentation
            for wid in document.workflows:
                old = {n.nodeId: n.params for n in document.workflows[wid].nodes}
                new = {n.nodeId: n.params for n in reloaded.workflows[wid].nodes}
                assert old == new
    finally:
        client.close()
