"""Spawn + loopback RPC acceptance for wait, monitoring and consumer capture."""
import json
from threading import Event

import cv2
import grpc
import pytest

from examples.dispatch_queue_demo import saveDemo
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5)
    client = RuntimeClient(rpc.RuntimeServiceStub(channel))
    try:
        yield service, client, rpc.DisplayServiceStub(channel)
    finally:
        channel.close()
        server.stop(0).wait()
        service.close()


def gateConsumers(monkeypatch):
    first, rest = Event(), Event()
    original = RunGroup.next
    def nextItem(group, owner):
        slot = next(s for s in group.slots.values() if s.jobId == owner)
        if not group.stopping and not rest.is_set() and (not first.is_set() or slot.completedTotal >= 1):
            slot.ready = True
            return dict(status="IDLE")
        return original(group, owner)
    monkeypatch.setattr(RunGroup, "next", nextItem)
    return first, rest


def loadDemo(service, tmp_path, **kwargs):
    path = saveDemo(tmp_path / "project", **kwargs)
    loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
    assert loaded.ok, loaded.message
    validated = service.ValidateProject(pb.ValidateProjectRequest(project_id=service.loadedProjectId), None)
    assert validated.ok, validated.errors
    return path


@pytest.mark.parametrize("slots,cancel", [(("detection",), False), (("left", "right"), False), (("detection",), True)])
def testWaitingCaptureAndCancellationThroughRealProcesses(runtime, tmp_path, monkeypatch, slots, cancel):
    service, client, display = runtime
    first, rest = gateConsumers(monkeypatch)
    path = loadDemo(service, tmp_path, iterations=None if cancel else 5, slots=slots)
    project = service.loadedProjectId
    owner = service._presentationOwner
    attach = owner.attachNormal
    def unlockedAttach(jobId, frozen):
        assert not service.jobSupervisor._lock._is_owned()
        return attach(jobId, frozen)
    monkeypatch.setattr(owner, "attachNormal", unlockedAttach)
    specs = []
    start = service.jobSupervisor.startJob
    def startObserved(spec, **kwargs):
        if not spec.dispatchSlots:
            specs.append(spec)
        return start(spec, **kwargs)
    monkeypatch.setattr(service.jobSupervisor, "startJob", startObserved)
    session = service.runInspectionStore.open(service._loadedProjectPreviewKey)
    started = service.StartJob(pb.StartJobRequest(workflow_id="main", capture_presentation=True,
                                                inspection_session_id=session), None)
    assert started.ok, started.message
    assert len(specs) == len(slots) + 1
    assert all(spec.inspectionEnabled == (spec.jobId == started.job_id) for spec in specs)
    assert all(spec.legacySnapshotPolicy == "ALL" for spec in specs)
    group = service.deviceManager.dispatchGroups[started.job_id]
    try:
        waitFor(lambda: bool(group.waiting), timeout=20)
        snapshot = client.dispatchQueueState(project, group.groupId, slots[0], True)
        assert len(snapshot["groups"]) == 1  # deduplicate producer and consumer memberships
        assert len(snapshot["selected"]["slots"]) == len(slots)
        for row in snapshot["selected"]["slots"]:
            assert (row["queued"], row["processing"], row["acceptedTotal"], row["rejectedTotal"]) == (1, 0, 1, 0)
            assert row["waitingProducers"][0]["jobId"] == started.job_id
        item, = snapshot["selected"]["slots"][0]["items"]
        assert item["position"] == 1 and item["frameCount"] == 1
        assert item["frames"][0]["shape"] == [240, 320, 3]
        assert set(item["frames"][0]) <= {"frameId", "shape", "dtype"}
        assert not any("items" in row for row in client.dispatchQueueState(project, group.groupId)["selected"]["slots"])
        assert client.dispatchQueueState(project, "foreign-group")["selected"] is None
        with pytest.raises(RuntimeClientError):
            client.dispatchQueueState("foreign-project", group.groupId)
        assert service.deviceManager.drivers["camera"].sequence == 2  # wait does not recapture
        if cancel:
            result = service.StopJob(pb.StopJobRequest(job_id=started.job_id), None)
            assert result.ok, result.message
            waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
            assert service.jobRepository.get(started.job_id).status == "ABORTED"
            assert all(slot.acceptedTotal == 1 and slot.cancelledTotal == 1 for slot in group.slots.values())
            assert not path.with_name("latest.png").exists()
        else:
            first.set()
            waitFor(lambda: all(slot.completedTotal == 1 for slot in group.slots.values()), timeout=20)
            waitFor(lambda: all(owner.store.snapshot(slot.jobId)["results"] for slot in group.slots.values()), timeout=15)
            metadata = display.ListJobs(pb.DisplayEmpty(project_id=project), timeout=5).jobs
            for key, slot in group.slots.items():
                assert not service.jobRepository.get(slot.jobId).isTerminal
                job = next(j for j in metadata if j.job_id == slot.jobId)
                assert (job.source_job_id, job.run_group_id, job.target_slot_id) == (started.job_id, group.groupId, key)
                result, = owner.store.snapshot(slot.jobId)["results"]
                assert result.status == "COMPLETE", result
                assert result.identity.jobId == slot.jobId
                assert result.identity.resultScopeId == "consumption"
                assert {s.sourceId for s in result.sources} == {"count", "image"}
                assert json.loads(next(s.valueJson for s in result.sources if s.sourceId == "count")) == 1
                assert next(s.image for s in result.sources if s.sourceId == "image") is not None
            assert cv2.imread(str(path.with_name("latest.png"))).shape == (240, 320, 3)
            rest.set()
            assert waitForTerminal(service, started.job_id, timeoutSeconds=30).status == "COMPLETED"
            waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
            assert all(slot.acceptedTotal == slot.completedTotal == 5 and slot.rejectedTotal == 0 for slot in group.slots.values())
            assert all(r.status == "COMPLETED" for r in service.jobRepository.all())
            events = [e for slot in group.slots.values() for e in service._eventsAfter(slot.jobId, 0)
                      if e.eventType == "dispatch.invocation.completed"]
            assert len(events) == 5 * len(slots)
        assert not group.waiting and not group.records and not group.deliveries
        assert not service.deviceManager.frames.frames
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        assert client.dispatchQueueState(project, group.groupId)["state"] == "UNAVAILABLE"
    finally:
        first.set()
        rest.set()


def testConsumerAttachmentFailureRetiresEarlierCaptureWithoutStartingProcesses(runtime, tmp_path, monkeypatch):
    service, _client, _display = runtime
    loadDemo(service, tmp_path, slots=("left", "right"))
    owner = service._presentationOwner
    attach = owner.attachNormal
    attached = []
    def failSecondConsumer(jobId, frozen):
        if len(attached) == 2:
            raise ValueError("consumer capture fixture failure")
        result = attach(jobId, frozen)
        attached.append(jobId)
        return result
    monkeypatch.setattr(owner, "attachNormal", failSecondConsumer)
    started = service.StartJob(pb.StartJobRequest(workflow_id="main", capture_presentation=True), None)
    assert not started.ok and "fixture failure" in started.message
    assert not service.jobSupervisor._handles and not service.deviceManager.dispatchGroups
    assert all(job.isTerminal for job in service.jobRepository.all())
    waitFor(lambda: all(not owner.readers[jobId].is_alive() for jobId in attached), timeout=5)
    for jobId in attached:
        owner.release(jobId)
    assert not owner.jobs and not service.deviceManager.frames.frames


@pytest.mark.parametrize("cause", ["timeout", "consumer-exit"])
def testWaitFailureReleasesProducerAndDrainsExistingWork(runtime, tmp_path, monkeypatch, cause):
    service, client, _display = runtime
    first, rest = gateConsumers(monkeypatch)
    path = saveDemo(tmp_path / "project")
    document = loadProjectDocument(path)
    dispatch = next(n for n in document.workflows["capture"].nodes if n.kind == "dispatch")
    if cause == "timeout":
        dispatch.params["waitTimeoutMs"] = 200
    saveProject(path, document.model_dump(mode="json"))
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    started = service.StartJob(pb.StartJobRequest(workflow_id="main", capture_presentation=True), None)
    assert started.ok, started.message
    group = service.deviceManager.dispatchGroups[started.job_id]
    try:
        waitFor(lambda: bool(group.waiting), timeout=20)
        if cause == "consumer-exit":
            assert service.StopJob(pb.StopJobRequest(job_id=group.slots["detection"].jobId), None).ok
        terminal = waitForTerminal(service, started.job_id, timeoutSeconds=20)
        assert terminal.status == "FAILED"
        assert terminal.error_code == ("E_DISPATCH_WAIT_TIMEOUT" if cause == "timeout" else "E_DISPATCH_UNAVAILABLE")
        assert service.deviceManager.drivers["camera"].sequence == 2
        if cause == "timeout":
            waitFor(lambda: group.sourceEnded)
            state = client.dispatchQueueState(service.loadedProjectId, group.groupId)["selected"]
            assert state["state"] == "DRAINING" and state["slots"][0]["queued"] == 1
            assert state["slots"][0]["lastErrorCode"] == "E_DISPATCH_WAIT_TIMEOUT"
        rest.set()
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
        assert not group.waiting and not service.deviceManager.frames.frames
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
    finally:
        first.set()
        rest.set()


def testTwoConcurrentGroupsStayDistinctOnQueueAndDisplayRPC(runtime, tmp_path, monkeypatch):
    service, client, display = runtime
    first, rest = gateConsumers(monkeypatch)
    path = saveDemo(tmp_path / "project")
    document = loadProjectDocument(path)
    document.runtime.maxConcurrentJobs = 4
    saveProject(path, document.model_dump(mode="json"))
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    started = [service.StartJob(pb.StartJobRequest(workflow_id="main", capture_presentation=True), None) for _ in range(2)]
    assert all(reply.ok for reply in started), started
    groups = [service.deviceManager.dispatchGroups[reply.job_id] for reply in started]
    try:
        waitFor(lambda: all(group.waiting for group in groups), timeout=20)
        state = client.dispatchQueueState(service.loadedProjectId)
        assert len(state["groups"]) == 2 and state["selected"] is None
        for group in groups:
            state = client.dispatchQueueState(service.loadedProjectId, group.groupId)["selected"]
            assert state["sourceJobId"] == group.source
            assert state["slots"][0]["jobId"] == group.slots["detection"].jobId
        metadata = display.ListJobs(pb.DisplayEmpty(project_id=service.loadedProjectId), timeout=5).jobs
        assert {j.run_group_id for j in metadata if j.target_slot_id} == {g.groupId for g in groups}
        for group in groups:
            assert service.StopJob(pb.StopJobRequest(job_id=group.source), None).ok
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
        assert not service.deviceManager.frames.frames
    finally:
        first.set()
        rest.set()
