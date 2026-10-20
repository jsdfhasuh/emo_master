from copy import deepcopy
import json

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_project_configuration import finish


def testExplicitIterationDrainStopsAtBoundaryWithoutWaitingForInfiniteRoot(tmp_path):
    from tests.runtime.test_loop_cycle_configuration import loopProject
    path = tmp_path / "project.json"
    path.write_text(json.dumps(loopProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        started = service.StartJob(pb.StartJobRequest(workflow_id="main", inputs_json='{"count":0}'), None)
        assert started.ok
        previous = service.deviceManager.drivers["camera"]
        waitFor(lambda: previous.sequence >= 3, timeout=15)
        snapshot = service.projectConfiguration.snapshot()
        candidate = deepcopy(snapshot["payload"])
        candidate["devices"]["definitions"]["camera"]["connection"]["serial"] = "REPLACEMENT"
        service.projectConfiguration.start(dict(requestId="iteration-drain", expectedRuntimeInstanceId=service.runtimeInstanceId,
            expectedRevision=snapshot["documentRevision"], localPayload=candidate,
            selectedPaths=["/devices/definitions/camera/connection/serial"], replacementPolicy="drainAffected"))
        status = finish(service.projectConfiguration, "iteration-drain")
        assert status["state"] == "COMMITTED", status
        assert service.jobRepository.get(started.job_id).isTerminal
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        assert not service.projectConfiguration.parkedOwners and not service.projectConfiguration.drainingOwners
        assert service.deviceManager.drivers["camera"] is not previous
        assert service.deviceManager.drivers["camera"].sequence == 0
    finally:
        service.close()


@pytest.mark.parametrize("policy", ["stopAffected", "drainAffected"])
def testExplicitTransitionOnlyStopsAffectedSyntheticJob(tmp_path, policy):
    payload = simulatedProject()
    payload["production"].update(mode="continuous", cycleIntervalMs=10)
    payload["devices"]["definitions"]["other"] = deepcopy(payload["devices"]["definitions"]["camera"])
    payload["devices"]["definitions"]["other"]["connection"]["serial"] = "OTHER"
    payload["workflows"]["other"] = deepcopy(payload["workflows"]["main"])
    payload["workflows"]["other"]["nodes"][1]["deviceBindings"]["camera"]["deviceId"] = "other"
    payload["workflowOrder"].append("other")
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        service.productionMode = True
        main = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        other = service.StartJob(pb.StartJobRequest(workflow_id="other"), None)
        assert main.ok and other.ok
        waitFor(lambda: all(d.sequence >= 2 for d in service.deviceManager.drivers.values()), timeout=30)
        previous = service.deviceManager.drivers["camera"]
        unrelated = service.deviceManager.drivers["other"]
        snapshot = service.projectConfiguration.snapshot()
        candidate = deepcopy(snapshot["payload"])
        candidate["devices"]["definitions"]["camera"]["connection"]["serial"] = "REPLACEMENT"
        request = pb.ProjectConfigurationWriteRequest(request_id="transition",
            expected_runtime_instance_id=service.runtimeInstanceId, expected_revision=snapshot["documentRevision"],
            local_payload_json=json.dumps(candidate), selected_paths=["/devices/definitions/camera/connection/serial"],
            replacement_policy=policy)
        assert service.WriteProjectConfiguration(request, None).ok
        status = finish(service.projectConfiguration, "transition")
        assert status["state"] == "COMMITTED", status
        waitFor(lambda: service.projectConfiguration.status("transition").get("connections", {}).get("camera") == "CONNECTED")
        saved = service.projectConfiguration.store.receipt("transition")[1]
        assert saved["connections"]["camera"] == "CONNECTED"
        assert service.jobRepository.get(main.job_id).isTerminal
        assert service.jobRepository.get(other.job_id).status == "RUNNING"
        assert service.deviceManager.drivers["other"] is unrelated
        assert service.deviceManager.drivers["camera"] is not previous
        assert service.deviceManager.drivers["camera"].sequence == 0
        assert not service.projectConfiguration.drainingOwners
    finally:
        service.close()


def testQueuedReconnectIsSupersededBeforeFifoExecution(tmp_path):
    from threading import Event
    from emo_master.apps.runtime.project_config.transition import DeviceTransition
    path = tmp_path / "project.json"
    path.write_text(json.dumps(simulatedProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    entered, release = Event(), Event()
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        manager = service.deviceManager
        executor = manager.executors["camera"]
        def block(_cancel, _deadline, _generation):
            entered.set()
            assert release.wait(10)
        operation = executor.submit("test-barrier", block, waitTimeout=10, operationTimeout=10)
        assert entered.wait(5)
        transition = DeviceTransition.__new__(DeviceTransition)
        transition.runtime, transition.configuration = service, service.projectConfiguration
        transition.owners, transition.previous = set(), {"camera": (True, False)}
        transition.communicationChanged = transition.communicationListening = False
        status = dict(state="COMMITTED", executionRevision=1, requestId="prior-intent")
        transition.finish(status)
        candidate = deepcopy(service.loadedPayload)
        candidate["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
        service.projectConfiguration.start(dict(requestId="newer", expectedRuntimeInstanceId=service.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
        assert finish(service.projectConfiguration, "newer")["state"] == "COMMITTED"
        release.set()
        executor.wait(operation)
        waitFor(lambda: status["connections"]["camera"] != "PENDING")
        assert status["connections"]["camera"] == "SUPERSEDED"
        assert not manager.lifecycles["camera"].connected
        assert manager.drivers["camera"].sequence == 0
    finally:
        release.set()
        service.close()
