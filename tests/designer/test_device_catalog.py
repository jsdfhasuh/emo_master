from copy import deepcopy
import json

import pytest

from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor, changeParameterSource
from emo_master.apps.designer.state.flow_graph_model import FlowGraphModel
from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument
from tests.core.project.test_devices import declaration, definition, project  # noqa: F401


@pytest.fixture
def editor(declaration):  # noqa: F811
    session = ProjectEditSession(WorkflowStore().toPayload(), enablePresentation=False)
    return DeviceCatalogEditor(session, {declaration.deviceTypeId: declaration}, {
        "vision.io.huaray_camera": {"camera": {"deviceTypeId": declaration.deviceTypeId}}})


def node():
    return {"nodeId": "capture", "operatorId": "vision.io.huaray_camera",
            "inputPorts": {"gain": "number"}, "deviceBindings": {"camera": {"deviceId": "camera"}}}


def testWizardCommitAndUndoAreSingleTransaction(editor, definition):  # noqa: F811
    session = editor.session
    original = session.payload()
    candidate = changeParameterSource(node(), "camera", "exposureUs", source="constant", value=8000.0)
    editor.commitNode("main", candidate, newDevice=definition.model_dump(), deviceId="camera")
    assert len(session._undo) == 1 and session.dirty
    assert editor.references("camera") == [("main", "capture", "camera")]
    saved = session.payload()
    assert session.undo() and session.payload() == original
    assert session.redo() and session.payload() == saved
    assert session.document().schemaVersion == "2.6"


def testCancelledDraftHasNoSideEffects(editor, definition):  # noqa: F811
    before = editor.session.payload()
    draft = changeParameterSource(node(), "camera", "gain", source="constant", value=0)
    draft["deviceOverrides"]["camera"]["gain"] = 2
    assert editor.session.payload() == before
    assert not editor.session.dirty and not editor.session._undo


def testFailedWizardRollsBackDeviceUpgradeAndNode(editor, definition):  # noqa: F811
    before = editor.session.payload()
    candidate = changeParameterSource(node(), "camera", "exposureUs", source="constant", value=-1)
    with pytest.raises(ValueError):
        editor.commitNode("main", candidate, newDevice=definition.model_dump(), deviceId="camera")
    assert editor.session.payload() == before
    assert not editor.session._undo and not editor.session.dirty


def testDefaultsChangeAffectsOnlyInheritedNode(editor, definition):  # noqa: F811
    editor.commitNode("main", node(), newDevice=definition.model_dump(), deviceId="camera")
    second = changeParameterSource(node(), "camera", "exposureUs", source="constant", value=8000.0)
    second["nodeId"] = "other"
    editor.commitNode("main", second)
    updated = definition.model_dump()
    updated["defaults"]["exposureUs"] = 10000
    editor.put(updated, deviceId="camera")
    doc = editor.session.document()
    first, second = doc.workflows["main"].nodes[-2:]
    device = doc.devices.definitions["camera"]
    deviceType = editor.deviceTypes[device.deviceTypeId]
    assert deviceType.validateNodeSources(device, first.deviceOverrides.get("camera", {}), {})["exposureUs"] == 10000
    assert deviceType.validateNodeSources(device, second.deviceOverrides["camera"], {})["exposureUs"] == 8000
    assert second.deviceOverrides["camera"]["exposureUs"] == 8000


def testSourceSwitchRemovesPreviousRecord():
    candidate = changeParameterSource(node(), "camera", "gain", source="constant", value=0)
    assert candidate["deviceOverrides"]["camera"]["gain"] == 0
    candidate = changeParameterSource(candidate, "camera", "gain", source="input", value="gain")
    assert "deviceOverrides" not in candidate
    assert candidate["deviceValueBindings"]["camera"]["gain"]["source"] == "input"
    candidate = changeParameterSource(candidate, "camera", "gain", source="inherit")
    assert "deviceOverrides" not in candidate and "deviceValueBindings" not in candidate


def testDeleteListsReferencesAndRenamePreservesId(editor, definition):  # noqa: F811
    editor.commitNode("main", node(), newDevice=definition.model_dump(), deviceId="camera")
    with pytest.raises(ValueError, match="capture"):
        editor.remove("camera")
    raw = definition.model_dump()
    raw["name"] = "Renamed camera"
    editor.put(raw, deviceId="camera")
    assert editor.references("camera") == [("main", "capture", "camera")]
    assert list(editor.session.workflows.devices["definitions"]) == ["camera"]


def testChangingDefaultRejectsInvalidExistingOverride(editor, definition):  # noqa: F811
    candidate = changeParameterSource(node(), "camera", "exposureUs", source="constant", value=12000)
    editor.commitNode("main", candidate, newDevice=definition.model_dump(), deviceId="camera")
    before = editor.session.payload()
    raw = definition.model_dump()
    raw["defaults"].pop("exposureUs")
    raw["defaults"]["exposureMode"] = "auto"
    with pytest.raises(ValueError, match="requires exposureMode"):
        editor.put(raw, deviceId="camera")
    assert editor.session.payload() == before


@pytest.mark.parametrize("case", ["type", "slot", "input", "variable"])
def testInvalidBindingCannotPublish(editor, definition, case):  # noqa: F811
    editor.put(definition.model_dump(), deviceId="camera")
    before = editor.session.payload()
    candidate = node()
    if case == "type":
        editor.operatorRequirements[candidate["operatorId"]]["camera"]["deviceTypeId"] = "device.other"
    elif case == "slot":
        candidate["deviceBindings"] = {"other": {"deviceId": "camera"}}
    else:
        candidate = changeParameterSource(candidate, "camera", "gain", source=case, value="missing")
    with pytest.raises(ValueError):
        editor.commitNode("main", candidate)
    assert editor.session.payload() == before


def testCanvasAndStoreRoundtripPreserveAllDeviceSources(definition):  # noqa: F811
    payload = project(definition)
    candidate = changeParameterSource(node(), "camera", "gain", source="input", value="gain")
    candidate = changeParameterSource(candidate, "camera", "exposureUs", source="constant", value=8000)
    payload["workflows"]["main"]["nodes"][-1] = candidate
    store = WorkflowStore(payload)
    model = FlowGraphModel()
    model.loadProjectGraph(store.graphFor())
    assert model.nodes["capture"].deviceBindings == candidate["deviceBindings"]
    store.captureActiveGraph(model.toProjectGraph(), {})
    result = WorkflowStore(store.toPayload()).toPayload()
    actual = next(n for n in result["workflows"]["main"]["nodes"] if n["nodeId"] == "capture")
    for key in ("deviceBindings", "deviceOverrides", "deviceValueBindings"):
        assert actual[key] == candidate[key]
    assert result["schemaVersion"] == "2.6"
    assert result["devices"] == store.devices


def testBoundVariableCannotBeDeleted(editor, definition):  # noqa: F811
    from emo_master.apps.designer.state.global_variables import editDefinitions
    editor.put(definition.model_dump(), deviceId="camera")
    editDefinitions(editor.session.workflows, {"g": {"name": "Gain", "type": "number", "initialValue": 0}})
    candidate = changeParameterSource(node(), "camera", "gain", source="variable", value="g")
    editor.commitNode("main", candidate)
    with pytest.raises(ValueError, match="引用"):
        editDefinitions(editor.session.workflows, {})


def testUnknownDeviceDataSurvivesStoreAndHistory(definition):  # noqa: F811
    payload = project(definition)
    payload["devices"]["definitions"]["camera"]["deviceTypeId"] = "missing.vendor"
    session = ProjectEditSession(payload, enablePresentation=False)
    before = deepcopy(session.workflows.devices)
    with session.transaction():
        session.workflows.project["name"] = "New name"
    assert session.undo() and session.workflows.devices == before
    assert session.redo() and session.workflows.devices == before
    assert ProjectDocument.model_validate(json.loads(json.dumps(session.payload()))).devices.definitions["camera"].deviceTypeId == "missing.vendor"


def testWorkflowExportIncludesDeviceDependencies(editor, definition):  # noqa: F811
    from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage
    editor.commitNode("main", node(), newDevice=definition.model_dump(), deviceId="camera")
    package = buildWorkflowPackage(editor.session.workflows, "main")
    assert package.schemaVersion == "1.2"
    assert package.devices == editor.session.document().devices.definitions


def testCompilerCannotFallBackToPrivateConnection(definition):  # noqa: F811
    from emo_master.core.workflow.compiler import WorkflowCompiler
    from emo_master.core.workflow.errors import WorkflowCompileError
    with pytest.raises(WorkflowCompileError) as caught:
        WorkflowCompiler().compile(project(definition))
    assert any(issue.code == "E_DEVICE_RUNTIME_UNAVAILABLE" for issue in caught.value.issues)
    payload = project(definition)
    payload["workflows"]["main"]["nodes"][-1].pop("deviceBindings")
    registry = {"vision.io.huaray_camera": {"deviceRequirements": {"camera": {"deviceTypeId": definition.deviceTypeId}}}}
    with pytest.raises(WorkflowCompileError) as caught:
        WorkflowCompiler(registry).compile(payload)
    assert any(issue.code == "E_DEVICE_RUNTIME_UNAVAILABLE" for issue in caught.value.issues)


def testSingleNodeDebugCannotStripDeviceBindings(definition):  # noqa: F811
    from emo_master.apps.runtime.operator_debug.contracts import draft, DebugError
    payload = project(definition)
    with pytest.raises(DebugError, match="managed device Runtime"):
        draft(json.dumps(payload), payload["project"]["projectId"], "main", "capture", "vision.io.huaray_camera", {})


def testSnapshotGateAndDeploymentMetadataGatePrecedeResourceWork(tmp_path, definition, monkeypatch):  # noqa: F811
    from emo_master.core.project import runtime_package
    from emo_master.core.project.snapshots import freezeProjectSnapshot
    document = ProjectDocument.model_validate(project(definition))
    monkeypatch.setattr(runtime_package, "prepareRuntimeProject", lambda *a, **k: pytest.fail("resource preparation"))
    with pytest.raises(ValueError, match="device metadata missing"):
        runtime_package._validate(document, tmp_path, {})
    with pytest.raises(ValueError, match="device resource mapping"):
        freezeProjectSnapshot(document, {}, mode="debug", resourceRoot=tmp_path, siteDataRoot=tmp_path)
