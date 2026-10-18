from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from emo_master.core.plugin.devices import DeviceType
from emo_master.core.project.device_resources import trustedDeviceTypes
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.resources import DeviceParameterTarget, ResourcePlan
from emo_master.core.project.runtime_directory import prepareRuntimeProject
from emo_master.core.project.runtime_package import buildRuntimePackage, installRuntimePackage
from emo_master.core.project.test_delivery import trustedRegistry
from tests.runtime.test_device_runtime import simulatedProject


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    content = b"synthetic calibration data"
    (root / "calibration.bin").write_bytes(content)
    metadata = trustedDeviceTypes()["device.simulated.camera"].model_dump()
    fileSpec = dict(type="string", default="", xWidget="file", xFileMode="open", xResourcePurpose="model")
    metadata["connectionSchema"]["properties"]["calibration"] = deepcopy(fileSpec)
    metadata["connectionSchema"]["properties"]["credential"] = dict(type="string", default="", xResourcePurpose="secret_ref")
    metadata["defaultsSchema"]["properties"]["reference"] = deepcopy(fileSpec)
    metadata["defaultsSchema"]["properties"]["trace"] = dict(
        type="string", default="", xWidget="file", xFileMode="save", xResourcePurpose="output_file")
    metadata["overridePaths"].append("reference")
    declaration = DeviceType.model_validate(metadata)
    types = {declaration.deviceTypeId: declaration}
    payload = simulatedProject()
    payload["devices"]["definitions"]["camera"]["defaults"]["trace"] = "logs/trace.bin"
    payload["resources"]["items"]["calibration"] = dict(
        path="calibration.bin", sha256=hashlib.sha256(content).hexdigest(), size=len(content), purpose="model")
    payload["resources"]["parameterBindings"] = [dict(resourceId="calibration", target=dict(
        deviceId="camera", section="connection", parameterPath=["calibration"]))]
    payload["resources"]["siteBindings"] = [dict(field="serial", purpose="camera_serial", target=dict(
        deviceId="camera", section="connection", parameterPath=["serial"]))]
    document = ProjectDocument.model_validate(payload)
    save(root, document)
    return root, document, types, trustedRegistry()


def save(root, document):
    (root / "project.json").write_text(document.model_dump_json(indent=2), encoding="utf-8")


def prepare(bundle, **kwargs):
    root, document, types, registry = bundle
    return prepareRuntimeProject(document, root, registry, deviceTypes=types, **kwargs)


def build(bundle, tmp_path):
    root, document, types, registry = bundle
    save(root, document)
    return buildRuntimePackage(root, tmp_path / "packages", registry=registry, deviceTypes=types)


def testDeviceTargetsResolveWithoutMutatingDraftOrGuessingAddress(bundle):
    root, document, _, _ = bundle
    before = document.model_dump()
    result = prepare(bundle, siteValues={"serial": "SITE-ONLY"})
    definition = result.devices.definitions["camera"]
    assert definition.connection["calibration"] == str(root / "calibration.bin")
    assert definition.connection["serial"] == "SITE-ONLY"
    assert definition.defaults["trace"] == str(root / "logs/trace.bin")
    assert document.model_dump() == before
    assert not (root / "logs").exists()
    assert document.resources.model_dump() == result.resources.model_dump()


@pytest.mark.parametrize("target", [
    {"deviceId": "camera", "section": "policies", "parameterPath": ["retry"]},
    {"deviceId": "camera", "section": "connection", "parameterPath": []},
    {"deviceId": "camera", "section": "connection", "parameterPath": ["serial"], "nodeId": "capture"},
])
def testAmbiguousOrInvalidDeviceTargetsRejected(target):
    with pytest.raises(ValueError):
        ResourcePlan.model_validate({"siteBindings": [{"field": "serial", "purpose": "camera_serial", "target": target}]})


def testDeviceTargetVersionAndReferenceGates(bundle):
    _, document, _, _ = bundle
    payload = document.model_dump()
    payload["devices"]["definitions"] = {}
    payload["workflows"]["main"]["nodes"][1].pop("deviceBindings")
    with pytest.raises(ValueError, match="unknown device resource target"):
        ProjectDocument.model_validate(payload)
    payload = document.model_dump()
    payload["schemaVersion"] = "2.5"
    payload["devices"] = {}
    payload["workflows"]["main"]["nodes"][1].pop("deviceBindings")
    with pytest.raises(ValueError, match="device resource targets.*2.6"):
        ProjectDocument.model_validate(payload)


def testOverlapIncludesAncestorsButDifferentOwnersAreIndependent():
    target = dict(deviceId="camera", section="defaults", parameterPath=["nested"])
    binding = dict(resourceId="a", target=target)
    child = dict(resourceId="b", target=dict(target, parameterPath=["nested", "path"]))
    with pytest.raises(ValueError, match="overlap"):
        ResourcePlan.model_validate({"parameterBindings": [binding, child]})
    child["target"]["section"] = "connection"
    plan = ResourcePlan.model_validate({"parameterBindings": [binding, child]})
    assert len(plan.parameterBindings) == 2 and not plan.nodeParameterBindings


@pytest.mark.parametrize("failure", ["missing", "changed", "purpose", "metadata", "path", "site", "empty-site"])
def testPreparationFailsClosedBeforeAnyRuntimeAction(bundle, failure):
    root, document, types, _ = bundle
    if failure == "missing":
        (root / "calibration.bin").unlink()
    elif failure == "changed":
        (root / "calibration.bin").write_bytes(b"x" * (root / "calibration.bin").stat().st_size)
    elif failure == "purpose":
        document.resources.items["calibration"].purpose = "input_image"
    elif failure == "metadata":
        types.clear()
    elif failure == "path":
        document.resources.parameterBindings[0].target.parameterPath = ["unknown"]
    kwargs = {"siteValues": {"undeclared": "value"}} if failure == "site" else (
        {"siteValues": {"serial": ""}} if failure == "empty-site" else {})
    with pytest.raises(ValueError):
        prepare(bundle, **kwargs)


@pytest.mark.parametrize("path", ["calibration.bin", "project.json", "runtime/data.db"])
def testDeviceOutputsCannotOverwriteInputsOrRuntime(bundle, path):
    root, document, _, _ = bundle
    document.devices.definitions["camera"].defaults["trace"] = path
    with pytest.raises(ValueError, match="output overlaps"):
        prepare(bundle, protectedPaths=(root / "runtime",))


def testDeviceSecretMustRemainVersionedReference(bundle):
    _, document, _, _ = bundle
    document.resources.siteBindings.append(document.resources.siteBindings[0].model_copy(update={
        "field": "credential", "purpose": "secret_ref", "target": DeviceParameterTarget(
            deviceId="camera", section="connection", parameterPath=["credential"])}))
    with pytest.raises(ValueError, match="versioned references"):
        prepare(bundle, siteValues={"credential": "plaintext"})
    assert prepare(bundle, siteValues={"credential": "secret://camera/token@v2"})


def testUnboundSecretCannotLeakIntoDeployment(bundle, tmp_path):
    bundle[1].devices.definitions["camera"].connection["credential"] = "plaintext"
    with pytest.raises(ValueError, match="versioned references"):
        build(bundle, tmp_path)
    assert not (tmp_path / "packages").exists()


def testDeviceResourceReferencePreventsCatalogDeletion(bundle):
    from emo_master.apps.designer.state.device_catalog import DeviceCatalogEditor
    from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
    _, document, types, _ = bundle
    payload = document.model_dump()
    payload["workflows"]["main"]["nodes"] = []
    payload["workflows"]["main"]["edges"] = []
    session = ProjectEditSession(payload, enablePresentation=False)
    editor = DeviceCatalogEditor(session, types)
    before = session.payload()
    with pytest.raises(ValueError, match="资源或现场绑定"):
        editor.remove("camera")
    assert session.payload() == before


def testStaticFileOverrideIsResolvedWithoutMaterializingInheritedFields(bundle):
    root, document, _, _ = bundle
    node = next(n for n in document.workflows["main"].nodes if n.nodeId == "capture")
    node.deviceOverrides = {"camera": {"reference": "calibration.bin", "gain": 0}}
    result = prepare(bundle)
    actual = next(n for n in result.workflows["main"].nodes if n.nodeId == "capture")
    assert actual.deviceOverrides == {"camera": {"reference": str(root / "calibration.bin"), "gain": 0}}
    assert node.deviceOverrides["camera"]["reference"] == "calibration.bin"


@pytest.mark.parametrize("field", ["dynamicPaths", "nextCyclePaths"])
def testFileFieldsCannotBypassResourcePreparation(bundle, field):
    declaration = next(iter(bundle[2].values())).model_dump()
    declaration[field].append("reference")
    with pytest.raises(ValueError, match="file parameters"):
        DeviceType.model_validate(declaration)


def testRuntimePackageMovesAllDeviceFilesAndLocksMetadataWithoutDriverCreation(bundle, tmp_path, monkeypatch):
    from emo_master.apps.runtime.devices.simulated import SimulatedCamera
    monkeypatch.setattr(SimulatedCamera, "__init__", lambda *a, **k: pytest.fail("driver constructed during packaging"))
    root, document, types, registry = bundle
    external = tmp_path / "outside.bin"
    external.write_bytes(b"external reference")
    definition = document.devices.definitions["camera"]
    definition.defaults["reference"] = str(external)
    node = next(n for n in document.workflows["main"].nodes if n.nodeId == "capture")
    node.deviceOverrides = {"camera": {"reference": "calibration.bin"}}
    package = build(bundle, tmp_path)
    before = (root / "project.json").read_bytes()
    external.unlink()
    moved = tmp_path / "moved"
    installed = installRuntimePackage(package, moved, registry=registry, deviceTypes=types)
    result = prepareRuntimeProject(installed, moved, registry, deviceTypes=types)
    assert Path(result.devices.definitions["camera"].defaults["reference"]).read_bytes() == b"external reference"
    assert result.devices.definitions["camera"].connection["calibration"] == str(moved / "calibration.bin")
    assert installed.devices.definitions["camera"].defaults["trace"] == "logs/trace.bin"
    assert installed.resources == document.resources
    assert (root / "project.json").read_bytes() == before
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["requiredDeviceTypes"]["device.simulated.camera"]["version"] == "1.0.0"
        assert all(not name.endswith(".py") for name in archive.namelist())


def testMetadataDriftRejectsInstallBeforeReplacingProject(bundle, tmp_path):
    _, _, types, registry = bundle
    package = build(bundle, tmp_path)
    target = tmp_path / "installed"
    installRuntimePackage(package, target, registry=registry, deviceTypes=types)
    before = (target / "project.json").read_bytes()
    declaration = next(iter(types.values()))
    declaration.defaultsSchema["properties"]["gain"]["maximum"] = 30
    with pytest.raises(ValueError, match="metadata lock"):
        installRuntimePackage(package, target, registry=registry, deviceTypes=types)
    assert (target / "project.json").read_bytes() == before


def testUpdateProtectsExistingDeviceOutput(bundle, tmp_path):
    root, document, types, registry = bundle
    target = tmp_path / "installed"
    installRuntimePackage(build(bundle, tmp_path), target, registry=registry, deviceTypes=types)
    (target / "logs").mkdir()
    (target / "logs/trace.bin").write_bytes(b"business output")
    (root / "logs").mkdir()
    (root / "calibration.bin").rename(root / "logs/trace.bin")
    document.resources.items["calibration"].path = "logs/trace.bin"
    document.devices.definitions["camera"].defaults["trace"] = "elsewhere/trace.bin"
    with pytest.raises(ValueError, match="existing business output"):
        installRuntimePackage(build(bundle, tmp_path), target, registry=registry, deviceTypes=types)
    assert (target / "logs/trace.bin").read_bytes() == b"business output"


def testWorkflowPackageCannotSilentlyDropDeviceResourceDeclarations(bundle):
    from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
    from emo_master.apps.designer.state.workflow_store import WorkflowStore
    store = WorkflowStore(bundle[1].model_dump())
    with pytest.raises(ValueError, match="完整工程部署包"):
        buildWorkflowPackage(store, "main")
    package = buildWorkflowPackage(store, "main", forInProjectCopy=True)
    resources = deepcopy(store.projectExtensions["resources"])
    result = importWorkflowPackage(store, package, reuseVariableIds=True, deviceMapping={"camera": "camera"})
    assert result.rootWorkflowId != "main"
    assert store.projectExtensions["resources"] == resources


def testPackagedDeviceInputCannotFallBackToInstallerHostFile(bundle, tmp_path):
    _, _, types, registry = bundle
    package = build(bundle, tmp_path)
    outside = tmp_path / "private.bin"
    outside.write_bytes(b"installer local data")
    with zipfile.ZipFile(package) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    payload = json.loads(files["project.json"])
    payload["devices"]["definitions"]["camera"]["defaults"]["reference"] = str(outside)
    files["project.json"] = json.dumps(payload).encode()
    manifest = json.loads(files["manifest.json"])
    manifest["files"]["project.json"] = dict(size=len(files["project.json"]),
        sha256=hashlib.sha256(files["project.json"]).hexdigest())
    files["manifest.json"] = json.dumps(manifest).encode()
    invalid = tmp_path / "invalid.vxpkg"
    with zipfile.ZipFile(invalid, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    destination = tmp_path / "target"
    with pytest.raises(ValueError, match="escapes package root"):
        installRuntimePackage(invalid, destination, registry=registry, deviceTypes=types)
    assert not (destination / "project.json").exists()


def testNestedFileDefaultsCannotBypassExistenceChecks(bundle):
    _, document, types, _ = bundle
    declaration = next(iter(types.values()))
    declaration.connectionSchema["properties"]["nested"] = dict(type="object", default={}, properties={
        "path": dict(type="string", default="missing.bin", xWidget="file", xFileMode="open")})
    with pytest.raises(ValueError, match="input file not found"):
        prepare(bundle)
    assert "nested" not in document.devices.definitions["camera"].connection


def testInstalledDeviceResourcesReachSpawnedJobAndRestartRechecksIntegrity(bundle, tmp_path):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from tests.runtime.runtime_test_utils import waitForTerminal

    _, document, types, registry = bundle
    installed = tmp_path / "installed"
    installRuntimePackage(build(bundle, tmp_path), installed, registry=registry, deviceTypes=types)
    # This installed metadata fixture is external to the project/package. It uses
    # the existing simulator; no SDK, socket or new driver code is loaded.
    plugins = tmp_path / "trusted-plugins"
    plugin = plugins / "simulated"
    (plugin / "device_types").mkdir(parents=True)
    (plugin / "device_types/camera.json").write_text(next(iter(types.values())).model_dump_json(), encoding="utf-8")
    builtin = Path(__file__).resolve().parents[3] / "src/emo_master/plugins/builtins/simulated_camera/manifest.json"
    (plugin / "manifest.json").write_bytes(builtin.read_bytes())
    kwargs = dict(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "jobs", pluginRootPaths=(str(plugins),))
    service = RuntimeService(**kwargs)
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(installed / "project.json")), None)
        assert loaded.ok, loaded.message
        prepared = Path(service.deviceManager.definitions["camera"].connection["calibration"])
        assert prepared.is_relative_to(tmp_path / "configuration-resources")
        assert prepared != installed / "calibration.bin"
        assert prepared.read_bytes() == (installed / "calibration.bin").read_bytes()
        # Immutable paths are execution details, not a reason to disconnect an
        # unchanged resource-backed adapter during an unrelated metadata write.
        from copy import deepcopy
        from tests.runtime.test_project_configuration import finish
        driver = service.deviceManager.drivers["camera"]
        lifecycle = service.deviceManager.lifecycles["camera"]
        executor = service.deviceManager.executors["camera"]
        executor.wait(executor.submit("test-connect", lambda cancel, deadline, _: lifecycle.connect(cancel, deadline)))
        candidate = deepcopy(service.loadedPayload)
        candidate["devices"]["definitions"]["camera"]["name"] = "Renamed resource camera"
        request = pb.ProjectConfigurationWriteRequest(request_id="rename", expected_revision=1,
            expected_runtime_instance_id=service.runtimeInstanceId, local_payload_json=json.dumps(candidate),
            selected_paths=["/devices/definitions/camera/name"], replacement_policy="stopAffected")
        validation = service.ValidateProjectWrite(request, None)
        assert validation.ok, validation.message
        assert json.loads(validation.result_json)["disconnectDevices"] == []
        assert service.WriteProjectConfiguration(request, None).ok
        status = finish(service.projectConfiguration, "rename")
        assert status["state"] == "COMMITTED" and not status["connections"], status
        assert service.deviceManager.drivers["camera"] is driver and lifecycle.connected
        from threading import Event
        changed = service.loadedDocument.model_dump()
        changed["devices"]["definitions"]["camera"]["defaults"]["reference"] = "new-resource.bin"
        with pytest.raises(ValueError, match="input file not found"):
            service._prepareProjectConfiguration(changed, Event())
        started = service.StartJob(pb.StartJobRequest(project_id=document.project.projectId, workflow_id="main"), None)
        assert started.ok, started.message
        result = waitForTerminal(service, started.job_id, timeoutSeconds=30)
        assert result.status == "COMPLETED", result
        assert service.deviceManager.drivers["camera"].sequence == 1
    finally:
        service.close()
    (installed / "calibration.bin").write_bytes(b"tampered")
    recovered = RuntimeService(**kwargs)
    try:
        assert "resource" in recovered.onlineRecoveryError
        assert not recovered.deviceManager.drivers
        reply = recovered.StartJob(pb.StartJobRequest(project_id=document.project.projectId, workflow_id="main"), None)
        assert not reply.ok and "E_CONFIG_NOT_READY" in reply.message
    finally:
        recovered.close()


def testProductionReopenUsesCommittedVersionAndRefusesInPlacePackageUpdate(tmp_path):
    from emo_master.apps.operator_runtime.controller import ProductionRuntime
    source = tmp_path / "project"
    source.mkdir()
    document = ProjectDocument.model_validate(simulatedProject())
    save(source, document)
    package = buildRuntimePackage(source, tmp_path / "packages")
    data = tmp_path / "runtime"
    first = ProductionRuntime(data)
    try:
        first.load(source)
        lease = first.projectOwner.stream
        first.load(source)
        assert first.projectOwner.stream is lease
        before = (source / "project.json").read_bytes()
        with pytest.raises(ValueError, match="no files changed"):
            first.installPackage(package)
        assert (source / "project.json").read_bytes() == before
        assert first.document is not None
    finally:
        first.close()
    document.devices.definitions["camera"].defaults["exposureUs"] = 12345
    save(source, document)
    second = ProductionRuntime(data)
    try:
        loaded = second.load(source)
        assert loaded.devices.definitions["camera"].defaults["exposureUs"] == 8000
        other = tmp_path / "other"
        other.mkdir()
        save(other, document)
        lease = second.projectOwner.stream
        with pytest.raises(ValueError, match="E_CONFIG_ACTIVE"):
            second.load(other)
        assert second.projectOwner.stream is lease and second.document == loaded
    finally:
        second.close()
