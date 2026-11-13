"""Native Qt automation of a project delivered by the new public service.

This is automated Designer-entry acceptance, not a claim of manual UI or device
acceptance. The only Runtime is isolated, and the saved camera is a disabled
built-in simulated definition; the test never connects any device or runs a Job.
"""
from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path
from shutil import copytree
import zipfile

import httpx
from PIL import Image

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import loadProjectDocument
from emo_master.authoring.http import create_app
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.snapshots import verifyResources
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.authoring.test_transport import HEADERS, TOKEN, real_target, running_http  # noqa: F401
from tests.designer.qt_wait import waitForCatalog


def test_http_export_opens_edits_saves_and_compiles_in_native_designer(
        tmp_path, real_target, ownedDesignerWindow, retainedQtApplication, isolatedDesignerSettings,  # noqa: F811
        record_property):
    target, runtime = real_target
    app = create_app(tmp_path / "authoring", TOKEN, {"isolated": target}, enable_mcp=False)

    with running_http(app) as base, httpx.Client(base_url=base, headers=HEADERS, timeout=15) as http:
        created = http.post("/api/project-drafts", json={"name": "HTTP to Designer"})
        assert created.status_code == 200
        state = created.json()
        draft_path = "/api/project-drafts/" + state["draftId"]

        def edit(command):
            nonlocal state
            response = http.post(draft_path + "/commands", json={
                "expectedDraftRevision": state["draftRevision"], "command": command})
            assert response.status_code == 200, response.text
            state = response.json()
            return state

        for operator_id, node_id in [("vision.io.image_loader", "loader"),
                                     ("vision.preprocess.threshold", "threshold")]:
            details = http.get(f"/api/project-catalog/isolated/operators/{operator_id}")
            assert details.status_code == 200
            params = {key: deepcopy(spec["default"])
                      for key, spec in details.json()["paramSchema"]["properties"].items() if "default" in spec}
            edit({"type": "node.create", "workflowId": "main", "targetId": "isolated",
                  "operatorId": operator_id, "nodeId": node_id, "params": params})
        edit({"type": "workflow.interface", "workflowId": "main", "inputs": {}, "outputs": {"mask": "image"}})
        output_node_id = next(node["nodeId"] for node in state["project"]["workflows"]["main"]["nodes"]
                              if node["kind"] == "workflow_output")
        edit({"type": "edge.connect", "workflowId": "main", "fromNode": "loader", "fromPort": "image",
              "toNode": "threshold", "toPort": "image"})
        edit({"type": "edge.connect", "workflowId": "main", "fromNode": "threshold", "fromPort": "mask",
              "toNode": output_node_id, "toPort": "mask"})
        encoded = io.BytesIO()
        Image.new("L", (16, 12), 180).save(encoded, format="PNG")
        image_bytes = encoded.getvalue()
        upload = http.post(draft_path + "/resources", params={"name": "input.png", "purpose": "input_image"},
                           headers={"Content-Type": "application/octet-stream",
                                    "X-Expected-Draft-Revision": state["draftRevision"],
                                    "X-Content-SHA256": hashlib.sha256(image_bytes).hexdigest()}, content=image_bytes)
        assert upload.status_code == 200, upload.text
        uploaded = upload.json()
        state["draftRevision"] = uploaded["draftRevision"]
        edit({"type": "resource.bind", "resourceId": uploaded["resourceId"],
              "target": {"workflowId": "main", "nodeId": "loader", "parameterPath": ["imagePath"]}})
        edit({"type": "page.create", "name": "Threshold mask"})
        page_id = state["createdIds"]["pageId"]
        edit({"type": "component.put", "pageId": page_id,
              "component": {"componentId": "mask-image", "type": "image", "props": {"title": "Mask"}}})
        presentation = deepcopy(state["project"]["presentation"])
        presentation["resultScopes"] = {"main-result": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}}
        presentation["pages"][page_id]["resultScopeIds"] = ["main-result"]
        presentation["pages"][page_id]["targets"] = ["desktop", "web"]
        edit({"type": "presentation.configure", "presentation": presentation})
        edit({"type": "component.bind", "pageId": page_id, "componentId": "mask-image", "property": "image",
              "source": {"kind": "node_output", "resultScopeId": "main-result", "workflowId": "main",
                         "nodeId": "threshold", "port": "mask", "expectedType": "image"}})
        device = http.get("/api/project-catalog/isolated/device-types/device.simulated.camera")
        assert device.status_code == 200
        declaration = device.json()["declaration"]
        def default_values(schema):
            return {key: deepcopy(spec["default"])
                    for key, spec in schema["properties"].items() if "default" in spec}
        policies = default_values(declaration["policiesSchema"])
        policies["connect"] = "manual"
        edit({"type": "device.put", "targetId": "isolated", "deviceId": "offline-simulated-camera",
              "definition": {"name": "Disabled simulated fixture", "deviceTypeId": declaration["deviceTypeId"],
                             "driverVersion": declaration["version"], "enabled": False,
                             "connection": default_values(declaration["connectionSchema"]),
                             "defaults": default_values(declaration["defaultsSchema"]), "policies": policies}})
        edit({"type": "variables.configure", "definitions": {
            "inspection-count": {"name": "Inspection count", "type": "integer", "initialValue": 0}}})
        saved = http.post(draft_path + "/save", json={"expectedDraftRevision": state["draftRevision"]})
        assert saved.status_code == 200, saved.text
        state = saved.json()
        exported = http.post(draft_path + "/exports", json={"expectedDraftRevision": state["draftRevision"],
                                                             "format": "project-zip"})
        assert exported.status_code == 200, exported.text
        metadata = exported.json()
        downloaded = http.get(metadata["download"]["path"])
        assert downloaded.status_code == 200
        assert len(downloaded.content) == metadata["sizeBytes"]
        assert hashlib.sha256(downloaded.content).hexdigest() == metadata["sha256"]

    destination = tmp_path / "downloaded"
    destination.mkdir()
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        archive.extractall(destination)
    project_path = destination / "project.emoproj"
    original = loadProjectDocument(project_path)
    assert original.schemaVersion == "2.9" and original.presentation.schemaVersion == "1.2"
    assert verifyResources(original, destination)[uploaded["resourceId"]]

    from emo_master.apps.designer.ui.main_window import _nativeQt
    assert _nativeQt, "This acceptance must exercise real PySide2, not the non-Qt shim"
    record_property("qt_platform", retainedQtApplication.platformName())
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    window.show()
    retainedQtApplication.processEvents()
    assert window.isVisible()
    assert window.loadProjectSelection(str(project_path)), window.projectController.lastLoadError
    assert window.projectController.currentProjectFile == project_path
    assert "threshold" in window.flowModel.nodes
    assert window.pageCoordinator.session.document().resources == original.resources
    assert window.renameNode("threshold", "Designer edited threshold") == "Designer edited threshold"
    window.renameWorkflow("main", "Designer edited workflow")
    window.pageCoordinator.showPages()
    retainedQtApplication.processEvents()
    window.pageCoordinator.showFlow()
    window.saveProjectAction()
    retainedQtApplication.processEvents()
    assert not window.pageCoordinator.session.dirty

    edited = loadProjectDocument(project_path)
    assert edited.schemaVersion == "2.9" and edited.presentation.schemaVersion == "1.2"
    assert edited.project.projectId == original.project.projectId
    assert edited.workflows["main"].name == "Designer edited workflow"
    assert next(node.displayName for node in edited.workflows["main"].nodes if node.nodeId == "threshold") == "Designer edited threshold"
    assert edited.resources == original.resources
    assert edited.devices == original.devices
    assert edited.presentation == original.presentation
    assert edited.globalVariables == original.globalVariables
    assert edited.production == original.production
    assert verifyResources(edited, destination)[uploaded["resourceId"]]
    assert window.loadProjectSelection(str(project_path)), window.projectController.lastLoadError
    assert window.flowModel.nodes["threshold"].displayName == "Designer edited threshold"
    prepared = prepareRuntimeProject(edited, destination, runtime.pluginScanResult.activeOperators,
                                     deviceTypes=runtime.pluginScanResult.deviceTypes)
    compiled = WorkflowCompiler(operatorRegistry=runtime.pluginScanResult.activeOperators,
                                deviceTypes=runtime.pluginScanResult.deviceTypes).compile(
                                    prepared, pluginRootPaths=runtime.pluginRootPaths)
    assert compiled is not None
    assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    screenshot = tmp_path / "designer-handoff-native.png"
    assert window.grab().save(str(screenshot))
    record_property("native_screenshot", str(screenshot))


def test_external_inspector_download_sample_continues_in_native_designer(
        tmp_path, real_target, ownedDesignerWindow, retainedQtApplication, isolatedDesignerSettings,  # noqa: F811
        record_property):
    """Open the exact external-client delivery, separately from the HTTP fixture."""
    _, runtime = real_target
    default_source = Path(__file__).resolve().parents[2] / "examples" / "independent_project_authoring"
    # Repair acceptance can point at the bytes downloaded in that same run.
    # The test still edits only its temporary copy, including for local inputs.
    source = Path(os.environ.get("EMO_AUTHORING_HANDOFF_PROJECT_DIR", str(default_source)))
    destination = tmp_path / "inspector-delivery"
    copytree(source, destination)
    project_path = destination / "project.emoproj"
    original = loadProjectDocument(project_path)
    assert original.schemaVersion == "2.9" and original.presentation.schemaVersion == "1.2"
    assert original.devices.schemaVersion == "1.0"
    assert verifyResources(original, destination)

    from emo_master.apps.designer.ui.main_window import _nativeQt
    assert _nativeQt
    window = ownedDesignerWindow(RuntimeClient(runtime))
    waitForCatalog(window)
    window.show()
    retainedQtApplication.processEvents()
    assert window.loadProjectSelection(str(project_path)), window.projectController.lastLoadError
    assert window.renameNode("threshold", "Inspector delivery edited in Designer") == "Inspector delivery edited in Designer"
    window.renameWorkflow("main", "Inspector delivery continued")
    window.saveProjectAction()
    retainedQtApplication.processEvents()
    assert not window.pageCoordinator.session.dirty
    edited = loadProjectDocument(project_path)
    assert edited.project.projectId == original.project.projectId
    assert edited.resources == original.resources
    assert edited.presentation == original.presentation
    assert edited.devices == original.devices
    assert edited.globalVariables == original.globalVariables
    assert edited.production == original.production
    assert verifyResources(edited, destination)
    assert window.loadProjectSelection(str(project_path)), window.projectController.lastLoadError
    assert window.flowModel.nodes["threshold"].displayName == "Inspector delivery edited in Designer"
    prepared = prepareRuntimeProject(edited, destination, runtime.pluginScanResult.activeOperators,
                                     deviceTypes=runtime.pluginScanResult.deviceTypes)
    assert WorkflowCompiler(operatorRegistry=runtime.pluginScanResult.activeOperators,
                            deviceTypes=runtime.pluginScanResult.deviceTypes).compile(
                                prepared, pluginRootPaths=runtime.pluginRootPaths) is not None
    assert runtime.loadedDocument is None and runtime.jobRepository.all() == []
    screenshot = tmp_path / "inspector-designer-native.png"
    assert window.grab().save(str(screenshot))
    record_property("qt_platform", retainedQtApplication.platformName())
    record_property("projectId", original.project.projectId)
    record_property("source_project", str(source.resolve()))
    record_property("native_screenshot", str(screenshot))
