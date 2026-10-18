from copy import deepcopy
from threading import Event, Thread
import json
import time

import pytest
import grpc

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.project_config.store import ConfigurationStore, ConfigurationError
from emo_master.apps.runtime.project_config.coordinator import ConfigurationCoordinator
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from tests.runtime.test_device_runtime import simulatedProject


def authority(tmp_path, **kwargs):
    store = ConfigurationStore(SqliteStore(tmp_path / "runtime.db"))
    store.initialize(simulatedProject(), str(tmp_path / "project.json"))
    return ConfigurationCoordinator(store, "instance", prepare=kwargs.get("prepare", lambda payload, cancelled: payload),
                                    publish=kwargs.get("publish", lambda prepared, receipt: None))


def request(coordinator, requestId="write-1", exposure=12000):
    snapshot = coordinator.snapshot()
    payload = deepcopy(snapshot["payload"])
    payload["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = exposure
    return dict(requestId=requestId, expectedRuntimeInstanceId=snapshot["runtimeInstanceId"],
                expectedRevision=snapshot["documentRevision"], localPayload=payload,
                selectedPaths=["/devices/definitions/camera/defaults/exposureUs"])


def finish(coordinator, requestId="write-1"):
    coordinator.thread.join(5)
    assert not coordinator.thread.is_alive()
    return coordinator.status(requestId)


def testAtomicCommitReceiptAndRestartRecovery(tmp_path):
    c = authority(tmp_path)
    try:
        write = request(c)
        c.start(write)
        receipt = finish(c)
        assert receipt["state"] == "COMMITTED"
        assert receipt["documentRevision"] == receipt["executionRevision"] == 2
        assert c.start(write) == receipt
        recovered = ConfigurationCoordinator(c.store, "restarted", prepare=lambda p, e: p, publish=lambda p, r: None)
        assert recovered.snapshot()["payload"] == c.snapshot()["payload"]
        assert recovered.start(write) == receipt
        stale = dict(write, requestId="new-id")
        with pytest.raises(ConfigurationError, match="restarted"):
            recovered.start(stale)
        with pytest.raises(ConfigurationError, match="content changed"):
            c.start(dict(write, selectedPaths=["/other"]))
    finally:
        c.close()


def testOneWriterCancellationAndNoPublicationBeforeCommit(tmp_path):
    entered, release = Event(), Event()
    publications = []
    def prepare(payload, cancelled):
        entered.set()
        assert release.wait(5)
        return payload
    c = authority(tmp_path, prepare=prepare, publish=lambda p, r: publications.append(p))
    try:
        c.start(request(c))
        assert entered.wait(2)
        assert c.snapshot()["documentRevision"] == 1
        with pytest.raises(ConfigurationError, match="pending"):
            c.start(request(c, "competitor"))
        c.cancel("write-1")
        release.set()
        assert finish(c)["state"] == "CANCELLED"
        assert not publications and c.store.snapshot()["documentRevision"] == 1
    finally:
        release.set()
        c.close()


def testSqliteFailureRollsBackConfigurationAndReceipt(tmp_path):
    c = authority(tmp_path)
    try:
        with c.store.sqlite.connection() as connection:
            connection.execute("CREATE TRIGGER failReceipt BEFORE INSERT ON projectWriteReceipts BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        c.start(request(c))
        assert finish(c)["state"] == "FAILED"
        assert c.store.snapshot()["documentRevision"] == c.snapshot()["documentRevision"] == 1
        assert c.store.receipt("write-1") is None
    finally:
        c.close()


def testLateCancelReturnsCommitted(tmp_path):
    publications = []
    c = authority(tmp_path, publish=lambda p, r: publications.append(r))
    try:
        c.start(request(c))
        receipt = finish(c)
        assert c.cancel("write-1") == receipt
        assert len(publications) == 1
    finally:
        c.close()


def testPublishFailureCannotEraseDurableCommit(tmp_path):
    def broken(payload, receipt):
        raise RuntimeError("injected publication failure")
    c = authority(tmp_path, publish=broken)
    try:
        c.start(request(c))
        assert finish(c)["state"] == "COMMITTED"
        assert "publicationError" in c.status("write-1")
        assert c.store.snapshot()["documentRevision"] == 2
        assert c.store.receipt("write-1")[1]["state"] == "COMMITTED"
    finally:
        c.close()


def testCleanupFailurePreservesCommittedReceiptAndFencesNewWrites(tmp_path):
    c = authority(tmp_path)
    def fail(_prepared):
        raise RuntimeError("injected disposal failure")
    c.discard = fail
    try:
        write = request(c)
        c.start(write)
        result = finish(c)
        assert result["state"] == "COMMITTED"
        assert result["cleanupError"] == "injected disposal failure"
        assert c.store.snapshot()["documentRevision"] == 2
        assert c.start(write)["state"] == "COMMITTED"
        with pytest.raises(ConfigurationError, match="requires recovery"):
            c.start(request(c, "new-write"))
        with pytest.raises(ConfigurationError, match="unavailable"):
            c.beginCycle("job", 2)
    finally:
        c.close()


def testIndependentSqliteWritersCompareAtCommitPoint(tmp_path):
    c = authority(tmp_path)
    try:
        candidate = request(c)["localPayload"]
        c.store.commit(1, candidate, "a", "digest-a", "instance")
        with pytest.raises(ConfigurationError, match="revision changed"):
            c.store.commit(1, candidate, "b", "digest-b", "instance")
        assert c.store.receipt("b") is None
    finally:
        c.close()


def testOldCycleKeepsPlanWhileNewCycleUsesCommittedRevision(tmp_path):
    c = authority(tmp_path)
    try:
        old = c.beginCycle("old", 1)
        assert "payload" not in old
        c.start(request(c))
        assert finish(c)["state"] == "COMMITTED"
        new = c.beginCycle("new", 1)
        assert new["planRevision"] == 2 and old["planRevision"] == 1
        assert new["cycleId"] != old["cycleId"]
        assert new["payload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] == 12000
        assert c.requireCycle("old", old["cycleId"]) == 1
        with pytest.raises(ConfigurationError, match="matching"):
            c.requireCycle("new", old["cycleId"])
        c.endCycle("new", new["cycleId"])
        assert "old" in c.cycles
        c.retireOwner("old")
        assert not c.cycles
    finally:
        c.close()


def testCycleIdentityIsInheritedByNestedCalls():
    from emo_master.apps.runtime.workflow.context import RunContext
    root = RunContext.root("job", "main", cycleId="cycle", planRevision=7)
    child = root.forNode("call").childWorkflow("child", "call").forIteration(2).forNode("capture")
    assert child.workflowRunId != root.workflowRunId
    assert (child.cycleId, child.planRevision) == ("cycle", 7)


def testRetirementAndCancelledAdmissionDoNotWaitForProjectLock(tmp_path):
    c = authority(tmp_path)
    try:
        c.beginCycle("worker", 1)
        retired = Event()
        def retire():
            c.retireOwner("worker")
            retired.set()
        errors = []
        def admit():
            try:
                c.beginCycle("new", 1, cancelled=lambda: True)
            except ConfigurationError as error:
                errors.append(error)
        with c.lock:
            retirement = Thread(target=retire)
            retirement.start()
            assert retired.wait(1)
            admission = Thread(target=admit)
            admission.start()
            admission.join(1)
            assert not admission.is_alive()
        retirement.join(1)
        assert errors and not c.cycles
    finally:
        c.close()


def testSpawnedContinuousCyclesApplyNextRevisionWithoutRestartingDriver(tmp_path):
    payload = simulatedProject()
    payload["production"]["mode"] = "continuous"
    payload["production"]["cycleIntervalMs"] = 10
    payload["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 100000
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        reply = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert reply.ok, reply.message
        # Exercise the real continuous spawn path, not deployment resource preparation.
        service.productionMode = True
        driver = service.deviceManager.drivers["camera"]
        execute = driver.execute
        parameters = []
        def record(action, params, cancelled, deadline):
            parameters.append(params["exposureUs"])
            return execute(action, params, cancelled, deadline)
        driver.execute = record
        started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
        assert started.ok, started.message
        deadline = time.monotonic() + 10
        while not parameters and time.monotonic() < deadline:
            time.sleep(.01)
        assert parameters and parameters[0] == 100000
        c = service.projectConfiguration
        write = request(c, exposure=12000)
        c.start(write)
        assert finish(c)["state"] == "COMMITTED"
        deadline = time.monotonic() + 10
        while 12000 not in parameters and time.monotonic() < deadline:
            time.sleep(.01)
        assert 12000 in parameters, service.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), None)
        assert service.deviceManager.drivers["camera"] is driver
        revisions = set()
        deadline = time.monotonic() + 5
        while not {1, 2} <= revisions and time.monotonic() < deadline:
            events = service._eventsAfter(started.job_id, 0)
            revisions = {json.loads(event.payloadJson).get("planRevision") for event in events if event.eventType == "workflow.started"}
            time.sleep(.01)
        assert {1, 2} <= revisions
    finally:
        service.close()
    assert not service.projectConfiguration.cycles


def testRuntimeSelectedWriteAndRecoveryDoesNotReadChangedSourceFile(tmp_path):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(simulatedProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    projectId = ""
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        client = RuntimeClient(runtimeService=service)
        snapshot = client.getOnlineProjectSnapshot()
        projectId = snapshot["payload"]["project"]["projectId"]
        local = deepcopy(snapshot["payload"])
        local["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
        local["workflows"]["main"]["nodes"].append(dict(nodeId="unfinished", operatorId="nonexistent"))
        paths = ["/devices/definitions/camera/defaults/exposureUs"]
        checked = client.writeProjectConfiguration(snapshot, local, paths, "rpc-write", validate=True)
        assert len(checked["candidate"]["workflows"]["main"]["nodes"]) == 3
        client.writeProjectConfiguration(snapshot, local, paths, "rpc-write")
        assert finish(service.projectConfiguration, "rpc-write")["state"] == "COMMITTED"
        assert service.deviceManager.drivers["camera"].sequence == 0
        assert service.loadedDocument.devices.definitions["camera"].defaults["exposureUs"] == 12000
        assert not service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        path.write_text("not a project anymore", encoding="utf-8")
    finally:
        service.close()
    recovered = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not recovered.onlineRecoveryError
        assert recovered.loadedProjectId == projectId
        assert recovered.loadedDocument.devices.definitions["camera"].defaults["exposureUs"] == 12000
        assert recovered.deviceManager.drivers["camera"].sequence == 0
        assert recovered.projectConfiguration.status("rpc-write")["state"] == "COMMITTED"
    finally:
        recovered.close()


def testOrdinaryParameterUpdateStartsAtNextCycleWithoutRestartingJob(tmp_path):
    payload = simulatedProject()
    payload["production"].update(mode="continuous", cycleIntervalMs=20)
    payload["workflows"]["main"]["nodes"].append(dict(nodeId="threshold-number",
        operatorId="vision.value.number", params=dict(value=12)))
    path = tmp_path / "project.emoproj"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        service.productionMode = True
        started = service.StartJob(pb.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
        assert started.ok, started.message

        def values():
            return [json.loads(event.payloadJson) for event in service._eventsAfter(started.job_id, 0)
                    if event.eventType == "node.completed" and event.nodeId == "threshold-number"]

        until = time.monotonic() + 10
        while not values() and time.monotonic() < until:
            time.sleep(.02)
        assert values()[0]["outputs"]["value"] == 12
        coordinator = service.projectConfiguration
        snapshot = coordinator.snapshot()
        local = deepcopy(snapshot["payload"])
        node = next(node for node in local["workflows"]["main"]["nodes"] if node["nodeId"] == "threshold-number")
        node["params"]["value"] = 34
        node["displayName"] = "Updated threshold"
        coordinator.start(dict(requestId="ordinary-parameter", expectedRuntimeInstanceId=service.runtimeInstanceId,
            expectedRevision=snapshot["documentRevision"], localPayload=local,
            selectedPaths=["/workflows/main/nodes/threshold-number/params/value",
                           "/workflows/main/nodes/threshold-number/displayName"]))
        assert finish(coordinator, "ordinary-parameter")["state"] == "COMMITTED"
        until = time.monotonic() + 10
        while not any(value["outputs"]["value"] == 34 for value in values()) and time.monotonic() < until:
            time.sleep(.02)
        outputs = values()
        assert {12, 34} <= {value["outputs"]["value"] for value in outputs}
        assert all(value["outputs"]["value"] == (12 if value["planRevision"] == 1 else 34) for value in outputs)
        assert len({value["cycleId"] for value in outputs}) == len(outputs)
        assert service.GetJobStatus(pb.GetJobStatusRequest(job_id=started.job_id), None).status == "RUNNING"
    finally:
        service.close()


def testRemoteWriteAndLegacyMutationFence(tmp_path):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(simulatedProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
        stub = runtime_pb2_grpc.RuntimeServiceStub(channel)
        assert stub.LoadProject(pb.LoadProjectRequest(project_path=str(path)), timeout=5).ok
        client = RuntimeClient(stub)
        snapshot = client.getOnlineProjectSnapshot()
        local = deepcopy(snapshot["payload"])
        local["devices"]["definitions"]["camera"]["defaults"]["gain"] = 4
        client.writeProjectConfiguration(snapshot, local, ["/devices/definitions/camera/defaults/gain"], "remote-write")
        assert finish(service.projectConfiguration, "remote-write")["state"] == "COMMITTED"
        assert client.getProjectWriteStatus("remote-write")["documentRevision"] == 2
        state = service.communicationManager.snapshot()
        before = service.loadedDocument.model_dump(mode="json")
        for action in ("SYNC", "UNLOAD"):
            rejected = stub.ControlCommunication(pb.CommunicationRequest(protocol_version="1.1",
                runtime_instance_id=service.runtimeInstanceId, project_id=service.loadedProjectId,
                project_path=str(path), project_load_generation=state["projectLoadGeneration"], request_id="old-" + action,
                action=action, expected_config_revision=state["desiredRevision"], config_json="{}"), timeout=5)
            assert not rejected.ok and rejected.code == "E_CONFIG_UNIFIED_WRITE", rejected
        assert before == service.loadedDocument.model_dump(mode="json")
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()
