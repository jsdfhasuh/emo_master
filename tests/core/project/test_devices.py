from copy import deepcopy
import json
from pathlib import Path

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.plugin.devices import DeviceType
from emo_master.core.project.devices import DeviceDefinition, DeviceValueBinding, resolveDeviceValues
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument, WorkflowNode


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def declaration():
    return DeviceType.model_validate_json((ROOT / "examples/device_plugin/device_types/simulated_camera.json").read_text())


@pytest.fixture
def definition(declaration):
    return DeviceDefinition(name="Camera A", deviceTypeId=declaration.deviceTypeId, driverVersion="1.0.0",
                            connection={"source": "offline"},
                            defaults={"exposureMode": "manual", "exposureUs": 8000.0, "gain": 0.0, "enabled": False})


def project(definition):
    payload = migrateProjectPayload(WorkflowStore().toPayload(), enableDevices=True)
    payload["devices"]["definitions"]["camera"] = definition.model_dump()
    payload["workflows"]["main"]["nodes"].append({
        "nodeId": "capture", "operatorId": "vision.io.huaray_camera",
        "deviceBindings": {"camera": {"deviceId": "camera"}}})
    return payload


@pytest.mark.parametrize("version", ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5"])
def testExplicitUpgradePreservesPreviousFeaturesAndLegacyBindings(version):
    payload = WorkflowStore().toPayload()
    flags = {"2.2": "enablePresentation", "2.3": "enableProduction",
             "2.4": "enableGlobalVariables", "2.5": "enableCommunication"}
    if version in flags:
        payload = migrateProjectPayload(payload, **{flags[version]: True})
    else:
        payload["schemaVersion"] = version
    payload["devices"]["bindings"] = {"legacy": {"opaque": [False, 0, None]}}
    before = deepcopy(payload)
    upgraded = migrateProjectPayload(payload, enableDevices=True)
    assert payload == before
    assert upgraded["schemaVersion"] == "2.6"
    assert upgraded["devices"] == {"bindings": payload["devices"]["bindings"], "schemaVersion": "1.0", "definitions": {}}
    assert upgraded["communication"]["enabled"] is False
    for key in ("presentation", "resources", "production", "globalVariables", "communication"):
        if key in payload:
            assert upgraded[key] == payload[key]
    assert migrateProjectPayload(upgraded, enableCommunication=True, enableGlobalVariables=True,
                                 enablePresentation=True, enableDevices=True) == upgraded


def testLegacySerializationDoesNotAddNewFields():
    payload = WorkflowStore().toPayload()
    normalized = ProjectDocument.model_validate(payload).model_dump()
    assert normalized["devices"] == {"bindings": {}}
    assert all(not any(key.startswith("device") for key in node)
               for node in normalized["workflows"]["main"]["nodes"])


@pytest.mark.parametrize("change", ["catalog", "emptySources", "binding"])
def testNewFieldsCannotHideInOldVersion(definition, change):
    payload = migrateProjectPayload(WorkflowStore().toPayload(), enableCommunication=True)
    if change == "catalog":
        payload["devices"]["definitions"] = {}
    elif change == "emptySources":
        payload["workflows"]["main"]["nodes"][0]["deviceBindings"] = {}
    else:
        payload = project(definition)
        payload["schemaVersion"] = "2.5"
    with pytest.raises(ValueError, match="2.6"):
        ProjectDocument.model_validate(payload)


def testUnknownTypeAndKeepArePreservedWithoutDiscovery(definition):
    payload = project(definition)
    raw = payload["devices"]["definitions"]["camera"]
    raw.update(deviceTypeId="missing.vendor.camera", defaults={"exposureMode": "keep"},
               policies={"vendorSpecific": {"token": None}})
    result = ProjectDocument.model_validate(payload).model_dump()
    assert result["devices"]["definitions"]["camera"] == raw
    assert "exposureUs" not in raw["defaults"]


@pytest.mark.parametrize("overrides,values", [
    ({"gain": 0}, {"gain": {"source": "variable", "variableId": "v"}}),
    ({"roi": {}}, {"roi.width": {"source": "input", "inputName": "width"}}),
    ({"roi": {"width": 5}}, {"roi": {"source": "variable", "variableId": "v"}}),
    ({}, {"roi": {"source": "variable", "variableId": "v"},
          "roi.width": {"source": "variable", "variableId": "v"}}),
])
def testConflictingSourcesRejected(overrides, values):
    with pytest.raises(ValueError, match="overlapping"):
        WorkflowNode(nodeId="n", deviceBindings={"camera": {"deviceId": "d"}},
                     deviceOverrides={"camera": overrides}, deviceValueBindings={"camera": values})


def testOrphanSourceAndMissingReferenceRejected(definition):
    with pytest.raises(ValueError, match="bound device slot"):
        WorkflowNode(nodeId="n", deviceOverrides={"camera": {"gain": 0}})
    payload = project(definition)
    payload["devices"]["definitions"].clear()
    with pytest.raises(ValueError, match="unknown device reference"):
        ProjectDocument.model_validate(payload)


@pytest.mark.parametrize("binding", [
    {"source": "variable"}, {"source": "input", "variableId": "v"},
    {"source": "variable", "variableId": "v", "inputName": "i"},
])
def testDynamicSourceHasExactlyOneIdentity(binding):
    with pytest.raises(ValueError):
        DeviceValueBinding.model_validate(binding)


def testOverridesPreservePresenceAndNeverMutateDefaults(declaration, definition):
    before = definition.model_dump()
    assert declaration.resolveParameters(definition, {"exposureUs": 12000.0}, {}, {}, {})["exposureUs"] == 12000
    assert declaration.resolveParameters(definition, {}, {}, {}, {})["exposureUs"] == 8000
    assert declaration.resolveParameters(definition, {"gain": 0, "enabled": False}, {}, {}, {})["enabled"] is False
    assert definition.model_dump() == before
    assert resolveDeviceValues({"a": 4}, {"a": None}, {}, {}, {}) == {"a": None}


@pytest.mark.parametrize("field", ["connection", "policies", "unknown"])
def testNodeCannotOverrideConnectionOrPolicy(declaration, definition, field):
    with pytest.raises(ValueError, match="cannot be overridden"):
        declaration.validateNodeSources(definition, {field: "arbitrary"}, {})


def testRelatedModeCannotBeSilentlyChanged(declaration, definition):
    definition.defaults = {"exposureMode": "keep", "gain": 0, "enabled": True}
    assert declaration.validateDefinition(definition).defaults["exposureMode"] == "keep"
    with pytest.raises(ValueError, match="requires exposureMode"):
        declaration.validateNodeSources(definition, {"exposureUs": 12000}, {})
    effective = declaration.validateNodeSources(definition, {"exposureUs": 12000, "exposureMode": "manual"}, {})
    assert effective["exposureMode"] == "manual"
    assert definition.defaults["exposureMode"] == "keep"


def testDynamicValuesResolveOnceAndValidateBeforeAdmission(declaration, definition):
    values = {"exposureUs": {"source": "variable", "variableId": "exposure"},
              "gain": {"source": "input", "inputName": "gain"}}
    variables = {"exposure": 13000}
    result = declaration.resolveParameters(definition, {}, values, variables, {"gain": 2})
    variables["exposure"] = 1
    assert result["exposureUs"] == 13000
    with pytest.raises(ValueError, match="maximum"):
        declaration.resolveParameters(definition, {}, values, variables, {"gain": 100})
    with pytest.raises(ValueError, match="missing device"):
        declaration.resolveParameters(definition, {}, values, {}, {"gain": 2})


def testPolicyDefaultsAndFixedValues(declaration, definition):
    checked = declaration.validateDefinition(definition)
    assert checked.policies["release"] == "on_project_close"
    assert definition.policies == {}
    definition.policies = {"release": "when_unused"}
    with pytest.raises(ValueError, match="enum"):
        declaration.validateDefinition(definition)


def testDynamicRelatedModeValidatedOnlyAfterResolution(declaration, definition):
    declaration.dynamicPaths.append("exposureMode")
    definition.defaults = {"exposureMode": "keep", "gain": 0, "enabled": True}
    bindings = {"exposureMode": {"source": "variable", "variableId": "mode"}}
    declaration.validateNodeSources(definition, {"exposureUs": 12000}, bindings)
    assert declaration.resolveParameters(definition, {"exposureUs": 12000}, bindings,
                                         {"mode": "manual"}, {})["exposureUs"] == 12000
    with pytest.raises(ValueError, match="requires exposureMode"):
        declaration.resolveParameters(definition, {"exposureUs": 12000}, bindings, {"mode": "auto"}, {})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def testOpaqueOrNonfiniteDeviceValuesRejected(declaration, definition, value):
    with pytest.raises(ValueError):
        raw = definition.model_dump()
        raw["defaults"]["gain"] = value
        declaration.validateDefinition(DeviceDefinition.model_validate(raw))


def testFileRoundtripAndExplicitBackup(tmp_path, definition):
    from emo_master.apps.designer.state.project_store import saveProject, loadProject

    path = tmp_path / "device.emoproj"
    old = WorkflowStore().toPayload()
    saveProject(path, old)
    oldBytes = path.read_bytes()
    loadProject(path)
    assert path.read_bytes() == oldBytes
    saveProject(path, project(definition))
    assert loadProject(path)["devices"]["definitions"]["camera"] == definition.model_dump()
    assert json.loads(path.with_suffix(path.suffix + ".bak").read_text())["schemaVersion"] == "2.1"
