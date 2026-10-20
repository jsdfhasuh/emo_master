"""Dispatch group visibility must cover every live consumer and retirement write."""
import json
from pathlib import Path
from threading import Event

import pytest

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.event_bridge import EventBridge
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_frame_dispatch import dispatchProject


def failingGroup(tmp_path, monkeypatch, failSlot="right"):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(dispatchProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    start = service.jobSupervisor.startJob

    def failSecond(spec):
        if spec.targetSlotId == failSlot:
            raise RuntimeError("injected consumer startup failure")
        return start(spec)

    monkeypatch.setattr(service.jobSupervisor, "startJob", failSecond)
    return service


def testFailedGroupRetainsSharedSnapshotUntilStartedConsumerRetires(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    run = EventBridge._run

    def gated(bridge):
        entered.set()
        assert release.wait(10)
        run(bridge)

    monkeypatch.setattr(EventBridge, "_run", gated)
    service = failingGroup(tmp_path, monkeypatch)
    try:
        reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert not reply.ok and "injected consumer" in reply.message
        assert entered.wait(2)
        snapshot = Path(service.workspaceRoot) / reply.job_id / "project.json"
        assert snapshot.exists(), "a started consumer still owns the producer snapshot"
        group = service.deviceManager.dispatchGroups[reply.job_id]
        assert group.retired < group.members
        assert set(service.deviceManager.dispatchGroups) == group.members
        release.set()
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
        assert not service.jobSupervisor._ownedJobs()
        assert not snapshot.exists()
    finally:
        release.set()
        service.close()


def testGroupStaysVisibleThroughFinalRetirementEventPersistence(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    service = failingGroup(tmp_path, monkeypatch)
    append = service.eventStore.append

    def gated(jobId, eventType, *args, **kwargs):
        if eventType == "dispatch.group.retired":
            entered.set()
            assert release.wait(10)
        return append(jobId, eventType, *args, **kwargs)

    monkeypatch.setattr(service.eventStore, "append", gated)
    try:
        reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert not reply.ok and "injected consumer" in reply.message
        assert entered.wait(10)
        assert service.deviceManager.dispatchGroups, "retirement persistence still owns the group"
        assert service.jobSupervisor._ownedJobs()
        release.set()
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=15)
        assert not service.jobSupervisor._ownedJobs()
        assert len([event for event in service.eventStore.read(reply.job_id)
                    if event.eventType == "dispatch.group.retired"]) == 1
    finally:
        release.set()
        service.close()


def testFailureBeforeFirstConsumerStartsLeavesNoDispatchOwners(tmp_path, monkeypatch):
    service = failingGroup(tmp_path, monkeypatch, failSlot="left")
    try:
        reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert not reply.ok and "injected consumer" in reply.message
        assert not service.deviceManager.dispatchGroups
        assert not service.jobSupervisor._ownedJobs()
        assert all(record.isTerminal for record in service.jobRepository.all())
        assert not (service.workspaceRoot / reply.job_id).exists()
    finally:
        service.close()


@pytest.mark.parametrize("status", ["FAILED", "ABORTED"])
@pytest.mark.parametrize("durable", [False, True])
def testUnknownDispatchRetirementRemainsOwnedWithoutEventReplay(tmp_path, monkeypatch, status, durable):
    from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup, Slot
    from emo_master.apps.runtime.jobs.models import JobRecord
    from tests.runtime.test_terminal_finalization_contract import Channel, Process

    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    supervisor = service.jobSupervisor
    for job in ("source", "consumer"):
        service.jobRepository.create(JobRecord(job, "project", 1, "main", status=status))
    group = RunGroup("source", {"left": Slot("consumer", 1, "consumer")}, service.deviceManager, None)
    group.workerRetired("source")
    service.deviceManager.dispatchGroups.update({job: group for job in group.members})
    process, channel = Process(alive=False), Channel()
    supervisor._handles["consumer"] = (process, Event(), channel)
    append = service.eventStore.append
    retire = service.deviceManager.retire
    eventAttempts, retireCalls = [], []
    failure = RuntimeError("uncertain final retirement event")

    def failFinal(job, event, *args, **kwargs):
        if event == "dispatch.group.retired":
            eventAttempts.append((job, event))
            if durable:
                append(job, event, *args, **kwargs)
            raise failure
        return append(job, event, *args, **kwargs)

    def trackedRetire(job):
        retireCalls.append(job)
        return retire(job)

    monkeypatch.setattr(service.eventStore, "append", failFinal)
    monkeypatch.setattr(service.deviceManager, "retire", trackedRetire)
    try:
        with supervisor._lock, pytest.raises(RuntimeError) as caught:
            supervisor._reap("consumer")
        assert caught.value is failure
        receipt = supervisor._retirements["consumer"]
        assert "uncertain final retirement event" in str(receipt.faults)
        assert group.retired == group.members
        assert service.deviceManager.dispatchGroups and supervisor._ownedJobs() == {"consumer"}
        originalRetireCalls = list(retireCalls)
        for _ in range(2):
            assert supervisor.finishRetirements() == ["consumer: E_JOB_RETIREMENT_INCOMPLETE"]
            assert supervisor._ownedJobs() == {"consumer"}
            assert service.deviceManager.dispatchGroups
        with pytest.raises(RuntimeError):
            service.close()
        assert not service._closed and service._runtimeDataLock._acquired
        assert process.closes == channel.closes == 1
        assert retireCalls == originalRetireCalls
        assert eventAttempts == [("source", "dispatch.group.retired")]
        events = [event for event in service.eventStore.read("source") if event.eventType == "dispatch.group.retired"]
        assert len(events) == int(durable)
        assert "uncertain final retirement event" in receipt.faults["dispatch"]
    finally:
        # Test-only resolution of injected uncertainty. Every fake process and
        # queue already closed; no production event or side effect is retried.
        supervisor._retirements.clear()
        service.deviceManager.dispatchGroups.clear()
        service.close()


def testWorkspaceRepairMakesFirstDispatchAttemptBeforeGroupRelease(tmp_path, monkeypatch):
    from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup, Slot
    from emo_master.apps.runtime.jobs.models import JobRecord
    from tests.runtime.test_terminal_finalization_contract import Channel, Process

    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    supervisor = service.jobSupervisor
    for job in ("source", "consumer"):
        service.jobRepository.create(JobRecord(job, "project", 1, "main", status="FAILED"))
    group = RunGroup("source", {"left": Slot("consumer", 1, "consumer")}, service.deviceManager, None)
    group.workerRetired("source")
    service.deviceManager.dispatchGroups.update({job: group for job in group.members})
    process, channel = Process(alive=False), Channel()
    supervisor._handles["consumer"] = (process, Event(), channel)
    original = service._removeOwnedWorkspace
    removals = []

    def partial(job, path):
        removals.append(job)
        original(job, path)
        if len(removals) == 1:
            raise RuntimeError("after-owned-removal")

    monkeypatch.setattr(service, "_removeOwnedWorkspace", partial)
    try:
        with supervisor._lock, pytest.raises(RuntimeError, match="after-owned-removal"):
            supervisor._reap("consumer")
        assert group.retired == {"source"}
        assert not service.eventStore.read("source")
        assert supervisor.finishRetirements() == []
        assert group.retired == group.members
        assert not supervisor._ownedJobs() and not service.deviceManager.dispatchGroups
        assert removals == ["consumer", "consumer", "source"]
        assert process.closes == channel.closes == 1
        assert [event.eventType for event in service.eventStore.read("source")] == ["dispatch.group.retired"]
    finally:
        supervisor._retirements.clear()
        service.deviceManager.dispatchGroups.clear()
        service.close()
