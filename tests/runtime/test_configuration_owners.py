from copy import deepcopy
import json
import socket
from threading import Event

import pytest

from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectChanges
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_global_variables import variable
from tests.runtime.test_project_configuration import finish


@pytest.fixture
def runtime(tmp_path):
    payload = simulatedProject()
    payload["globalVariables"] = {"count": variable(7, name="count")}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    yield service
    service.close()


def write(service, field, value, *, policy="reject", requestId="owners"):
    snapshot = service.projectConfiguration.snapshot()
    candidate = deepcopy(snapshot["payload"])
    candidate[field] = value
    service.projectConfiguration.start(dict(requestId=requestId, expectedRuntimeInstanceId=service.runtimeInstanceId,
        expectedRevision=snapshot["documentRevision"], localPayload=candidate,
        selectedPaths=[change["path"] for change in projectChanges(snapshot["payload"], candidate)], replacementPolicy=policy))
    return finish(service.projectConfiguration, requestId)


def variables(service):
    return ProjectGlobalVariables(service.sqliteStore, service.loadedProjectId, service.loadedDocument.globalVariables)


def testVariableDefinitionsCommitWithoutResettingPersistentValues(runtime):
    variables(runtime).set("count", 42)
    candidate = dict(count=variable(99, name="renamed"), added=variable(3, name="new"))
    status = write(runtime, "globalVariables", candidate)
    assert status["state"] == "COMMITTED", status
    assert variables(runtime).readMany(["count", "added"]) == dict(count=42, added=3)
    assert runtime.projectConfiguration.store.snapshot()["payload"]["globalVariables"] == candidate
    status = write(runtime, "globalVariables", {"added": candidate["added"]}, requestId="remove")
    assert status["state"] == "COMMITTED", status
    assert "count" not in runtime.loadedDocument.globalVariables
    with runtime.sqliteStore.connection() as connection:
        assert json.loads(connection.execute("SELECT valueJson FROM variableValues WHERE variableId='count'").fetchone()[0]) == 42


def testVariableValidationDoesNotCreateDefinitionsOrValues(runtime):
    candidate = deepcopy(runtime.loadedPayload)
    candidate["globalVariables"]["new"] = variable(1, name="new")
    prepared = runtime._prepareProjectConfiguration(candidate, Event())
    runtime._discardProjectConfiguration(prepared)
    with runtime.sqliteStore.connection() as connection:
        assert not connection.execute("SELECT 1 FROM variableDefinitions WHERE variableId='new'").fetchone()
        assert not connection.execute("SELECT 1 FROM variableValues WHERE variableId='new'").fetchone()


def testReceiptFailureRollsBackVariableDefinitionsAndValues(runtime):
    with runtime.sqliteStore.connection() as connection:
        connection.execute("CREATE TRIGGER denyReceipt BEFORE INSERT ON projectWriteReceipts BEGIN SELECT RAISE(ABORT,'injected'); END")
    result = write(runtime, "globalVariables", {"added": variable(3)})
    assert result["state"] == "FAILED", result
    assert runtime.projectConfiguration.snapshot()["documentRevision"] == 1
    with runtime.sqliteStore.connection() as connection:
        assert not connection.execute("SELECT 1 FROM variableDefinitions WHERE variableId='added'").fetchone()
        assert not connection.execute("SELECT 1 FROM variableValues WHERE variableId='added'").fetchone()
    assert variables(runtime).get("count") == 7


def testVariableTypeChangeRequiresNewIdentityBeforeAnyTransition(runtime, monkeypatch):
    stopped = []
    monkeypatch.setattr(runtime.communicationManager, "stop", lambda: stopped.append(True))
    status = write(runtime, "globalVariables", {"count": variable("text", "string", name="count")}, policy="stopAffected")
    assert status["state"] == "FAILED" and status["code"] == "E_VARIABLE_DEFINITION_CHANGED", status
    assert not stopped and variables(runtime).get("count") == 7


def testRuntimeAndProductionPoliciesPublishAndRecover(runtime, tmp_path):
    policy = runtime.loadedDocument.runtime.model_dump()
    policy.update(maxConcurrentJobs=4, gracefulStopTimeoutMs=1000, heartbeatTimeoutMs=2000, eventRetentionPerJob=500)
    status = write(runtime, "runtime", policy)
    assert status["state"] == "COMMITTED", status
    assert runtime.jobSupervisor.maxConcurrentJobs == 4
    assert runtime.jobSupervisor.gracefulStopTimeoutMs == 1000
    assert runtime.jobSupervisor.heartbeatTimeoutMs == 2000
    assert runtime.eventStore.retentionPerJob == runtime.sqliteStore.jobEventRetention == 500
    production = runtime.loadedDocument.production.model_dump()
    production.update(mode="continuous", cycleIntervalMs=75, autoStart=False)
    assert write(runtime, "production", production, requestId="production")["state"] == "COMMITTED"
    runtime.close()
    recovered = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not recovered.onlineRecoveryError
        assert recovered.loadedDocument.production.model_dump() == production
        assert recovered.jobSupervisor.maxConcurrentJobs == 4
        assert not recovered.jobRepository.all()
    finally:
        recovered.close()


@pytest.mark.parametrize("invalid", [["not.installed"], [{"operatorId": "vision.io.simulated_camera", "version": "missing"}], [42]])
def testInvalidDependencyLocksDoNotCommit(runtime, invalid):
    result = write(runtime, "dependencies", dict(operators=invalid))
    assert result["state"] == "FAILED", result
    assert runtime.projectConfiguration.snapshot()["documentRevision"] == 1


def testInstalledDependencyLocksCommit(runtime):
    key, descriptor = next(iter(runtime.pluginScanResult.activeOperators.items()))
    result = write(runtime, "dependencies", dict(operators=[dict(operatorId=key, version=descriptor.manifest.version)]))
    assert result["state"] == "COMMITTED", result


def communication(service):
    config = service.loadedDocument.communication.model_dump()
    config["enabled"] = True
    # An ephemeral loopback allocation is test-only; no physical endpoint is used.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config["tcpServer"].update(bindAddress="127.0.0.1", port=port,
        channels=[dict(channelId="loopback", name="Loopback", allowedIps=["127.0.0.1"])])
    return config


def testCommunicationReplacementIsHardwareFreeUntilExplicitStart(runtime):
    config = communication(runtime)
    result = write(runtime, "communication", config)
    assert result["state"] == "COMMITTED", result
    state = runtime.communicationManager.snapshot()
    assert state["desired"] == state["effective"] == runtime.loadedDocument.communication.model_dump()
    assert state["state"] == "STOPPED" and not state["ownsResources"] and not state["savePending"]


@pytest.mark.parametrize("policy", ["stopAffected", "drainAffected"])
def testListeningCommunicationReplacementRestartsOnlyAfterCommit(runtime, policy):
    config = communication(runtime)
    assert write(runtime, "communication", config)["state"] == "COMMITTED"
    runtime.communicationManager.start()
    oldGeneration = runtime.communicationManager.serviceGeneration
    replacement = communication(runtime)
    result = write(runtime, "communication", replacement, requestId="listening", policy=policy)
    assert result["state"] == "COMMITTED", result
    assert result["connections"]["communication"] == "LISTENING"
    state = runtime.communicationManager.snapshot()
    assert state["state"] == "LISTENING" and state["serviceGeneration"] > oldGeneration
    assert state["desired"]["tcpServer"]["port"] == replacement["tcpServer"]["port"]


def testListeningCommunicationRejectPolicyLeavesListenerRunning(runtime):
    assert write(runtime, "communication", communication(runtime))["state"] == "COMMITTED"
    runtime.communicationManager.start()
    generation = runtime.communicationManager.serviceGeneration
    result = write(runtime, "communication", communication(runtime), requestId="rejected")
    assert result["state"] == "FAILED" and result["code"] == "E_CONFIG_REQUIRES_STOP", result
    assert runtime.communicationManager.state == "LISTENING"
    assert runtime.communicationManager.serviceGeneration == generation
    assert not runtime.communicationManager.snapshot()["savePending"]


def testCommunicationBindFailureIsSeparateFromCommittedConfiguration(runtime, monkeypatch):
    assert write(runtime, "communication", communication(runtime))["state"] == "COMMITTED"
    runtime.communicationManager.start()
    def fail():
        raise OSError("injected bind failure")
    monkeypatch.setattr(runtime.communicationManager, "start", fail)
    result = write(runtime, "communication", communication(runtime), requestId="failed-bind", policy="stopAffected")
    assert result["state"] == "COMMITTED", result
    assert result["connections"]["communication"] == "FAILED: injected bind failure"
    assert runtime.projectConfiguration.store.receipt("failed-bind")[1]["connections"] == result["connections"]


def testCommunicationTransitionDoesNotStopUnrelatedContinuousCamera(runtime):
    from tests.runtime.test_device_production_outcomes import waitFor
    production = runtime.loadedDocument.production.model_dump()
    production.update(mode="continuous", cycleIntervalMs=20)
    assert write(runtime, "production", production)["state"] == "COMMITTED"
    config = communication(runtime)
    config["startupPolicy"] = "manual"
    assert write(runtime, "communication", config, requestId="tcp-enable")["state"] == "COMMITTED"
    runtime.communicationManager.start()
    runtime.productionMode = True
    started = runtime.StartJob(pb.StartJobRequest(workflow_id="main"), None)
    assert started.ok, started.message
    driver = runtime.deviceManager.drivers["camera"]
    waitFor(lambda: driver.sequence >= 2)
    config["tcpServer"]["name"] = "Replacement listener"
    status = write(runtime, "communication", config, policy="drainAffected", requestId="scoped-tcp")
    assert status["state"] == "COMMITTED", status
    assert not status["affectedJobs"]
    count = driver.sequence
    waitFor(lambda: driver.sequence > count)
    assert runtime.jobRepository.get(started.job_id).status == "RUNNING"
    assert runtime.deviceManager.drivers["camera"] is driver
    candidate = deepcopy(runtime.loadedPayload)
    candidate["runtime"]["maxConcurrentJobs"] = 4
    validation = runtime.ValidateProjectWrite(pb.ProjectConfigurationWriteRequest(
        request_id="validate-only", expected_revision=runtime.projectConfiguration.current["documentRevision"],
        expected_runtime_instance_id=runtime.runtimeInstanceId, local_payload_json=json.dumps(candidate),
        selected_paths=["/runtime/maxConcurrentJobs"], replacement_policy="stopAffected"), None)
    assert validation.ok, validation.message
    impact = json.loads(validation.result_json)
    assert impact["affectedJobs"] == [started.job_id] and not impact["cycleSafe"]
    assert runtime.jobRepository.get(started.job_id).status == "RUNNING"
    assert runtime.jobSupervisor.maxConcurrentJobs != 4


def testExplicitStopDuringConfigurationWriteSupersedesListenerRestart(runtime, monkeypatch):
    assert write(runtime, "communication", communication(runtime))["state"] == "COMMITTED"
    runtime.communicationManager.start()
    entered, release = Event(), Event()
    prepare = runtime.projectConfiguration.prepare
    def paused(payload, cancelled):
        entered.set()
        assert release.wait(10)
        return prepare(payload, cancelled)
    monkeypatch.setattr(runtime.projectConfiguration, "prepare", paused)
    snapshot = runtime.projectConfiguration.snapshot()
    candidate = deepcopy(snapshot["payload"])
    candidate["communication"]["tcpServer"]["name"] = "Updated listener"
    runtime.projectConfiguration.start(dict(requestId="explicit-stop", expectedRuntimeInstanceId=runtime.runtimeInstanceId,
        expectedRevision=snapshot["documentRevision"], localPayload=candidate,
        selectedPaths=["/communication/tcpServer/name"], replacementPolicy="stopAffected"))
    try:
        assert entered.wait(5)
        state = runtime.communicationManager.snapshot()
        reply = runtime.ControlCommunication(pb.CommunicationRequest(protocol_version="1.1", action="STOP",
            runtime_instance_id=runtime.runtimeInstanceId, project_id=runtime.loadedProjectId,
            project_path=state["projectPath"], project_load_generation=state["projectLoadGeneration"],
            expected_config_revision=state["desiredRevision"], request_id="manual-stop"), None)
        assert reply.ok, reply.message
        release.set()
        status = finish(runtime.projectConfiguration, "explicit-stop")
        assert status["state"] == "COMMITTED", status
        assert status["connections"]["communication"] == "SUPERSEDED"
        assert runtime.communicationManager.state == "STOPPED"
    finally:
        release.set()
