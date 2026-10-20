from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import Event

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.project_config.resources import prepareResources
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import finish


@pytest.fixture
def runtime(tmp_path):
    payload = simulatedProject()
    resource = tmp_path / "input.png"
    resource.write_bytes(b"immutable input resource")
    payload["workflows"]["main"]["nodes"].append(dict(nodeId="file", kind="operator",
        operatorId="vision.io.image_loader", params=dict(imagePath=str(resource))))
    payload["resources"]["items"]["image"] = dict(path="input.png", sha256=hashlib.sha256(resource.read_bytes()).hexdigest(),
        size=resource.stat().st_size, purpose="input_image")
    payload["resources"]["parameterBindings"] = [dict(resourceId="image", target=dict(
        workflowId="main", nodeId="file", parameterPath=["imagePath"]))]
    project = tmp_path / "project.json"
    project.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None)
    assert reply.ok, reply.message
    yield service, resource
    service.close()


def imagePath(service):
    return Path(next(n for n in service.loadedDocument.workflows["main"].nodes if n.nodeId == "file").params["imagePath"])


def testResourceReplacementPinsOldBytesAndInstallsNewBytes(runtime):
    service, resource = runtime
    old = imagePath(service)
    cycle = service.projectConfiguration.beginCycle("retained-plan", 0)
    assert old != resource and old.read_bytes() == b"immutable input resource"
    resource.write_bytes(b"replacement resource")
    candidate = deepcopy(service.projectConfiguration.snapshot()["payload"])
    candidate["resources"]["items"]["image"].update(sha256=hashlib.sha256(resource.read_bytes()).hexdigest(), size=resource.stat().st_size)
    service.projectConfiguration.start(dict(requestId="resources", expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=1, localPayload=candidate,
        selectedPaths=["/resources/items/image/sha256", "/resources/items/image/size"]))
    result = finish(service.projectConfiguration, "resources")
    assert result["state"] == "COMMITTED", result
    assert imagePath(service) != old
    assert imagePath(service).read_bytes() == b"replacement resource"
    assert old.read_bytes() == b"immutable input resource"
    assert service.loadedPayload["resources"]["items"]["image"]["path"] == "input.png"
    service.projectConfiguration.endCycle("retained-plan", cycle["cycleId"])
    service._reclaimConfigurationResources()
    assert not old.exists()
    assert imagePath(service).exists()


def testResourceValidationFailureDoesNotCommitOrReplace(runtime):
    service, resource = runtime
    old = imagePath(service)
    owned = service.projectResourceTransfers.resolve(service.loadedDocument)["image"]
    owned.write_bytes(b"unexpected replacement")
    candidate = deepcopy(service.projectConfiguration.snapshot()["payload"])
    candidate["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    service.projectConfiguration.start(dict(requestId="failure", expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
    assert finish(service.projectConfiguration, "failure")["state"] == "FAILED"
    assert service.projectConfiguration.snapshot()["documentRevision"] == 1
    assert imagePath(service) == old and old.exists()


def testChangedSourceCannotAlterCommittedBytesOrBlockUnrelatedWrite(runtime):
    service, resource = runtime
    old = imagePath(service)
    resource.write_bytes(b"unexpected external replacement")
    candidate = deepcopy(service.projectConfiguration.snapshot()["payload"])
    candidate["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    service.projectConfiguration.start(dict(requestId="source-independent", expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
    assert finish(service.projectConfiguration, "source-independent")["state"] == "COMMITTED"
    assert imagePath(service) == old and old.read_bytes() == b"immutable input resource"


def testCancelledCopyDoesNotLeavePartialFile(runtime, tmp_path):
    service, _resource = runtime
    document = service.loadedDocument.model_copy(deep=True)
    source = tmp_path / "new.bin"
    source.write_bytes(b"next resource")
    next(n for n in document.workflows["main"].nodes if n.nodeId == "file").params["imagePath"] = str(source)
    cancelled = Event()
    cancelled.set()
    root = tmp_path / "candidate-cache"
    with pytest.raises(ValueError, match="cancelled"):
        prepareResources(document, service.pluginScanResult.activeOperators, service.pluginScanResult.deviceTypes,
                         root, cancelled)
    assert not list(root.rglob("*.pending"))
    assert not list(root.iterdir())


def testCorruptedRetainedCopyIsNotSilentlyReused(runtime):
    service, _resource = runtime
    imagePath(service).write_bytes(b"corrupted private copy")
    candidate = deepcopy(service.projectConfiguration.snapshot()["payload"])
    candidate["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    service.projectConfiguration.start(dict(requestId="corrupt", expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
    status = finish(service.projectConfiguration, "corrupt")
    assert status["state"] == "FAILED" and status["code"] == "E_CONFIG_RESOURCE_CORRUPT", repr(status)
    assert service.projectConfiguration.snapshot()["documentRevision"] == 1
