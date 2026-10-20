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


def tcpDevices(service):
    from tests.runtime.test_tcp_device_integration import definition
    catalog = service.loadedDocument.devices.model_dump()
    server = definition(service.pluginScanResult.deviceTypes['device.tcp.server'])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        server['connection'].update(bindAddress='127.0.0.1', port=sock.getsockname()[1],
            channels=[dict(channelId='loopback', name='Loopback', allowedIps=['127.0.0.1'])])
    server['policies']['connect'] = 'manual'
    from emo_master.core.project.devices import DeviceDefinition
    catalog['definitions']['tcp'] = DeviceDefinition.model_validate(server).model_dump()
    return catalog


def tcpBackend(service):
    return service.deviceManager.drivers['tcp'].backend


def tcpControl(service, action):
    from emo_master.clients.runtime.devices import DeviceManagementClient
    client = DeviceManagementClient(service)
    runtimeId, states = client.getDeviceStates(service.loadedProjectId)
    return client.controlDevice(service.loadedProjectId, runtimeId, 'tcp', states['tcp'], action, confirmed=True)


def settledReceipt(service, requestId):
    from tests.runtime.test_device_production_outcomes import waitFor
    waitFor(lambda: all(value != 'PENDING' for value in
        service.projectConfiguration.status(requestId).get('connections', {}).values()))
    return service.projectConfiguration.status(requestId)


def testTcpDeviceReplacementIsHardwareFreeUntilExplicitStart(runtime):
    config = tcpDevices(runtime)
    result = write(runtime, 'devices', config)
    assert result['state'] == 'COMMITTED', result
    state = tcpBackend(runtime).snapshot()
    assert state['state'] == 'STOPPED' and not state['ownsResources'] and not state['savePending']
    assert runtime.loadedDocument.devices.model_dump() == config


@pytest.mark.parametrize('policy', ['stopAffected', 'drainAffected'])
def testListeningTcpDeviceReplacementRestartsOnlyAfterCommit(runtime, policy):
    assert write(runtime, 'devices', tcpDevices(runtime))['state'] == 'COMMITTED'
    tcpControl(runtime, 'connect')
    oldBackend = tcpBackend(runtime)
    replacement = tcpDevices(runtime)
    result = write(runtime, 'devices', replacement, requestId='listening', policy=policy)
    assert result['state'] == 'COMMITTED', result
    result = settledReceipt(runtime, 'listening')
    assert result['connections']['tcp'] == 'LISTENING', result
    state = tcpBackend(runtime).snapshot()
    assert state['state'] == 'LISTENING' and tcpBackend(runtime) is not oldBackend
    assert not oldBackend.snapshot()['ownsResources']
    assert state['desired']['tcpServer']['port'] == replacement['definitions']['tcp']['connection']['port']


def testListeningTcpRejectPolicyLeavesListenerRunning(runtime):
    assert write(runtime, 'devices', tcpDevices(runtime))['state'] == 'COMMITTED'
    tcpControl(runtime, 'connect')
    backend = tcpBackend(runtime)
    result = write(runtime, 'devices', tcpDevices(runtime), requestId='rejected')
    assert result['state'] == 'FAILED' and result['code'] == 'E_CONFIG_REQUIRES_STOP', result
    assert tcpBackend(runtime) is backend and backend.state == 'LISTENING'
    assert not backend.snapshot()['savePending']


def testTcpBindFailureIsSeparateFromCommittedConfiguration(runtime):
    assert write(runtime, 'devices', tcpDevices(runtime))['state'] == 'COMMITTED'
    tcpControl(runtime, 'connect')
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        replacement = tcpDevices(runtime)
        replacement['definitions']['tcp']['connection']['port'] = occupied.getsockname()[1]
        result = write(runtime, 'devices', replacement, requestId='failed-bind', policy='stopAffected')
        assert result['state'] == 'COMMITTED', result
        result = settledReceipt(runtime, 'failed-bind')
        assert result['connections']['tcp'].startswith('FAILED:'), result
        assert runtime.projectConfiguration.store.receipt('failed-bind')[1]['connections'] == result['connections']


def testTcpDeviceTransitionDoesNotStopUnrelatedContinuousCamera(runtime):
    from tests.runtime.test_device_production_outcomes import waitFor
    production = runtime.loadedDocument.production.model_dump()
    production.update(mode='continuous', cycleIntervalMs=20)
    assert write(runtime, 'production', production)['state'] == 'COMMITTED'
    assert write(runtime, 'devices', tcpDevices(runtime), requestId='tcp-enable')['state'] == 'COMMITTED'
    tcpControl(runtime, 'connect')
    runtime.productionMode = True
    started = runtime.StartJob(pb.StartJobRequest(workflow_id='main'), None)
    assert started.ok, started.message
    driver = runtime.deviceManager.drivers['camera']
    waitFor(lambda: driver.sequence >= 2)
    status = write(runtime, 'devices', tcpDevices(runtime), policy='drainAffected', requestId='scoped-tcp')
    assert status['state'] == 'COMMITTED', status
    status = settledReceipt(runtime, 'scoped-tcp')
    assert status['connections']['tcp'] == 'LISTENING' and not status['affectedJobs'], status
    count = driver.sequence
    waitFor(lambda: driver.sequence > count)
    assert runtime.jobRepository.get(started.job_id).status == 'RUNNING'
    assert runtime.deviceManager.drivers['camera'] is driver
    candidate = deepcopy(runtime.loadedPayload)
    candidate['runtime']['maxConcurrentJobs'] = 4
    validation = runtime.ValidateProjectWrite(pb.ProjectConfigurationWriteRequest(
        request_id='validate-only', expected_revision=runtime.projectConfiguration.current['documentRevision'],
        expected_runtime_instance_id=runtime.runtimeInstanceId, local_payload_json=json.dumps(candidate),
        selected_paths=['/runtime/maxConcurrentJobs'], replacement_policy='stopAffected'), None)
    assert validation.ok, validation.message
    impact = json.loads(validation.result_json)
    assert impact['affectedJobs'] == [started.job_id] and not impact['cycleSafe']
    assert runtime.jobRepository.get(started.job_id).status == 'RUNNING'
    assert runtime.jobSupervisor.maxConcurrentJobs != 4


def testExplicitStopDuringConfigurationWriteSupersedesTcpRestart(runtime, monkeypatch):
    assert write(runtime, 'devices', tcpDevices(runtime))['state'] == 'COMMITTED'
    tcpControl(runtime, 'connect')
    entered, release = Event(), Event()
    prepare = runtime.projectConfiguration.prepare
    def paused(payload, cancelled):
        entered.set()
        assert release.wait(10)
        return prepare(payload, cancelled)
    monkeypatch.setattr(runtime.projectConfiguration, 'prepare', paused)
    snapshot = runtime.projectConfiguration.snapshot()
    candidate = deepcopy(snapshot['payload'])
    candidate['devices'] = tcpDevices(runtime)
    runtime.projectConfiguration.start(dict(requestId='explicit-stop', expectedRuntimeInstanceId=runtime.runtimeInstanceId,
        expectedRevision=snapshot['documentRevision'], localPayload=candidate,
        selectedPaths=['/devices/definitions/tcp/connection/port'], replacementPolicy='stopAffected'))
    try:
        assert entered.wait(5)
        tcpControl(runtime, 'disconnect')
        release.set()
        status = finish(runtime.projectConfiguration, 'explicit-stop')
        assert status['state'] == 'COMMITTED', status
        assert status['connections']['tcp'] == 'SUPERSEDED', status
        assert tcpBackend(runtime).state == 'STOPPED'
        assert runtime.deviceManager.lifecycles['tcp'].paused
    finally:
        release.set()
