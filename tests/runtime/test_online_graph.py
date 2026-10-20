from copy import deepcopy
import json

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import finish, request, authority
from tests.runtime.runtime_test_utils import waitForTerminal


def testHistorySurvivesCommitAndFailedCommitDoesNotLeaveSnapshot(tmp_path):
    coordinator = authority(tmp_path)
    coordinator.start(request(coordinator))
    assert finish(coordinator)["state"] == "COMMITTED"
    store = coordinator.store
    assert store.historicalSnapshot(1)["payload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] == 8000
    assert store.historicalSnapshot(2)["payload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] == 12000
    with store.sqlite.connection() as connection:
        connection.execute("CREATE TRIGGER failHistory BEFORE INSERT ON onlineProjectHistory BEGIN SELECT RAISE(ABORT,'history failure'); END")
    coordinator.start(request(coordinator, "failure", 14000))
    assert finish(coordinator, "failure")["state"] == "FAILED"
    assert store.snapshot()["documentRevision"] == 2
    assert len(store.history()) == 2
    coordinator.close()


def testOnlineGraphResultsUseHistoricalSnapshotNotLatest(tmp_path):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(simulatedProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok
        assert waitForTerminal(service, started.job_id, timeoutSeconds=30).status == "COMPLETED"
        snapshot = service.projectConfiguration.snapshot()
        candidate = deepcopy(snapshot["payload"])
        candidate["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 16000
        service.projectConfiguration.start(dict(requestId="update", expectedRuntimeInstanceId=service.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
        assert finish(service.projectConfiguration, "update")["state"] == "COMMITTED"
        old = service.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(document_revision=1, job_id=started.job_id), None)
        assert old.ok, old.message
        result = json.loads(old.result_json)
        assert result["events"] and all(e["payload"]["documentRevision"] == 1 for e in result["events"])
        assert result["snapshot"]["payload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] == 8000
        latest = service.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(job_id=started.job_id), None)
        assert latest.ok, latest.message
        assert not json.loads(latest.result_json)["events"]
        missing = service.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(document_revision=999), None)
        assert not missing.ok and missing.code == "E_CONFIG_HISTORY_UNAVAILABLE"
    finally:
        service.close()


def testDesignerTransportDisconnectDoesNotStopProductionOrReplaceHistory(tmp_path):
    import grpc
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
    from emo_master.apps.runtime.main import createRuntimeServer
    from tests.runtime.test_device_production_outcomes import waitFor
    payload = simulatedProject()
    payload["production"].update(mode="continuous", cycleIntervalMs=20)
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        service.productionMode = True
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            grpc.channel_ready_future(channel).result(5)
            client = runtime_pb2_grpc.RuntimeServiceStub(channel)
            started = client.StartJob(pb.StartJobRequest(workflow_id="main"), timeout=5)
            assert started.ok, started.message
        driver = service.deviceManager.drivers["camera"]
        waitFor(lambda: driver.sequence >= 3, timeout=30)
        count = driver.sequence
        waitFor(lambda: driver.sequence > count + 2)
        assert service.jobRepository.get(started.job_id).status == "RUNNING"
        service.projectConfiguration.start(request(service.projectConfiguration, "while-disconnected"))
        assert finish(service.projectConfiguration, "while-disconnected")["state"] == "COMMITTED"
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = runtime_pb2_grpc.RuntimeServiceStub(channel)
            old = client.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(document_revision=1, job_id=started.job_id), timeout=5)
            assert old.ok, old.message
            snapshot = json.loads(old.result_json)
            assert snapshot["readOnly"] and snapshot["events"]
            assert all(event["payload"]["documentRevision"] == 1 for event in snapshot["events"])
            assert snapshot["snapshot"]["payload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] == 8000
        assert service.jobRepository.get(started.job_id).status == "RUNNING"
    finally:
        server.stop(0).wait()
        service.close()
