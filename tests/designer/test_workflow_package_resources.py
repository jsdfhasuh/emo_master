import base64
from copy import deepcopy
import hashlib

import pytest

from emo_master.apps.designer.state.workflow_package import (
    buildWorkflowPackage, importWorkflowPackage, loadWorkflowPackage,
    previewWorkflowPackageImport, writeWorkflowPackage,
)
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject


@pytest.fixture(autouse=True)
def stableSerializationClock(monkeypatch):
    # Payload equality must detect edits, not toPayload's per-call timestamp.
    monkeypatch.setattr("emo_master.apps.designer.state.workflow_store.utc_now_iso",
                        lambda: "2026-10-18T00:00:00Z")


def source(tmp_path):
    payload = simulatedProject()
    payload["devices"]["definitions"]["camera"]["defaults"]["modelPath"] = "PRIVATE/model.bin"
    workflow = payload["workflows"]["main"]
    workflow["nodes"].append(dict(nodeId="file", kind="operator", operatorId="vision.io.image_loader",
        params=dict(imagePath="PRIVATE/model.bin", output="PRIVATE/output")))
    data = b"workflow-model-bytes"
    (tmp_path / "model.bin").write_bytes(data)
    payload["resources"] = dict(items={"model": dict(path="model.bin", size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), purpose="model")}, parameterBindings=[
        dict(resourceId="model", target=dict(deviceId="camera", section="defaults", parameterPath=["modelPath"])),
        dict(resourceId="model", target=dict(workflowId="main", nodeId="file", parameterPath=["imagePath"]))],
        siteBindings=[dict(field="serial", purpose="camera_serial", target=dict(
            deviceId="camera", section="connection", parameterPath=["serial"])),
            dict(field="output", purpose="output_file", target=dict(
                workflowId="main", nodeId="file", parameterPath=["output"]))])
    return WorkflowStore(payload), data


def testPortableClosureRemapsBytesDevicesNodesAndSiteFields(tmp_path):
    store, data = source(tmp_path)
    before = store.toPayload()
    package = buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    assert package.schemaVersion == "1.3" and base64.b64decode(package.resourceData["model"]) == data
    assert package.devices["camera"].connection["serial"] == ""
    assert package.devices["camera"].defaults["modelPath"] == "model.bin"
    assert "PRIVATE" not in str(package.toPayload())
    path = writeWorkflowPackage(tmp_path / "workflow", package)
    package = loadWorkflowPackage(path)
    assert store.toPayload() == before
    target = WorkflowStore()
    root = tmp_path / "destination"
    root.mkdir()
    preview = previewWorkflowPackageImport(target, package)
    assert len(preview.resourceDependencies) == 3
    assert preview.devices[0].compatibleTargets == ()
    result = importWorkflowPackage(target, package, targetRoot=root, deviceMapping={"camera": None},
        expectedFingerprint=preview.packageFingerprint, expectedTargetFingerprint=preview.targetFingerprint)
    document = ProjectDocument.model_validate(target.toPayload())
    plan = document.resources
    assert len(plan.items) == 1 and "model" not in plan.items
    item = next(iter(plan.items.values()))
    assert (root / item.path).read_bytes() == data
    assert document.devices.definitions[result.deviceIdMap["camera"]].defaults["modelPath"] == item.path
    assert not document.devices.definitions[result.deviceIdMap["camera"]].enabled
    assert {b.field for b in plan.siteBindings}.isdisjoint({"serial", "output"})
    nodeBinding = next(b for b in plan.parameterBindings if hasattr(b.target, "workflowId"))
    assert nodeBinding.target.workflowId == result.rootWorkflowId != "main"
    assert nodeBinding.target.nodeId == "file"
    assert store.toPayload() == before


def testInProjectCopyRetainsBytesAndDeviceBindingsWithoutFilesystemWrites(tmp_path):
    store, data = source(tmp_path)
    package = buildWorkflowPackage(store, "main", forInProjectCopy=True)
    assert not package.resourceData
    result = importWorkflowPackage(store, package, reuseVariableIds=True, deviceMapping={"camera": "camera"})
    resources = store.projectExtensions["resources"]
    assert list(resources["items"]) == ["model"]
    assert len(resources["parameterBindings"]) == 3 and len(resources["siteBindings"]) == 3
    assert result.deviceIdMap == {"camera": "camera"}
    assert (tmp_path / "model.bin").read_bytes() == data
    assert len(list(tmp_path.iterdir())) == 1
    with pytest.raises(ValueError):
        writeWorkflowPackage(tmp_path / "reference-only", package)


@pytest.mark.parametrize("changed", ["digest", "missing-bytes", "site-value", "target", "resource-path"])
def testMalformedDependenciesFailBeforeDestinationMutation(tmp_path, changed):
    store, _ = source(tmp_path)
    package = buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    if changed == "digest":
        package.resources.items["model"].sha256 = "0" * 64
    elif changed == "missing-bytes":
        package.resourceData.clear()
    elif changed == "site-value":
        package.devices["camera"].connection["serial"] = "OLD-SITE"
    elif changed == "target":
        package.resources.parameterBindings[1].target.nodeId = "missing"
    else:
        package.resources.items["model"].path = "../escape"
    target = WorkflowStore()
    before, files = target.toPayload(), set(tmp_path.iterdir())
    with pytest.raises((ValueError, KeyError)):
        importWorkflowPackage(target, package, targetRoot=tmp_path, deviceMapping={"camera": None})
    assert target.toPayload() == before and set(tmp_path.iterdir()) == files


def testFailedInstallationRemovesOnlyNewFilesAndNeverUpdatesDraft(tmp_path, monkeypatch):
    from emo_master.apps.designer.state import workflow_package_resources as module
    store, _ = source(tmp_path)
    package = buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    target = WorkflowStore()
    before, files = target.toPayload(), set(tmp_path.iterdir())
    def fail(*args):
        raise ValueError("injected after resource installation")
    monkeypatch.setattr(module, "applyImportedPaths", fail)
    with pytest.raises(ValueError, match="injected"):
        importWorkflowPackage(target, package, targetRoot=tmp_path, deviceMapping={"camera": None})
    assert target.toPayload() == before and set(tmp_path.iterdir()) == files


def testRootAndDigestRequiredAndResourceBudgetIsBounded(tmp_path, monkeypatch):
    from emo_master.apps.designer.state import workflow_package_resources as module
    store, _ = source(tmp_path)
    with pytest.raises(ValueError, match="save the source"):
        buildWorkflowPackage(store, "main")
    package = buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    target = WorkflowStore()
    before = target.toPayload()
    with pytest.raises(ValueError, match="save the destination"):
        importWorkflowPackage(target, package, deviceMapping={"camera": None})
    assert target.toPayload() == before
    (tmp_path / "model.bin").write_bytes(b"different")
    with pytest.raises(ValueError, match="size"):
        buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    monkeypatch.setattr(module, "MAX_RESOURCE_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        previewWorkflowPackageImport(target, package)


def testDependentDevicesCannotOverwriteExistingSiteBindings(tmp_path):
    store, _ = source(tmp_path)
    package = buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    target = deepcopy(store)
    before = target.toPayload()
    with pytest.raises(ValueError, match="不能覆盖"):
        importWorkflowPackage(target, package, deviceMapping={"camera": "camera"}, targetRoot=tmp_path)
    assert target.toPayload() == before


@pytest.mark.parametrize('withDispatch', [False, True])
def testNativeImportPreviewConfirmationAndUndoRedoPreserveResourceBytes(tmp_path, ownedDesignerWindow, monkeypatch, withDispatch):
    from PySide2.QtCore import QTimer
    from PySide2.QtWidgets import QListWidget
    from emo_master.apps.designer.state.project_store import saveProject
    from emo_master.apps.designer.ui import main_window as module
    from emo_master.apps.designer.ui.workflow_package_preview_dialog import WorkflowPackagePreviewDialog
    from tests.designer.qt_wait import waitForCatalog
    from tests.designer.test_main_window_workflow_tabs import _RuntimeClientStub
    store, data = source(tmp_path)
    if withDispatch:
        from tests.runtime.test_frame_dispatch import dispatchProject
        payload = store.toPayload()
        graph = dispatchProject()
        payload['workflows']['consumer'] = graph['workflows']['consumer']
        payload['workflowOrder'].append('consumer')
        payload['workflows']['main']['nodes'].extend(n for n in graph['workflows']['main']['nodes']
                                                     if n.get('kind') in {'dispatch', 'join'})
        payload['workflows']['main']['edges'].extend(e for e in graph['workflows']['main']['edges']
                                                     if e['toNode'] in {'dispatch', 'join'})
        store = WorkflowStore(payload)
    package = buildWorkflowPackage(store, "main", sourceRoot=tmp_path)
    path = writeWorkflowPackage(tmp_path / "portable", package)
    root = tmp_path / "destination"
    root.mkdir()
    projectFile = root / "project.emoproj"
    saveProject(projectFile, simulatedProject())
    window = ownedDesignerWindow(_RuntimeClientStub())
    waitForCatalog(window)
    assert window.loadProjectDirectory(str(projectFile), confirmed=True)
    window.pageCoordinator.sync()
    session = window.pageCoordinator.session
    before, disk = session.payload(), projectFile.read_bytes()
    cancel = True
    def previewDialog(preview):
        dialog = WorkflowPackagePreviewDialog(preview, window)
        assert dialog.findChild(QListWidget, "workflowPackageResourceDependencies").count() == 3
        assert dialog._deviceCombos["camera"].count() == 2
        assert not dialog._importButton.isEnabled()
        dialog._deviceCombos["camera"].setCurrentIndex(1)
        assert dialog._importButton.isEnabled()
        dialog.setInsertSubflowChecked(False)
        QTimer.singleShot(10, dialog.reject if cancel else dialog._importButton.click)
        return dialog
    monkeypatch.setattr(window, "_createWorkflowPackagePreviewDialog", previewDialog)
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    assert window.importWorkflowPackageAction() is None
    assert session.payload() == before and projectFile.read_bytes() == disk
    cancel = False
    imported = window.importWorkflowPackageAction()
    assert imported and imported != "main"
    after = session.payload()
    item = next(iter(after["resources"]["items"].values()))
    resource = root / item["path"]
    assert resource.read_bytes() == data and projectFile.read_bytes() == disk
    window.pageCoordinator.history()
    assert session.payload() == before and resource.read_bytes() == data
    window.pageCoordinator.history(redo=True)
    assert session.payload() == after and resource.read_bytes() == data
    assert projectFile.read_bytes() == disk
    saveProject(projectFile, session.payload())
    assert window.loadProjectDirectory(str(projectFile), confirmed=True)
    assert resource.read_bytes() == data
    if withDispatch:
        from emo_master.core.workflow.dispatch_contracts import groupSlots
        reopened = ProjectDocument.model_validate(window.pageCoordinator.session.payload())
        assert len(groupSlots(reopened, imported)) == 2
