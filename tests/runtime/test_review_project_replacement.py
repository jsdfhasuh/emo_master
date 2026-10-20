from copy import deepcopy
import json

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectChanges
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import finish
from tests.runtime.test_device_production_outcomes import waitFor


@pytest.fixture
def runtime(tmp_path):
    project = tmp_path / "project.json"
    project.write_text(json.dumps(simulatedProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
    try:
        yield service
    finally:
        service.close()


def replace(service, candidate, requestId="replace", policy="stopAffected", paths=None):
    snapshot = service.projectConfiguration.snapshot()
    request = pb.ProjectConfigurationWriteRequest(request_id=requestId,
        expected_runtime_instance_id=service.runtimeInstanceId, expected_revision=snapshot["documentRevision"],
        local_payload_json=json.dumps(candidate), replacement_policy=policy,
        selected_paths=paths if paths is not None else [c["path"] for c in projectChanges(snapshot["payload"], candidate)])
    reply = service.WriteProjectConfiguration(request, None)
    assert reply.ok, (reply.code, reply.message)
    return finish(service.projectConfiguration, requestId)


def testIdleReplacementResetsAdaptersAndSeparatesHistory(runtime):
    oldId = runtime.loadedProjectId
    oldDriver = runtime.deviceManager.drivers["camera"]
    oldPath = runtime.loadedProjectFile
    candidate = simulatedProject()
    candidate["project"]["projectId"] = "project-B"
    result = replace(runtime, candidate)
    assert result["state"] == "COMMITTED", result
    assert runtime.loadedProjectId == "project-B"
    assert runtime.deviceManager.drivers["camera"] is not oldDriver
    assert not runtime.deviceManager.lifecycles["camera"].connected
    assert runtime.loadedProjectFile != oldPath
    assert runtime.projectConfiguration.store.historicalSnapshot(1)["payload"]["project"]["projectId"] == oldId
    assert [r["documentRevision"] for r in runtime.projectConfiguration.store.history("project-B")] == [2]
    reply = runtime.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(project_id=oldId, document_revision=1), None)
    assert reply.ok, reply.message


def testProjectReplacementRejectsPartialSelectionAndImplicitPolicy(runtime):
    candidate = deepcopy(runtime.loadedPayload)
    candidate["project"]["projectId"] = "project-B"
    candidate["devices"]["definitions"]["camera"]["connection"]["serial"] = "B-ONLY"
    with pytest.raises(AssertionError, match="complete candidate"):
        replace(runtime, candidate, paths=["/project/projectId"])
    with pytest.raises(AssertionError, match="explicit"):
        replace(runtime, candidate, policy="reject")
    assert runtime.projectConfiguration.current["documentRevision"] == 1


@pytest.mark.parametrize("policy", ["stopAffected", "drainAffected"])
def testReplacementRetiresLiveJobsWithoutCarryingConnectionIntent(tmp_path, policy):
    payload = simulatedProject()
    payload["production"].update(mode="continuous", cycleIntervalMs=10)
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        service.productionMode = True
        started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        oldDriver = service.deviceManager.drivers["camera"]
        waitFor(lambda: oldDriver.sequence >= 2, timeout=30)
        candidate = deepcopy(service.loadedPayload)
        candidate["project"]["projectId"] = "project-B"
        result = replace(service, candidate, policy=policy)
        assert result["state"] == "COMMITTED", result
        assert result["affectedJobs"] == [started.job_id]
        assert service.jobRepository.get(started.job_id).isTerminal
        assert not service.jobSupervisor.ownsJobResources(started.job_id)
        assert service.deviceManager.drivers["camera"] is not oldDriver
        assert not service.deviceManager.lifecycles["camera"].connected
        assert service.deviceManager.drivers["camera"].sequence == 0
        assert not service.projectConfiguration.cycles
        reply = service.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(project_id="project-B"), None)
        assert reply.ok and not json.loads(reply.result_json)["jobs"]
        reply = service.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(project_id="project-B", job_id=started.job_id), None)
        assert not reply.ok and reply.code == "E_CONFIG_JOB"
    finally:
        service.close()


def testCandidateResourcesAndPersistentVariablesSurviveReplacementAndRestart(tmp_path):
    from pathlib import Path
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
    from tests.runtime.test_global_variables import variable
    from tests.runtime.test_project_resource_transfer import manifest
    payload = simulatedProject()
    payload["globalVariables"] = {"v": variable(7)}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = tmp_path / "model.bin"
    source.write_bytes(b"candidate-only-resource")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    oldId = payload["project"]["projectId"]
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        original = ProjectGlobalVariables(service.sqliteStore, oldId, payload["globalVariables"])
        original.set("v", 91)
        client = RuntimeClient(runtimeService=service)
        candidate = deepcopy(service.loadedPayload)
        candidate["project"]["projectId"] = "project-B"
        candidate["resources"]["items"]["model"] = manifest(source.read_bytes(), source.name)
        uploaded = client.uploadProjectResources(client.getOnlineProjectSnapshot(), candidate, tmp_path)
        assert uploaded["uploaded"][0]["state"] == "READY"
        assert replace(service, candidate)["state"] == "COMMITTED"
        assert ProjectGlobalVariables(service.sqliteStore, "project-B", payload["globalVariables"]).get("v") == 7
        assert original.get("v") == 91
        assert not Path(service.loadedProjectFile).exists()
    finally:
        service.close()
    source.unlink()
    restored = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not restored.onlineRecoveryError
        assert restored.loadedProjectId == "project-B"
        resources = restored.projectResourceTransfers.resolve(restored.loadedDocument)
        assert Path(resources["model"]).read_bytes() == b"candidate-only-resource"
        assert ProjectGlobalVariables(restored.sqliteStore, "project-B", payload["globalVariables"]).get("v") == 7
        assert ProjectGlobalVariables(restored.sqliteStore, oldId, payload["globalVariables"]).get("v") == 91
    finally:
        restored.close()


@pytest.mark.parametrize("cancel", [False, True])
def testReplacementRollbackPreservesAuthorityAndReleasesPins(runtime, monkeypatch, cancel, tmp_path):
    from threading import Event
    from emo_master.apps.designer.services.runtime_client import RuntimeClient
    from tests.runtime.test_project_resource_transfer import manifest
    before = runtime.projectConfiguration.snapshot()
    candidate = deepcopy(before["payload"])
    candidate["project"]["projectId"] = "project-B"
    data = b"uncommitted-candidate"
    (tmp_path / "candidate.bin").write_bytes(data)
    candidate["resources"]["items"]["candidate"] = manifest(data, "candidate.bin")
    client = RuntimeClient(runtimeService=runtime)
    uploaded = client.uploadProjectResources(client.getOnlineProjectSnapshot(), candidate, tmp_path)
    entered, release = Event(), Event()
    original = runtime.projectConfiguration.prepare
    def prepare(payload, cancelled):
        prepared = original(payload, cancelled)
        entered.set()
        assert release.wait(10)
        return prepared
    monkeypatch.setattr(runtime.projectConfiguration, "prepare", prepare)
    if not cancel:
        with runtime.sqliteStore.connection() as connection:
            connection.execute("CREATE TRIGGER failHistory BEFORE INSERT ON onlineProjectHistory BEGIN SELECT RAISE(ABORT,'rollback'); END")
    request = dict(requestId="rollback", expectedRuntimeInstanceId=runtime.runtimeInstanceId,
        expectedRevision=before["documentRevision"], localPayload=candidate,
        selectedPaths=[c["path"] for c in projectChanges(before["payload"], candidate)], replacementPolicy="stopAffected")
    try:
        runtime.projectConfiguration.start(request)
        assert entered.wait(5)
        with runtime.sqliteStore.connection() as connection:
            assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone()[0] == 1
        if cancel:
            runtime.projectConfiguration.cancel("rollback")
    finally:
        release.set()
    status = finish(runtime.projectConfiguration, "rollback")
    assert status["state"] == ("CANCELLED" if cancel else "FAILED"), status
    assert runtime.projectConfiguration.snapshot() == before
    assert runtime.loadedProjectId == before["payload"]["project"]["projectId"]
    assert runtime.projectConfiguration.store.snapshot()["sourcePath"] == before["sourcePath"]
    with runtime.sqliteStore.connection() as connection:
        assert connection.execute("SELECT count(*) FROM projectResourcePins").fetchone()[0] == 0
    candidateSnapshot = dict(before, payload=candidate)
    status = client.transferProjectResource(candidateSnapshot, uploaded["uploaded"][0]["transferId"], "CANCEL")
    assert status["state"] == "CANCELLED"
