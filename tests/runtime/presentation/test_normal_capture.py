"""R3 integration fixtures; synthetic regressions, not a user-project acceptance."""
import json
import time
from uuid import uuid4

import grpc
import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_global_counter_grpc import _writeCounterProject
from tests.runtime.runtime_test_utils import waitForTerminal


def normalProject(root):
    directory = _writeCounterProject(root, "normal-capture-project")
    raw = json.loads((directory / "project.json").read_text())
    raw["schemaVersion"] = "2.2"
    raw["resources"] = {}
    raw["workflows"]["main"]["nodes"].append({"nodeId": "judge", "operatorId": "vision.compare.number",
        "params": {"operator": "gte", "rightValue": 1.0}})
    raw["workflows"]["main"]["edges"].append({"fromNode": "counter", "fromPort": "count", "toNode": "judge", "toPort": "left"})
    raw["presentation"] = {"defaultPageId": "one", "pageOrder": ["one", "two"],
        "resultScopes": {"root": {"entryWorkflowId": "main", "scopeWorkflowId": "main"}},
        "dataSources": {
            "count": {"kind": "node_output", "resultScopeId": "root", "workflowId": "main", "nodeId": "counter", "port": "count", "expectedType": "integer"},
            "judge": {"kind": "node_output", "resultScopeId": "root", "workflowId": "main", "nodeId": "judge", "port": "result", "expectedType": "boolean"}},
        "pages": {"one": {"name": "Counts", "components": [{"componentId": "count", "type": "number", "bindings": {"value": "count"}}]},
                  "two": {"name": "Decision", "components": [{"componentId": "judge", "type": "indicator", "bindings": {"value": "judge"}}]}}}
    document = ProjectDocument.model_validate(raw)
    (directory / "project.json").write_text(document.model_dump_json())
    return directory, document


def load(runtime, root):
    directory, document = normalProject(root)
    reply = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(directory)), None)
    assert reply.ok, reply.message
    return directory, document


def request(runtime, capture=True):
    return pb.StartJobRequest(project_id="normal-capture-project", inputs_json='{"increment":true}',
        capture_presentation=capture, start_request_id=uuid4().hex,
        expected_runtime_instance_id=runtime.runtimeInstanceId)


def release(service, jobId):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            service.release(jobId)
            return
        except ValueError:
            time.sleep(.02)
    pytest.fail("display ownership never retired")


def testOriginalStartCapturesInstalledOperatorsAndProductionCounter(channel, tmp_path):
    runtime = channel.runtime
    _, document = load(runtime, tmp_path)
    runtime.sqliteStore.setGlobalCounter(document.project.projectId, "jobs", 40)
    first = runtime.StartJob(request(runtime), None)
    assert first.ok, first.message
    assert waitForTerminal(runtime, first.job_id).status == "COMPLETED"
    end = time.monotonic() + 10
    while not channel.store.snapshot(first.job_id)["results"] and time.monotonic() < end:
        time.sleep(.02)
    result = channel.store.snapshot(first.job_id)["results"][0]
    assert result.identity.mode == "runtime"
    assert {source.sourceId: source.valueJson for source in result.sources} == {"count": "41", "judge": "true"}
    assert runtime.sqliteStore.getGlobalCounter(document.project.projectId, "jobs").value == 41
    assert len(runtime.jobRepository.all()) == 1
    assert not channel.prepared
    workspace = runtime.workspaceRoot / first.job_id
    assert (workspace / "project.json").is_file()
    assert not list(channel.root.rglob("runtime.sqlite3"))
    frozen = channel.jobs[first.job_id]["plan"]
    runtime.loadedDocument.presentation.dataSources["count"].port = "uncollected"
    metadata = DisplayRpc(channel).ListJobs(pb.DisplayEmpty(project_id=document.project.projectId), None).jobs
    assert len(metadata) == 1 and metadata[0].source_ids == ["count", "judge"]
    assert metadata[0].capture_definition_json
    assert channel.jobs[first.job_id]["plan"] == frozen
    # Resource release does not remove normal output/workspace data.
    release(channel, first.job_id)
    assert (workspace / "project.json").is_file()
    runtime.loadedDocument.presentation = document.presentation
    second = runtime.StartJob(request(runtime), None)
    assert second.ok and second.job_id != first.job_id
    assert waitForTerminal(runtime, second.job_id).status == "COMPLETED"
    assert runtime.sqliteStore.getGlobalCounter(document.project.projectId, "jobs").value == 42


def testLegacyStartAndProjectScopedReadonlyMetadata(channel, tmp_path):
    directory, document = load(channel.runtime, tmp_path)
    # The old request has none of the new fields, and continues the old path.
    start = channel.runtime.StartJob(pb.StartJobRequest(project_id=str(directory), inputs_json='{"increment":true}'), None)
    assert start.ok
    assert waitForTerminal(channel.runtime, start.job_id).status == "COMPLETED"
    assert not channel.jobs and not channel.prepared
    adapter = DisplayRpc(channel)
    assert not adapter.ListJobs(pb.DisplayEmpty(), None).jobs
    assert not adapter.ListJobs(pb.DisplayEmpty(project_id="another-project"), None).jobs
    entry = adapter.ListJobs(pb.DisplayEmpty(project_id=document.project.projectId), None).jobs[0]
    assert entry.job_id == start.job_id and not entry.capture_enabled
    assert entry.mode == "runtime" and not entry.source_ids
    assert len(channel.runtime.jobRepository.all()) == 1


def testIdempotentStartLookupAndGenerationFence(channel, tmp_path):
    runtime = channel.runtime
    load(runtime, tmp_path)
    startRequest = request(runtime)
    first = runtime.StartJob(startRequest, None)
    second = runtime.StartJob(startRequest, None)
    assert first.ok and second.job_id == first.job_id
    assert len(runtime._startRequests[startRequest.start_request_id][0]) == 64
    lookup = runtime.GetStartRequest(pb.StartRequestLookup(start_request_id=startRequest.start_request_id,
        runtime_instance_id=runtime.runtimeInstanceId), None)
    assert lookup.ok and lookup.job_id == first.job_id
    changed = pb.StartJobRequest()
    changed.CopyFrom(startRequest)
    changed.inputs_json = '{"increment":false}'
    assert runtime.StartJob(changed, None).status == "REJECTED"
    changed.expected_runtime_instance_id = "old-generation"
    assert runtime.StartJob(changed, None).status == "RESET_REQUIRED"
    assert runtime.GetStartRequest(pb.StartRequestLookup(start_request_id="missing",
        runtime_instance_id=runtime.runtimeInstanceId), None).status == "UNKNOWN"
    assert len(runtime.jobRepository.all()) == 1
    assert waitForTerminal(runtime, first.job_id).status == "COMPLETED"
    assert runtime.sqliteStore.getGlobalCounter("normal-capture-project", "jobs").value == 1


def testKnownRejectionAndCaptureQuotasDoNotExecute(channel, tmp_path):
    runtime = channel.runtime
    load(runtime, tmp_path)
    bad = request(runtime)
    bad.workflow_id = "missing"
    assert runtime.StartJob(bad, None).status == "REJECTED"
    assert runtime.GetStartRequest(pb.StartRequestLookup(start_request_id=bad.start_request_id,
        runtime_instance_id=runtime.runtimeInstanceId), None).status == "REJECTED"
    starts = [runtime.StartJob(request(runtime), None) for _ in range(2)]
    assert all(start.ok for start in starts)
    assert not runtime.StartJob(request(runtime), None).ok
    assert len(runtime.jobRepository.all()) == 2
    assert all(waitForTerminal(runtime, start.job_id).status == "COMPLETED" for start in starts)


def testCaptureFreezeRejectsUnsupportedScopeWithoutPreparing(channel, tmp_path):
    runtime = channel.runtime
    directory, document = load(runtime, tmp_path)
    document.presentation.resultScopes["other"] = document.presentation.resultScopes["root"].model_copy()
    document.presentation.pages["two"].resultScopeIds = ["other"]
    with pytest.raises(ValueError, match="one result scope"):
        freezeNormalCapture(document, runtime.pluginScanResult.activeOperators, directory, "main")
    assert not runtime.jobRepository.all() and not channel.prepared


def testIncrementalNetworkStartLookupAndMetadata(channel, tmp_path):
    runtime = channel.runtime
    _, document = load(runtime, tmp_path)
    server = AioRuntimeServer(runtime, channel)
    connection = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        execution, display = rpc.RuntimeServiceStub(connection), rpc.DisplayServiceStub(connection)
        capabilities = display.Capabilities(pb.DisplayEmpty(), timeout=2)
        assert "normal_start_capture" in capabilities.capabilities
        assert not display.ListJobs(pb.DisplayEmpty(project_id=document.project.projectId), timeout=2).jobs
        startRequest = request(runtime)
        first = execution.StartJob(startRequest, timeout=2)
        lookup = execution.GetStartRequest(pb.StartRequestLookup(start_request_id=startRequest.start_request_id,
            runtime_instance_id=capabilities.runtime_instance_id), timeout=2)
        assert first.ok and lookup.job_id == first.job_id
        entry = display.ListJobs(pb.DisplayEmpty(project_id=document.project.projectId), timeout=2).jobs[0]
        assert entry.project_id == document.project.projectId and entry.mode == "runtime"
        assert entry.capture_enabled and entry.start_request_id == startRequest.start_request_id
        assert waitForTerminal(runtime, first.job_id).status == "COMPLETED"
    finally:
        connection.close()
        server.close()


def testImageCountDecisionShareOneNormalResultAndPreserveOutput(channel, tmp_path, sample):
    from emo_master.core.project.models import WorkflowNode, WorkflowEdge
    from emo_master.core.presentation.models import DataSource, Component, Page
    project = sample(tmp_path)
    project.workflows["main"].nodes[1].params["imagePath"] = str(tmp_path / "input.png")
    output = tmp_path / "ordinary-output.png"
    project.workflows["main"].nodes.extend([
        WorkflowNode(nodeId="judge", operatorId="vision.compare.number", params={"rightValue": 2.0}),
        WorkflowNode(nodeId="save", operatorId="vision.io.image_saver", params={"outputPath": str(output)})])
    project.workflows["main"].edges.extend([
        WorkflowEdge(fromNode="count", fromPort="count", toNode="judge", toPort="left"),
        WorkflowEdge(fromNode="load", fromPort="image", toNode="save", toPort="image")])
    project.presentation.dataSources["judge"] = DataSource(kind="node_output", resultScopeId="root",
        workflowId="main", nodeId="judge", port="result", expectedType="boolean")
    project.presentation.dataSources["image-alias"] = project.presentation.dataSources["image"].model_copy()
    project.presentation.pages["second"] = Page(name="Decision", components=[
        Component(componentId="decision", type="indicator", bindings={"value": "judge"}),
        Component(componentId="image-again", type="image", bindings={"image": "image-alias"}, layout={"row": 1})])
    project.presentation.pageOrder.append("second")
    (tmp_path / "project.json").write_text(project.model_dump_json())
    runtime = channel.runtime
    assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(tmp_path)), None).ok
    start = runtime.StartJob(pb.StartJobRequest(project_id=project.project.projectId,
        capture_presentation=True, start_request_id=uuid4().hex,
        expected_runtime_instance_id=runtime.runtimeInstanceId), None)
    assert start.ok, start.message
    assert waitForTerminal(runtime, start.job_id).status == "COMPLETED"
    deadline = time.monotonic() + 10
    while not channel.store.snapshot(start.job_id)["results"] and time.monotonic() < deadline:
        time.sleep(.02)
    result = channel.store.snapshot(start.job_id)["results"][0]
    assert result.status == "COMPLETE"
    values = {source.sourceId: source for source in result.sources}
    assert values["count"].valueJson == "2" and values["judge"].valueJson == "true"
    assert values["image"].image.ownerResultKey == result.identity.resultKey
    assert len(result.expectedSourceIds) == 4
    assert values["image-alias"].image.resourceId == values["image"].image.resourceId
    assert channel.exporter.stats["completed"] == 1  # even alias IDs reuse one encoding
    assert output.is_file()
    assert len(runtime.jobRepository.all()) == 1
    release(channel, start.job_id)
    assert output.is_file()


def testFailedStartIsConclusiveAndNeverReplayed(channel, tmp_path, monkeypatch):
    runtime = channel.runtime
    load(runtime, tmp_path)
    def fail(*args):
        raise RuntimeError("injected process-start failure")
    monkeypatch.setattr(runtime.jobManager, "start", fail)
    startRequest = request(runtime)
    first = runtime.StartJob(startRequest, None)
    assert not first.ok and first.job_id and first.status == "FAILED"
    again = runtime.StartJob(startRequest, None)
    assert not again.ok and again.job_id == first.job_id
    assert len(runtime.jobRepository.all()) == 1
    release(channel, first.job_id)


def testUnknownStartLedgerEntriesAreNeverEvicted(channel, tmp_path):
    runtime = channel.runtime
    load(runtime, tmp_path)
    runtime._startRequests = {str(index): ("fingerprint", pb.StartJobReply(status="UNKNOWN")) for index in range(1024)}
    reply = runtime.StartJob(request(runtime), None)
    assert reply.status == "REJECTED" and "quota" in reply.message
    assert not runtime.jobRepository.all()


@pytest.mark.parametrize("mode", ["graceful", "force"])
def testExplicitStopReleaseAndRestartNormalJob(tmp_path, mode):
    from examples.runtime_pages_p2 import pacedProject, pluginRoots
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.presentation.service import PresentationService
    project = pacedProject(tmp_path, count=30)
    project.workflows["detect"].nodes[1].params["imagePath"] = str(tmp_path / "input.png")
    (tmp_path / "project.json").write_text(project.model_dump_json())
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3",
        workspaceRoot=tmp_path / "jobs", pluginRootPaths=pluginRoots(tmp_path))
    display = PresentationService(runtime, tmp_path / "display")
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(tmp_path)), None).ok
        started = []
        for _ in range(2):
            reply = runtime.StartJob(pb.StartJobRequest(project_id=project.project.projectId,
                capture_presentation=True, start_request_id=uuid4().hex,
                expected_runtime_instance_id=runtime.runtimeInstanceId), None)
            assert reply.ok, reply.message
            assert reply.job_id not in started
            started.append(reply.job_id)
            end = time.monotonic() + 10
            while not display.store.snapshot(reply.job_id)["results"] and time.monotonic() < end:
                time.sleep(.02)
            assert display.store.snapshot(reply.job_id)["results"]
            assert runtime.StopJob(pb.StopJobRequest(job_id=reply.job_id, mode=mode), None).ok
            assert waitForTerminal(runtime, reply.job_id).status == "ABORTED"
            release(display, reply.job_id)
            assert runtime.jobSupervisor.getProcess(reply.job_id) is None
            assert not display.jobs and not display.readers
        assert len(runtime.jobRepository.all()) == 2
        assert not display.prepared
    finally:
        runtime.close()
        display.close()


def testLostStartReplyReconcilesWithoutAnotherExecution(channel, tmp_path, monkeypatch):
    from threading import Event
    runtime = channel.runtime
    load(runtime, tmp_path)
    actualStart = runtime.StartJob
    dispatched = Event()
    def delayedReply(request, context):
        reply = actualStart(request, context)
        dispatched.set()
        time.sleep(.25)
        return reply
    monkeypatch.setattr(runtime, "StartJob", delayedReply)
    server = AioRuntimeServer(runtime, channel)
    connection = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        stub = rpc.RuntimeServiceStub(connection)
        startRequest = request(runtime)
        with pytest.raises(grpc.RpcError) as timeout:
            stub.StartJob(startRequest, timeout=.1)
        assert timeout.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
        assert dispatched.wait(3)
        known = stub.GetStartRequest(pb.StartRequestLookup(start_request_id=startRequest.start_request_id,
            runtime_instance_id=runtime.runtimeInstanceId), timeout=2)
        assert known.ok and known.job_id
        assert waitForTerminal(runtime, known.job_id).status == "COMPLETED"
        assert len(runtime.jobRepository.all()) == 1
        assert runtime.sqliteStore.getGlobalCounter("normal-capture-project", "jobs").value == 1
    finally:
        connection.close()
        server.close()


def testDiscardPreparedRetiresOnlyItsDebugState(channel, tmp_path, sample):
    from pathlib import Path
    project = sample(tmp_path, image=False)
    debug = channel.prepare(project, tmp_path)
    releaseRecord = channel.prepare(project, tmp_path, mode="release", releaseRevision="test-only")
    debugState = Path(debug.snapshot.runtimeDbPath).parent
    releaseState = Path(releaseRecord.snapshot.runtimeDbPath).parent
    assert debugState.is_dir() and releaseState.is_dir()
    channel.discardPrepared(debug.snapshot.snapshotId)
    assert not debug.projectPath.parent.exists() and not debugState.exists()
    assert releaseState.is_dir()
    channel.discardPrepared(releaseRecord.snapshot.snapshotId)
    assert releaseState.is_dir()
    assert not channel.prepared


def testDiscardPreparedCleanupCanRetryAfterPartialFailure(channel, tmp_path, sample, monkeypatch):
    from pathlib import Path
    import shutil
    record = channel.prepare(sample(tmp_path, image=False), tmp_path)
    stateRoot = Path(record.snapshot.runtimeDbPath).parent
    original = shutil.rmtree
    def failState(path, *args, **kwargs):
        if Path(path) == stateRoot:
            raise OSError("injected state cleanup failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(shutil, "rmtree", failState)
    with pytest.raises(OSError, match="injected"):
        channel.discardPrepared(record.snapshot.snapshotId)
    assert record.snapshot.snapshotId in channel.prepared
    assert not record.projectPath.parent.exists() and stateRoot.exists()
    monkeypatch.setattr(shutil, "rmtree", original)
    channel.discardPrepared(record.snapshot.snapshotId)
    assert not stateRoot.exists() and not channel.prepared
