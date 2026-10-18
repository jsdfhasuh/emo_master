from copy import deepcopy
import json
from types import SimpleNamespace

from PySide2.QtCore import QSettings

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.online_graph import OnlineGraphDialog
from emo_master.apps.designer.ui.resource_bindings import ResourceBindingsDialog
from emo_master.apps.designer.ui.device_migration import DeviceMigrationDialog
from emo_master.apps.designer.ui.dispatch_editor import DispatchEditor
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_frame_dispatch import dispatchProject
from tests.designer.test_device_workspace import waitUntil


def testOnlineGraphIsIndependentReadOnlyAndRenders(retainedQtApplication, tmp_path):
    source = tmp_path / "project.json"
    source.write_text(json.dumps(dispatchProject()), encoding="utf-8")
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(source)), None).ok
    def snapshot(projectId, revision, job):
        reply = runtime.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(project_id=projectId,
            document_revision=revision, job_id=job), None)
        assert reply.ok, reply.message
        return json.loads(reply.result_json)
    dialog = OnlineGraphDialog(SimpleNamespace(getOnlineGraphSnapshot=snapshot), runtime.loadedProjectId)
    before = deepcopy(runtime.loadedPayload)
    try:
        dialog.show()
        waitUntil(lambda: dialog.pending is None)
        assert dialog.data is not None, dialog.status.text()
        assert not dialog.view.isInteractive() and not dialog.view.acceptDrops()
        assert dialog.details.isReadOnly()
        assert len(dialog.scene._nodeItems) == 5
        assert len(dialog.scene._edgeItems) == 3
        assert dialog.scene._wirePaths
        original = dialog.scene._nodeItems["dispatch"]
        dialog.view.scale(1.5, 1.5)
        transform = dialog.view.transform()
        dialog.render()
        assert dialog.scene._nodeItems["dispatch"] is original
        assert dialog.view.transform() == transform
        dialog.workflow.setCurrentIndex(1)
        dialog.workflow.setCurrentIndex(0)
        assert runtime.loadedPayload == before
        assert dialog.grab().save(str(tmp_path / "online-history.png"))
    finally:
        dialog.reject()
        runtime.close()


def testDeviceSiteBindingMigrationInventoryAndUndo(ownedDesignerWindow, tmp_path):
    payload = simulatedProject()
    from emo_master.core.project.device_resources import trustedDeviceTypes
    types = trustedDeviceTypes()
    requirements = {"vision.io.simulated_camera": {"camera": {"deviceTypeId": "device.simulated.camera"}}}
    client = SimpleNamespace(listOperators=lambda: [], listDeviceTypes=lambda: (types, requirements, {}))
    window = ownedDesignerWindow(client, settingsStore=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    source = tmp_path / "project.emoproj"
    saveProject(source, payload)
    assert window.loadProjectDirectory(str(source), confirmed=True)
    c = window.pageCoordinator
    c.sync()
    before = c.session.payload()
    disk = source.read_bytes()
    dialog = ResourceBindingsDialog(c, types, {}, window)
    dialog.show()
    row = next(row for row in dialog.rows if row[0].get("deviceId") == "camera")
    row[2].setCurrentIndex(1)
    row[4].setText("camera.serial")
    row[5].setText("SITE-SERIAL")
    row[6].setChecked(True)
    assert dialog.grab().save(str(tmp_path / "resource-site-bindings.png"))
    dialog.accept()
    assert dialog.result() == dialog.Accepted, dialog.error.text()
    assert c.session.document().devices.definitions["camera"].connection["serial"] == "SITE-SERIAL"
    assert c.session.document().resources.siteBindings[0].target.deviceId == "camera"
    assert source.read_bytes() == disk
    assert c.session.undo()
    assert c.session.payload() == before
    migration = DeviceMigrationDialog(c, requirements, window)
    migration.show()
    assert len(migration.rows) == 1 and migration.table.item(0, 2).text()
    assert migration.grab().save(str(tmp_path / "migration-inventory.png"))
    migration.reject()


def testDispatchEditorFitsAndMapsTwoConsumers(retainedQtApplication, tmp_path):
    dialog = DispatchEditor(dispatchProject(), "main", "dispatch", nodeId="dispatch")
    try:
        dialog.resize(720, 480)
        dialog.show()
        retainedQtApplication.processEvents()
        assert dialog.tabs.count() == 2
        assert dialog.grab().save(str(tmp_path / "dispatch-editor.png"))
        candidate = dialog.candidate()
        assert len(next(n for n in candidate["workflows"]["main"]["nodes"] if n["kind"] == "dispatch")["params"]["targets"]) == 2
    finally:
        dialog.reject()
