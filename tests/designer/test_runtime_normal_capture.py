"""Synthetic offline regression, not actual-project or device acceptance."""
import time

from examples.runtime_pages_p2 import sampleProject
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.runtime_worker import RuntimeWorker, _runWorker
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService


def testOriginalRunAndLostAcknowledgementShareOneJobThenExplicitlyRunAgain(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    document = sampleProject(root)
    document.workflows["main"].nodes[1].params["imagePath"] = str(root / "input.png")
    (root / "project.json").write_text(document.model_dump_json(), encoding="utf-8")
    runtime = RuntimeService(dbPath=tmp_path / "production.sqlite3", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(runtime, ownedRuntimeService=runtime)
    calls, accepted, failures, uncertain, events = [], [], [], [], []
    originalStart = client.startJob

    def lostAcknowledgement(projectId, **kwargs):
        reply = originalStart(projectId, **kwargs)
        calls.append(reply)
        if len(calls) == 1:
            raise TimeoutError("synthetic lost start response")
        return reply

    client.startJob = lostAcknowledgement
    try:
        # A viewing attempt cannot create the display backend or execution Job.
        assert client.displayAddress() == ""
        assert getattr(runtime, "_presentationOwner", None) is None
        assert client.loadProject(str(root)).ok
        first = RuntimeWorker(client, str(root), "main", capturePresentation=True)
        first.jobAccepted.connect(accepted.append)
        first.failed.connect(failures.append)
        first.uncertain.connect(uncertain.append)
        first.eventReceived.connect(events.append)
        _runWorker(first)
        assert not failures
        assert len(calls) == len(accepted) == len(uncertain) == 1
        assert first._jobId == calls[0].job_id == accepted[0].job_id
        assert first._jobId and all(event.jobId == first._jobId for event in events)
        assert client.getJobStatus(first._jobId).status == "COMPLETED"
        assert client.displayAddress().startswith("127.0.0.1:")
        listed = client.listDisplayJobs(document.project.projectId)
        assert [job.job_id for job in listed] == [first._jobId]
        assert listed[0].mode == "runtime" and listed[0].capture_enabled
        assert set(listed[0].source_ids) == {"count", "image"}
        assert listed[0].start_request_id == first.startRequestId
        assert runtime.sqliteStore.dbPath == tmp_path / "production.sqlite3"
        deadline = time.monotonic() + 5
        while True:
            snapshot = client._callPresentation("Snapshot", pb.DisplayRequest(job_id=first._jobId,
                runtime_instance_id=first.expectedRuntimeInstanceId))
            if snapshot.results:
                break
            assert time.monotonic() < deadline
            time.sleep(.02)
        result = snapshot.results[0]
        assert result.identity.job_id == first._jobId
        assert result.identity.mode == "runtime"
        assert result.status == "COMPLETE"
        assert next(source.value_json for source in result.sources if source.source_id == "count") == "2"
        assert next(source.image.resource_id for source in result.sources if source.source_id == "image")
        # One explicit subsequent run releases the terminal capture owner first.
        second = RuntimeWorker(client, str(root), "main", capturePresentation=True,
            previousCaptureJob=(first._jobId, first.expectedRuntimeInstanceId))
        second.failed.connect(failures.append)
        _runWorker(second)
        assert not failures
        assert len(calls) == 2
        assert second.startRequestId != first.startRequestId
        assert second._jobId != first._jobId
        assert client.getJobStatus(second._jobId).status == "COMPLETED"
        jobs = {job.job_id: job for job in client.listDisplayJobs(document.project.projectId)}
        assert jobs[first._jobId].resources_released
        assert jobs[second._jobId].capture_enabled
    finally:
        client.close()


def testNoPageEmbeddedRunDoesNotProvisionDisplayResources(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / "production.sqlite3", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(runtime, ownedRuntimeService=runtime)
    try:
        generation = client.prepareStart(False)
        assert generation == runtime.runtimeInstanceId
        assert client.displayAddress() == ""
        assert getattr(runtime, "_presentationOwner", None) is None
        assert client._presentationServer is None
    finally:
        client.close()
