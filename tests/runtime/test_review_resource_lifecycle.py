"""Durable resource ownership and explicit software-only recovery regressions."""
from copy import deepcopy
import json
from pathlib import Path
from threading import Event

import pytest
import cv2
import numpy as np

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectChanges
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_prepared_resources import imagePath
from tests.runtime.test_project_configuration import finish
from tests.runtime.test_project_resource_transfer import manifest
from tests.runtime.runtime_test_utils import waitForTerminal


INPUT = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))[1].tobytes()
REPLACEMENT = cv2.imencode(".png", np.full((8, 8, 3), 128, dtype=np.uint8))[1].tobytes()


def resourceProject(root):
    root.mkdir()
    source = root / "input.png"
    source.write_bytes(INPUT)
    payload = simulatedProject()
    payload["workflows"]["main"]["nodes"].append(dict(nodeId="file", kind="operator",
        operatorId="vision.io.image_loader", params=dict(imagePath=str(source))))
    payload["resources"]["items"]["image"] = dict(manifest(source.read_bytes(), source.name), purpose="input_image")
    payload["resources"]["parameterBindings"] = [dict(resourceId="image", target=dict(
        workflowId="main", nodeId="file", parameterPath=["imagePath"]))]
    project = root / "project.json"
    project.write_text(json.dumps(payload), encoding="utf-8")
    return project, source


def upload(service, document, data):
    request = pb.ProjectResourceTransferRequest(runtime_instance_id=service.runtimeInstanceId,
        project_id=document["project"]["projectId"], transfer_id="replacement", action="BEGIN",
        resource_json=json.dumps(document["resources"]["items"]["image"]))
    assert service.TransferProjectResource(request, None).ok
    request.action, request.data = "CHUNK", data
    assert service.TransferProjectResource(request, None).ok
    request.action, request.data = "COMMIT", b""
    assert service.TransferProjectResource(request, None).ok
    return request


def write(service, candidate, name="repair"):
    snapshot = service.projectConfiguration.snapshot()
    service.projectConfiguration.start(dict(requestId=name, expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=snapshot["documentRevision"], localPayload=candidate,
        selectedPaths=[c["path"] for c in projectChanges(snapshot["payload"], candidate)]))
    return finish(service.projectConfiguration, name)


@pytest.mark.parametrize("corrupt", [False, True])
def testInitialLocalResourceSurvivesDirectoryRemovalOrAllowsExplicitRepair(tmp_path, corrupt):
    project, source = resourceProject(tmp_path / "source-project")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        owned = service.projectResourceTransfers.resolve(service.loadedDocument)["image"]
        assert owned.read_bytes() == source.read_bytes()
    finally:
        service.close()
    source.unlink()
    project.unlink()
    project.parent.rmdir()
    if corrupt:
        owned.write_bytes(b"corrupt")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert bool(service.onlineRecoveryError) == corrupt
        if not corrupt:
            assert imagePath(service).read_bytes() == INPUT
        else:
            reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
            assert not reply.ok
        candidate = deepcopy(service.projectConfiguration.snapshot()["payload"])
        data = REPLACEMENT
        candidate["resources"]["items"]["image"] = dict(manifest(data, "new.png"), purpose="input_image")
        transfer = upload(service, candidate, data)
        assert write(service, candidate)["state"] == "COMMITTED"
        assert not service.onlineRecoveryError
        assert service.loadedProjectId == candidate["project"]["projectId"]
        assert imagePath(service).read_bytes() == data
        transfer.action = "CANCEL"
        denied = service.TransferProjectResource(transfer, None)
        assert not denied.ok and denied.code == "E_RESOURCE_TRANSFER_READY"
        with service.sqliteStore.connection() as connection:
            assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone()[0] == 0
        started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        assert waitForTerminal(service, started.job_id, timeoutSeconds=30).status == "COMPLETED"
    finally:
        service.close()
    restored = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not restored.onlineRecoveryError
        assert restored.projectConfiguration.current["documentRevision"] == 2
        assert imagePath(restored).read_bytes() == REPLACEMENT
    finally:
        restored.close()


def testPreparedUploadCannotBeCancelledUntilCancelledWriteRetires(tmp_path, monkeypatch):
    project, _source = resourceProject(tmp_path / "source-project")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    entered, release = Event(), Event()
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        candidate = deepcopy(service.projectConfiguration.current["payload"])
        data = b"candidate bytes"
        candidate["resources"]["items"]["image"] = dict(manifest(data, "candidate.png"), purpose="input_image")
        transfer = upload(service, candidate, data)
        original = service._preparePinnedProjectConfiguration
        def prepare(payload, cancelled):
            result = original(payload, cancelled)
            entered.set()
            assert release.wait(10)
            return result
        monkeypatch.setattr(service, "_preparePinnedProjectConfiguration", prepare)
        snapshot = service.projectConfiguration.snapshot()
        service.projectConfiguration.start(dict(requestId="cancel", expectedRuntimeInstanceId=service.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate,
            selectedPaths=[c["path"] for c in projectChanges(snapshot["payload"], candidate)]))
        assert entered.wait(5)
        transfer.action = "CANCEL"
        denied = service.TransferProjectResource(transfer, None)
        assert not denied.ok and denied.code == "E_RESOURCE_TRANSFER_READY"
        service.projectConfiguration.cancel("cancel")
        release.set()
        assert finish(service.projectConfiguration, "cancel")["state"] == "CANCELLED"
        assert service.TransferProjectResource(transfer, None).ok
        assert service.projectConfiguration.current["documentRevision"] == 1
        assert imagePath(service).read_bytes() == INPUT
    finally:
        release.set()
        service.close()


def testFailedResourceIngestionReleasesQuotaWithoutCommitting(tmp_path, monkeypatch):
    project, _source = resourceProject(tmp_path / "source-project")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        candidate = deepcopy(service.projectConfiguration.current["payload"])
        source = project.parent / "wrong.bin"
        source.write_bytes(b"wrong content")
        candidate["resources"]["items"]["image"]["path"] = source.name
        assert write(service, candidate, "bad-input")["state"] == "FAILED"
        assert service.projectConfiguration.current["documentRevision"] == 1
        with service.sqliteStore.connection() as connection:
            assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone()[0] == 0
        assert Path(service.loadedProjectFile) == project
        assert service.projectResourceTransfers.resolve(ProjectDocument.model_validate(service.loadedPayload))
    finally:
        service.close()


def testUpgradeIngestsExistingOnlineSnapshotBeforeSourceIsRemoved(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.project_config.store import ConfigurationStore
    project, source = resourceProject(tmp_path / "source-project")
    sqlite = SqliteStore(tmp_path / "runtime.db")
    ConfigurationStore(sqlite).initialize(json.loads(project.read_text(encoding="utf-8")), str(project))
    service = RuntimeService(dbPath=sqlite.dbPath, workspaceRoot=tmp_path / "jobs")
    try:
        assert not service.onlineRecoveryError
        assert service.projectResourceTransfers.resolve(service.loadedDocument)["image"].read_bytes() == INPUT
    finally:
        service.close()
    source.unlink()
    project.unlink()
    project.parent.rmdir()
    restored = RuntimeService(dbPath=sqlite.dbPath, workspaceRoot=tmp_path / "jobs")
    try:
        assert not restored.onlineRecoveryError
        assert imagePath(restored).read_bytes() == INPUT
        assert restored.projectConfiguration.current["documentRevision"] == 1
    finally:
        restored.close()


def testResourceCommitFailureReleasesCandidateButKeepsOnlineCopy(tmp_path):
    project, _source = resourceProject(tmp_path / "source-project")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        candidate = deepcopy(service.projectConfiguration.current["payload"])
        candidate["resources"]["items"]["image"] = dict(manifest(REPLACEMENT, "candidate.png"), purpose="input_image")
        transfer = upload(service, candidate, REPLACEMENT)
        with service.sqliteStore.connection() as connection:
            connection.execute("CREATE TRIGGER failReceipt BEFORE INSERT ON projectWriteReceipts BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        assert write(service, candidate, "rollback")["state"] == "FAILED"
        assert service.projectConfiguration.current["documentRevision"] == 1
        assert service.projectConfiguration.store.receipt("rollback") is None
        assert imagePath(service).read_bytes() == INPUT
        with service.sqliteStore.connection() as connection:
            assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone()[0] == 0
        transfer.action = "CANCEL"
        assert service.TransferProjectResource(transfer, None).ok
    finally:
        service.close()


def testFailedLocalPreparationReclaimsOnlyItsAutomaticallyIngestedCopy(tmp_path):
    project, _source = resourceProject(tmp_path / "source-project")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        source = project.parent / "candidate.png"
        source.write_bytes(REPLACEMENT)
        candidate = deepcopy(service.projectConfiguration.current["payload"])
        candidate["resources"]["items"]["image"] = dict(manifest(REPLACEMENT, source.name), purpose="input_image")
        with service.sqliteStore.connection() as connection:
            connection.execute("CREATE TRIGGER failReceipt BEFORE INSERT ON projectWriteReceipts BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        assert write(service, candidate, "local-rollback")["state"] == "FAILED"
        assert len(list(service.projectResourceTransfers.root.iterdir())) == 1
        assert source.read_bytes() == REPLACEMENT
        assert imagePath(service).read_bytes() == INPUT
    finally:
        service.close()


def testRestartReclaimsOrphanedAutoIngestionButKeepsExplicitReadyUpload(tmp_path):
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.project_config.transfer import ResourceTransfers
    from tests.runtime.test_project_resource_transfer import request, chunk
    project, _source = resourceProject(tmp_path / "source-project")
    sqlite = SqliteStore(tmp_path / "runtime.db")
    original = ResourceTransfers(sqlite, tmp_path / "uploads")
    original.execute(request("BEGIN"))
    original.execute(chunk(b"model data"))
    original.execute(request("COMMIT"))
    document = ProjectDocument.model_validate_json(project.read_text(encoding="utf-8"))
    original.pin(document, project.parent)
    assert len(list(original.root.iterdir())) == 2
    restarted = ResourceTransfers(sqlite, original.root)
    try:
        restarted.recover()
        assert len(list(original.root.iterdir())) == 1
        assert restarted.execute(request("STATUS"))["state"] == "READY"
        assert not restarted.resolve(document)
        with sqlite.connection() as connection:
            assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM projectResourceIngestions").fetchone()[0] == 0
    finally:
        restarted.close()
        original.close()
