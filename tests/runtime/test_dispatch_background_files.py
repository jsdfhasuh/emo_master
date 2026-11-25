"""Background writes stay with the Dispatch Job and delivery that submitted them."""
from copy import deepcopy
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import json

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.jobs.worker_main import _runConsumer
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master import __version__
from tests.runtime.current_production_fixture import currentProductionProject
from tests.runtime.inspection_fixtures import loopProject
from tests.runtime.inspection_test_operator import InspectionTestOperator
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor


def testDispatchAndJoinTransferCompletedFilesAcrossTwoJobs(tmp_path):
    path, document = currentProductionProject(tmp_path / "project")
    payload = document.model_dump(mode="json")
    payload["runtime"]["maxConcurrentJobs"] = 2
    source = payload["workflows"]["main"]
    saver = next(node for node in source["nodes"] if node["nodeId"] == "save")
    saver["params"].update(saveMode="background", namingMode="unique")
    ports = {"image": "image", "sourceFile": "json"}
    source["nodes"].extend([
        dict(nodeId="send", kind="dispatch", inputPorts=ports, params=dict(targets=[
            dict(targetSlotId="inspection", workflowId="consumer", inputs={port: port for port in ports})])),
        dict(nodeId="join", kind="join", params=dict(dispatchNodeId="send")),
    ])
    source["edges"].extend([
        dict(fromNode="load", fromPort="image", toNode="send", toPort="image"),
        dict(fromNode="save", fromPort="result", toNode="send", toPort="sourceFile"),
        dict(fromNode="send", fromPort="token", toNode="join", toPort="token"),
    ])
    payload["workflowOrder"].append("consumer")
    payload["workflows"]["consumer"] = dict(name="Consumer", inputs=ports,
        outputs={"received": "json", "created": "json"}, nodes=[
            dict(nodeId="input", kind="workflow_input"),
            dict(nodeId="save", operatorId=saver["operatorId"], params=dict(saver["params"], outputPath="outputs/child.png")),
            dict(nodeId="output", kind="workflow_output"),
        ], edges=[
            dict(fromNode="input", fromPort="image", toNode="save", toPort="image"),
            dict(fromNode="input", fromPort="sourceFile", toNode="output", toPort="received"),
            dict(fromNode="save", fromPort="result", toNode="output", toPort="created"),
        ])
    document = ProjectDocument.model_validate(payload)
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = runtime.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        assert waitForTerminal(runtime, started.job_id, timeoutSeconds=30).status == "COMPLETED"
        waitFor(lambda: not runtime.deviceManager.dispatchGroups)
        jobs = runtime.jobRepository.all()
        assert len(jobs) == len({job.pid for job in jobs}) == 2
        assert all(job.status == "COMPLETED" for job in jobs)
        sourceEvents = runtime.eventStore.readMerged(started.job_id)
        join, = [event for event in sourceEvents if event.eventType == "node.completed" and event.nodeId == "join"]
        returned = json.loads(join.payloadJson)["outputs"]["inspection"]
        for key, prefix in (("received", "result_"), ("created", "child_")):
            receipt = returned[key]
            assert set(receipt) == {"saved", "path"} and receipt["saved"] is True
            saved = Path(receipt["path"])
            assert saved.name.startswith(prefix)
            assert saved.read_bytes() == (path.parent / "input.png").read_bytes()
        for job in jobs:
            events = runtime.eventStore.readMerged(job.jobId)
            artifact, = [e for e in events if e.eventType == "artifact.created" and e.nodeId == "save"]
            handoff = "dispatch.accepted" if job.jobId == started.job_id else "dispatch.invocation.completed"
            assert artifact.sequence < next(e.sequence for e in events if e.eventType == handoff)
    finally:
        runtime.close()


@pytest.mark.parametrize("observed", [False, True])
def testConsumerSettlesSideEffectsBeforeConfirmingTheirDelivery(observed):
    token, release = CancellationToken(), Event()
    requests, receipts = [], []
    taskIds = []

    class Service(BackgroundTaskService):
        def waitForIdle(self):
            release.set()
            super().waitForIdle()

    class Runner(WorkflowRunner):
        def run(self, workflowId, inputs, context, cancellation):
            self.pollBackgroundTasks()
            if context.deliveryId == "A":
                client = service.client(context.forNode("writer"), "writer")
                def write(control):
                    assert release.wait(5)
                    raise OSError("delivery A write failed")
                taskIds.append(client.submit(write, taskName="write", estimatedBytes=0).id)
                if observed:
                    # An operator (including SQLite with continue policy) handled this error.
                    release.set()
                    with pytest.raises(WorkflowExecutionError, match="delivery A write failed"):
                        client.wait(taskIds[0])
            return self.result({})

    def dispatch(action, **fields):
        if action == "dispatchComplete":
            receipts.append((fields["deliveryId"], fields["status"]))
            return
        assert action == "dispatchNext"
        if requests == ["A"]:
            release.set()
            waitFor(lambda: service.status(taskIds[0]) == "FAILED")
        if len(requests) == 2:
            return {"status": "CLOSED"}
        delivery = "B" if requests else "A"
        requests.append(delivery)
        return dict(status="AVAILABLE", runGroupId="group", dispatchId="dispatch", deliveryId=delivery,
                    invocationId=delivery, targetSlotId="slot", cycleId=delivery, planRevision=1,
                    cycle={}, inputs={}, frames=[])

    service = Service(token)
    runner = Runner(SimpleNamespace(), {}, backgroundTasks=service)
    spec = SimpleNamespace(jobId="job", workflowId="consumer", jobWorkspacePath="", projectId="project")
    devices = SimpleNamespace(framework=dispatch, encode=lambda value, context: value, frameCopies={})
    args = (runner, spec, devices, SimpleNamespace(install=lambda cycle: None), token, Event(), lambda *a, **kw: None)
    try:
        if observed:
            _runConsumer(*args)
            assert receipts == [("A", "COMPLETED"), ("B", "COMPLETED")]
        else:
            with pytest.raises(WorkflowExecutionError, match="delivery A write failed"):
                _runConsumer(*args)
            assert requests == ["A"] and receipts == [("A", "FAILED")]
    finally:
        release.set()
        service.close()


def testTransferResolvesNestedReceiptsAndLeavesLocalInputsIntact(tmp_path):
    token = CancellationToken()
    service = BackgroundTaskService(token)
    runner = WorkflowRunner(SimpleNamespace(), {}, backgroundTasks=service)
    path = tmp_path / "nested.txt"
    try:
        client = service.client(RunContext.root("job", "main").forNode("write"), "write")
        def write(control):
            path.write_text("ready")
            return {"saved": True, "path": str(path)}
        task = client.submit(write, taskName="write", estimatedBytes=0)
        client.wait(task.id)
        submitted = dict(accepted=True, state="SUBMITTED", taskId=task.id, saved=False, path=str(path))
        value = {"batch": [None, {"meta": "preserved", "receipt": submitted}], "other": {"state": "SUBMITTED"}}
        expected = {"saved": True, "path": str(path)}
        assert runner.prepareDispatchValue(value) == {
            "batch": [None, {"meta": "preserved", "receipt": expected}], "other": {"state": "SUBMITTED"}}
        assert submitted["state"] == "SUBMITTED" and submitted["saved"] is False
        other = tmp_path / "other.txt"
        other.write_text("another file")
        with pytest.raises(WorkflowExecutionError, match="did not publish the submitted file"):
            runner.prepareDispatchValue(dict(submitted, path=str(other)))
    finally:
        service.close()


def testCollectedForeachReceiptsSurviveTaskRecordExpiry(tmp_path):
    payload = loopProject(tmp_path).model_dump(mode="json")
    main, body = (payload["workflows"][key] for key in ("main", "body"))
    main["nodes"] = [node for node in main["nodes"] if node["nodeId"] != "gate"]
    main["outputs"] = {"files": "list<json>"}
    next(node for node in main["nodes"] if node["nodeId"] == "loop")["outputPorts"] = {"file": "list<json>"}
    main["edges"][-1] = dict(fromNode="loop", fromPort="file", toNode="output", toPort="files")
    body["outputs"] = {"file": "json"}
    body["edges"][-1] = dict(fromNode="save", fromPort="result", toNode="output", toPort="file")
    next(node for node in body["nodes"] if node["nodeId"] == "save")["params"].update(
        saveMode="background", namingMode="unique")
    registry = dict(PluginRegistry(__version__).scanRoots([Path("src/emo_master/plugins/builtins/image_saver")]).activeOperators)
    registry[InspectionTestOperator.meta.operatorId] = InspectionTestOperator
    compiled = WorkflowCompiler(registry).compile(ProjectDocument.model_validate(payload))
    token = CancellationToken()
    service = BackgroundTaskService(token, maxRecords=1)
    captured, artifacts = [], []
    class CopyingRunner(WorkflowRunner):
        def _route(self, nodeId, outputs, edges, nodeInputs):
            if nodeId == "save":
                # A live dataflow value may be copied locally before its file is registered.
                captured.append(deepcopy(outputs["result"]))
            super()._route(nodeId, outputs, edges, nodeInputs)

    def publish(eventType, context, payload, **kwargs):
        if eventType == "node.completed" and context.callerNodeId == "save":
            service.wait(payload["outputs"]["result"]["taskId"])
        if eventType == "artifact.created":
            artifacts.append(payload["artifact"]["path"])
    runner = CopyingRunner(compiled, registry, backgroundTasks=service, eventPublisher=publish)
    try:
        collected = runner.run("main", {"items": [1, 2]}, RunContext.root("job", "main"), token).outputs["files"]
        assert [receipt["state"] for receipt in collected] == ["SUBMITTED", "SUBMITTED"]
        expected = [{"saved": True, "path": path} for path in artifacts]
        assert len(expected) == 2 and all(Path(item["path"]).is_file() for item in expected)
        assert runner.prepareDispatchValue(collected) == runner.prepareDispatchValue(captured) == expected
        with pytest.raises(WorkflowExecutionError) as expired:
            service.wait(collected[0]["taskId"])
        assert expired.value.code == "E_BACKGROUND_TASK_EXPIRED"
        with pytest.raises(WorkflowExecutionError) as unverified:
            runner.prepareDispatchValue(json.loads(json.dumps(collected[0])))
        assert unverified.value.code == "E_BACKGROUND_TASK_EXPIRED"
    finally:
        service.close()
