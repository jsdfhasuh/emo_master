from copy import deepcopy
from dataclasses import replace

import pytest

from emo_master.apps.designer.state.workflow_package import (
    WorkflowPackageDocument, buildWorkflowPackage, importWorkflowPackage,
    loadWorkflowPackage, previewWorkflowPackageImport, writeWorkflowPackage,
)
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from tests.core.project.test_devices import declaration, definition, project  # noqa: F401
from tests.designer.test_workflow_package import _packageSourcePayload


def sourceStore(definition):  # noqa: F811
    payload = migrateProjectPayload(_packageSourcePayload(), enableDevices=True)
    payload["devices"]["definitions"] = {
        "camera": definition.model_dump(), "unused": definition.model_dump()}
    node = payload["workflows"]["body"]["nodes"][2]
    node["deviceBindings"] = {"camera": {"deviceId": "camera"}}
    node["deviceOverrides"] = {"camera": {"gain": 0, "enabled": False}}
    node["deviceValueBindings"] = {"camera": {
        "exposureUs": {"source": "variable", "variableId": "exposure"}}}
    payload["globalVariables"]["exposure"] = {
        "name": "Exposure", "type": "number", "initialValue": 8000}
    return WorkflowStore(payload)


def testDeviceDependencyClosureAndUnknownDriverRoundtrip(definition, tmp_path):  # noqa: F811
    definition.deviceTypeId = "vendor.missing"
    definition.connection = {"opaque": [None, False, 0]}
    source = sourceStore(definition)
    before = deepcopy(vars(source))
    package = buildWorkflowPackage(source, "main")
    assert set(package.devices) == {"camera"}
    assert set(package.workflows) == {"main", "body", "condition"}
    assert set(package.globalVariables) == {"exposure"}
    path = writeWorkflowPackage(tmp_path / "devices", package)
    assert loadWorkflowPackage(path) == package
    assert vars(source) == before


def testImportNewDisabledDevicePreservesSourcesAndLegacyCatalog(definition):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    target = WorkflowStore()
    target.devices["bindings"] = {"old": {"opaque": False}}
    result = importWorkflowPackage(target, package, deviceMapping={"camera": None})
    document = ProjectDocument.model_validate(target.toPayload())
    assert document.schemaVersion == "2.7"
    assert document.devices.bindings == {"old": {"opaque": False}}
    targetId = result.deviceIdMap["camera"]
    assert targetId != "camera"
    assert document.devices.definitions[targetId] == definition.model_copy(update={"enabled": False})
    node = next(n for n in document.workflows[result.workflowIdMap["body"]].nodes if n.nodeId == "operator")
    original = next(n for n in package.workflows["body"].nodes if n.nodeId == "operator")
    assert node.deviceBindings["camera"].deviceId == targetId
    assert node.deviceOverrides == original.deviceOverrides
    assert node.deviceValueBindings == original.deviceValueBindings


@pytest.mark.parametrize("mapping", [None, {}, {"camera": "missing"}, {"other": None}])
def testUnresolvedOrStaleMappingIsAtomic(definition, mapping):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    target = WorkflowStore()
    before = deepcopy(vars(target))
    with pytest.raises(ValueError):
        importWorkflowPackage(target, package, deviceMapping=mapping)
    assert vars(target) == before


@pytest.mark.parametrize("field,value", [
    ("deviceTypeId", "other"), ("driverVersion", "2.0"), ("defaults", {"gain": 3}),
])
def testIncompatibleReuseRejectedWithoutMutation(definition, field, value, monkeypatch):  # noqa: F811
    now = "2026-10-20T02:40:48Z"
    monkeypatch.setattr("emo_master.apps.designer.state.workflow_store.utc_now_iso", lambda: now)
    package = buildWorkflowPackage(sourceStore(definition), "main")
    payload = project(definition)
    payload["devices"]["definitions"]["camera"][field] = value
    target = WorkflowStore(payload)
    before = deepcopy(vars(target))
    serializedBefore = target.toPayload()
    # toPayload creates a save candidate with the current time. Cross a second
    # deterministically, but compare every stored field for the no-mutation contract.
    now = "2026-10-20T02:40:49Z"
    preview = previewWorkflowPackageImport(target, package)
    assert preview.devices[0].compatibleTargets == ()
    assert vars(target) == before
    with pytest.raises(ValueError, match="映射不兼容"):
        importWorkflowPackage(target, package, deviceMapping={"camera": "camera"})
    assert vars(target) == before
    expected = deepcopy(serializedBefore)
    expected["project"]["updatedAt"] = now
    assert target.toPayload() == expected
    assert vars(target) == before


def testReuseDoesNotReplaceConnectionPoliciesOrEnabledState(definition, declaration):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    payload = project(definition)
    payload["devices"]["definitions"]["camera"].update(
        connection={"source": "other-site"}, policies={"vendor": 1}, enabled=False)
    target = WorkflowStore(payload)
    before = deepcopy(target.devices)
    from emo_master.core.plugin.devices import DeviceRequirement
    metadata = dict(deviceTypes={declaration.deviceTypeId: declaration}, operatorRequirements={
        node.operatorId: {slot: DeviceRequirement(deviceTypeId=declaration.deviceTypeId) for slot in node.deviceBindings}
        for workflow in package.workflows.values() for node in workflow.nodes if node.deviceBindings})
    preview = previewWorkflowPackageImport(target, package, **metadata)
    assert preview.devices[0].compatibleTargets == (("camera", definition.name),)
    result = importWorkflowPackage(target, package, deviceMapping={"camera": "camera"},
                                   **metadata,
                                   expectedFingerprint=preview.packageFingerprint,
                                   expectedTargetFingerprint=preview.targetFingerprint)
    assert target.devices == before
    assert result.deviceIdMap == {"camera": "camera"}


def testReuseUsesRegisteredGenericSelectorAndCapabilityRules(definition, declaration):  # noqa: F811
    from emo_master.core.plugin.devices import DeviceRequirement
    declaration.capabilities = ['capture']
    declaration.bindingSelectors = {'laneId': dict(itemsPath='connection.lanes', valueField='id', labelField='name')}
    definition.connection['lanes'] = [dict(id='one', name='Lane A')]
    package = buildWorkflowPackage(sourceStore(definition), 'main')
    node = next(node for workflow in package.workflows.values() for node in workflow.nodes if node.deviceBindings)
    node.deviceBindings['camera'].selectors = dict(laneId='one')
    requirement = DeviceRequirement(deviceTypeId=declaration.deviceTypeId, requiredCapabilities=['capture'],
        selectorSchema=dict(type='object', properties=dict(laneId=dict(type='string')), required=['laneId'],
                            additionalProperties=False))
    metadata = dict(deviceTypes={declaration.deviceTypeId: declaration},
                    operatorRequirements={node.operatorId: dict(camera=requirement)})
    target = WorkflowStore(project(definition))
    assert previewWorkflowPackageImport(target, package, **metadata).devices[0].compatibleTargets
    target.devices['definitions']['camera']['connection']['lanes'][0]['id'] = 'other'
    before = deepcopy(vars(target))
    assert not previewWorkflowPackageImport(target, package, **metadata).devices[0].compatibleTargets
    with pytest.raises(ValueError, match='selectors'):
        importWorkflowPackage(target, package, deviceMapping={'camera': 'camera'}, **metadata)
    assert vars(target) == before
    target.devices['definitions']['camera']['connection']['lanes'][0]['id'] = 'one'
    requirement.requiredCapabilities = ['missing-business-capability']
    assert not previewWorkflowPackageImport(target, package, **metadata).devices[0].compatibleTargets
    with pytest.raises(ValueError, match='映射不兼容'):
        importWorkflowPackage(target, package, deviceMapping={'camera': 'camera'}, **metadata)
    # An unavailable registry still permits an editable disabled copy.
    assert not previewWorkflowPackageImport(target, package).devices[0].compatibleTargets
    result = importWorkflowPackage(target, package, deviceMapping={'camera': None})
    assert not target.devices['definitions'][result.deviceIdMap['camera']]['enabled']


def testSameConnectionNeverAutomaticallyMerged(definition):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    target = WorkflowStore(project(definition))
    result = importWorkflowPackage(target, package, deviceMapping={"camera": None})
    assert len(target.devices["definitions"]) == 2
    assert result.deviceIdMap["camera"] != "camera"
    assert not target.devices["definitions"][result.deviceIdMap["camera"]]["enabled"]


def testInProjectCopyReusesDeviceAndVariableIds(definition):  # noqa: F811
    source = sourceStore(definition)
    package = buildWorkflowPackage(source, "main")
    before = deepcopy(source.devices)
    result = importWorkflowPackage(source, package, reuseVariableIds=True,
                                   deviceMapping={"camera": "camera"})
    assert result.rootWorkflowId != "main"
    assert result.deviceIdMap == {"camera": "camera"}
    assert source.devices == before


def testFailureAfterDeviceAllocationRollsBackEntireImport(definition):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    target = WorkflowStore()
    before = deepcopy(vars(target))
    with pytest.raises((ValueError, KeyError)):
        importWorkflowPackage(target, package, insertIntoWorkflowId="missing", deviceMapping={"camera": None})
    assert vars(target) == before


def testChangedPackageRequiresNewPreview(definition):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    target = WorkflowStore()
    preview = previewWorkflowPackageImport(target, package)
    before = deepcopy(vars(target))
    package.devices["camera"].connection = {"source": "changed"}
    with pytest.raises(ValueError, match="重新预览"):
        importWorkflowPackage(target, package, deviceMapping={"camera": None},
                              expectedFingerprint=preview.packageFingerprint)
    assert vars(target) == before


def testChangedTargetConnectionRequiresNewPreview(definition):  # noqa: F811
    package = buildWorkflowPackage(sourceStore(definition), "main")
    target = WorkflowStore(project(definition))
    preview = previewWorkflowPackageImport(target, package)
    target.devices["definitions"]["camera"]["connection"] = {"source": "changed"}
    before = deepcopy(vars(target))
    with pytest.raises(ValueError, match="目标工程"):
        importWorkflowPackage(target, package, deviceMapping={"camera": "camera"},
                              expectedTargetFingerprint=preview.targetFingerprint)
    assert vars(target) == before


@pytest.mark.parametrize("case", ["missing", "extra", "old-version"])
def testMalformedDeviceDependenciesRejected(definition, tmp_path, case):  # noqa: F811
    import json
    package = buildWorkflowPackage(sourceStore(definition), "main").toPayload()
    if case == "missing":
        package["devices"] = {}
    elif case == "extra":
        package["devices"]["extra"] = definition.model_dump()
    else:
        package["schemaVersion"] = "1.1"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(package))
    with pytest.raises(ValueError):
        loadWorkflowPackage(path)


def testNewWorkflowPackageUsesCurrentCompleteSchema():
    package = buildWorkflowPackage(WorkflowStore(), "main")
    assert package.schemaVersion == "1.4"
    assert package.toPayload()['devices'] == {}
    assert WorkflowPackageDocument.model_validate(package.toPayload()) == package


def testMissingCatalogDefinitionHasActionableExportFailure(definition):  # noqa: F811
    source = sourceStore(definition)
    del source.devices["definitions"]["camera"]
    with pytest.raises(ValueError, match="missing devices.*camera"):
        buildWorkflowPackage(source, "main")


def testInputParameterSourceSurvivesImport(definition):  # noqa: F811
    source = WorkflowStore(project(definition))
    node = next(n for n in source.get("main").nodes if n["nodeId"] == "capture")
    node["inputPorts"] = {"exposure": "number"}
    node["deviceValueBindings"] = {"camera": {
        "exposureUs": {"source": "input", "inputName": "exposure"}}}
    package = buildWorkflowPackage(source, "main")
    target = WorkflowStore()
    result = importWorkflowPackage(target, package, deviceMapping={"camera": None})
    imported = next(n for n in target.get(result.rootWorkflowId).nodes if n["nodeId"] == "capture")
    from emo_master.core.project.models import WorkflowNode
    assert (WorkflowNode.model_validate(imported).deviceValueBindings
            == WorkflowNode.model_validate(node).deviceValueBindings)


def testDeviceMappingDialogRequiresEverySelection(definition, declaration):  # noqa: F811
    from emo_master.apps.designer.ui.workflow_package_preview_dialog import WorkflowPackagePreviewDialog
    package = buildWorkflowPackage(sourceStore(definition), "main")
    from emo_master.core.plugin.devices import DeviceRequirement
    requirements = {node.operatorId: {slot: DeviceRequirement(deviceTypeId=declaration.deviceTypeId)
                    for slot in node.deviceBindings}
                    for workflow in package.workflows.values() for node in workflow.nodes if node.deviceBindings}
    preview = previewWorkflowPackageImport(WorkflowStore(project(definition)), package,
        deviceTypes={declaration.deviceTypeId: declaration}, operatorRequirements=requirements)
    extra = replace(preview.devices[0], sourceDeviceId="second")
    dialog = WorkflowPackagePreviewDialog(replace(preview, devices=(*preview.devices, extra)))
    try:
        assert not dialog._importButton.isEnabled()
        dialog._onImportClicked()
        assert not dialog.getSelection()[0]
        dialog._deviceCombos["camera"].setCurrentIndex(1)
        assert not dialog._importButton.isEnabled()
        dialog._deviceCombos["second"].setCurrentIndex(2)
        assert dialog._importButton.isEnabled()
        assert dialog.getDeviceMapping() == {"camera": None, "second": "camera"}
        dialog._onCancelClicked()
        assert not dialog.getSelection()[0]
    finally:
        dialog.close()


@pytest.mark.parametrize('offline', [False, True])
def testNormalWindowImportAndCopyAreSingleUndoableEdits(
        definition, ownedDesignerWindow, monkeypatch, tmp_path, offline):  # noqa: F811
    from emo_master.apps.designer.ui import main_window as module
    from tests.designer.qt_wait import waitForCatalog
    from tests.designer.test_main_window_workflow_tabs import _RuntimeClientStub

    package = buildWorkflowPackage(sourceStore(definition), "main")
    path = writeWorkflowPackage(tmp_path / "source", package)
    window = ownedDesignerWindow(_RuntimeClientStub())
    if offline:
        from emo_master.apps.designer.services.runtime_client import RuntimeClientError
        def unavailable():
            raise RuntimeClientError('UNAVAILABLE', 'offline metadata')
        monkeypatch.setattr(window.runtimeClient, 'listDeviceTypes', unavailable, raising=False)
    waitForCatalog(window)
    session = window.pageCoordinator.session
    window.pageCoordinator.sync()
    before = session.payload()
    undoCount = len(session._undo)

    class Dialog:
        def execSelection(self):
            assert window.pageCoordinator.commandDepth == 0
            return True, False

        def getDeviceMapping(self):
            return {"camera": None}

    monkeypatch.setattr(window, "_createWorkflowPackagePreviewDialog", lambda preview: Dialog())
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    importedId = window.importWorkflowPackageAction()
    assert importedId and importedId != "main"
    assert len(session._undo) == undoCount + 1
    after = session.payload()
    assert len(after["devices"]["definitions"]) == 1
    assert not next(iter(after["devices"]["definitions"].values()))["enabled"]
    window.pageCoordinator.history()
    assert session.payload() == before
    window.pageCoordinator.history(redo=True)
    assert session.payload() == after

    copyUndoCount = len(session._undo)
    copied = window.duplicateCurrentWorkflow()
    assert copied != importedId
    assert session.workflows.devices == after["devices"]
    assert len(session._undo) == copyUndoCount + 1
    window.pageCoordinator.history()
    assert session.payload() == after
