"""Job boundaries, idle failure detection and a pair of real spawned workers."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from emo_master.apps.runtime.execution import background_tasks
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.jobs import worker_main
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from tests.runtime.test_worker_heartbeat_finalization import _spec, _stubComputation


def workerIdentity(control):
    return os.getpid(), threading.get_ident()


def waitForStop(control, marker):
    path = Path(marker)
    path.touch()
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            control.raiseIfCancelled()
            time.sleep(.02)
        raise AssertionError("Job did not cancel the active task")
    finally:
        path.unlink()


class WorkerProbe:
    meta = SimpleNamespace(inputPorts={}, outputPorts={})

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, context):
        tasks = context["backgroundTasks"]
        if params.get("stopMarker"):
            tasks.submit(waitForStop, params["stopMarker"], taskName="hold", estimatedBytes=0)
            return dict(status="ok", outputs={})
        task = tasks.submit(workerIdentity, taskName="probe", estimatedBytes=0)
        pid, worker = tasks.wait(task.id)
        return dict(status="ok", outputs={}, diagnostics=dict(
            pid=pid, worker=worker, execution=threading.get_ident(), taskId=task.id))


@pytest.mark.parametrize("stop", [False, True], ids=["sharing-and-terminal-order", "graceful-stop"])
def testSpawnedBackgroundJob(tmp_path, stop):
    from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.core.project.models import WorkflowNode
    from tests.runtime.current_production_fixture import currentProductionProject, node, savedPixels
    from tests.runtime.production_fixture import waitFor
    from tests.runtime.runtime_test_utils import waitForTerminal

    probes = tmp_path / "probes"
    probes.mkdir()
    manifest = dict(operatorId="test.worker_probe", displayName="Worker Probe", version="1.0.0",
                    entry=f"{__name__}:WorkerProbe", runtimeCapabilities=["backgroundTasks"],
                    inputPorts={}, outputPorts={}, paramSchema={"type": "object", "properties": {
                        "stopMarker": {"type": "string", "title": "Stop marker"}}},
                    minCoreVersion="0.1.0", maxCoreVersion="1.x")
    (probes / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    builtins = Path(__file__).resolve().parents[2] / "src/emo_master/plugins"
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs",
                             pluginRootPaths=(str(builtins), str(probes)))
    try:
        path, doc = currentProductionProject(tmp_path / "project")
        node(doc, "save").params.update(saveMode="background", namingMode="unique")
        for name in ("probe1", "probe2"):
            doc.workflows["main"].nodes.insert(-1, WorkflowNode(nodeId=name, operatorId=manifest["operatorId"]))
        doc.dependencies.operators.append(dict(operatorId=manifest["operatorId"], version="1.0.0"))
        marker = tmp_path / "active.tmp"
        if stop:
            node(doc, "probe2").params["stopMarker"] = str(marker)
        saveProject(path, doc.model_dump(mode="json"))
        assert loadProjectDocument(path) == doc
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        processes = set()
        for _ in range(1 if stop else 2):
            started = runtime.StartJob(pb.StartJobRequest(project_id=doc.project.projectId, workflow_id="main"), None)
            assert started.ok, started.message
            if stop:
                waitFor(marker.is_file)
                assert runtime.jobSupervisor.gracefulStopTimeoutMs == 5000
                stopped = runtime.StopJob(pb.StopJobRequest(job_id=started.job_id, mode="graceful"), None)
                assert stopped.ok, stopped.message
                assert waitForTerminal(runtime, started.job_id).status == "ABORTED"
                assert not marker.exists()
                return
            status = waitForTerminal(runtime, started.job_id, timeoutSeconds=30)
            assert status.status == "COMPLETED", status.message
            events = runtime.eventStore.readMerged(started.job_id)
            ids = [json.loads(e.payloadJson)["diagnostics"] for e in events
                   if e.eventType == "node.completed" and e.nodeId in {"probe1", "probe2"}]
            assert len(ids) == 2 and ids[0]["taskId"] != ids[1]["taskId"]
            assert ids[0]["worker"] == ids[1]["worker"] != ids[0]["execution"]
            assert ids[0]["pid"] == ids[1]["pid"] == status.pid
            processes.add(status.pid)
            artifact, = [e for e in events if e.eventType == "artifact.created" and e.nodeId == "save"]
            terminal, = [e for e in events if e.eventType == "job.completed"]
            assert events.index(artifact) < events.index(terminal)
            ref = json.loads(artifact.payloadJson)["artifact"]
            data = Path(ref["path"]).read_bytes()
            assert ref["sizeBytes"] == len(data) and ref["checksum"] == hashlib.sha256(data).hexdigest()
            assert json.loads(terminal.payloadJson)["previewSnapshots"]["written"] > 0
        assert len(processes) == 2
        files = list((path.parent / "outputs").glob("*.png"))
        assert len(files) == 2
        for file in files:
            np.testing.assert_array_equal(savedPixels(file), savedPixels(path.parent / "input.png"))

    finally:
        runtime.close()


@pytest.mark.parametrize("mode", ["continuous", "dispatch"])
def testIdleWorkerSurfacesBackgroundFailureWithoutAnotherCycle(mode):
    release = threading.Event()
    token = CancellationToken()
    service = background_tasks.BackgroundTaskService(token)
    context = RunContext.root("idle", "main").forNode("origin")
    calls = []
    deadline = time.monotonic() + 3

    def fail(control):
        assert release.wait(3)
        raise OSError("write interrupted")

    def poll():
        assert time.monotonic() < deadline, "background failure was not observed while idle"
        release.set()
        service.raiseUnobservedFailure()

    def run(*args):
        calls.append("cycle")
        assert len(calls) == 1, "second cycle started before checking background failure"

    runner = SimpleNamespace(run=run, pollBackgroundTasks=poll, closeSession=lambda *_: None)
    spec = SimpleNamespace(jobId="idle", projectId="project", workflowId="main",
                           jobWorkspacePath="", cycleIntervalMs=2000)
    service.client(context, "origin").submit(fail, taskName="write", estimatedBytes=1)
    try:
        with pytest.raises(WorkflowExecutionError, match="write interrupted") as caught:
            if mode == "continuous":
                worker_main._runContinuous(runner, spec, {}, token, threading.Event())
            else:
                def idle(action):
                    assert time.monotonic() < deadline, "dispatch did not poll background failures"
                    return {"status": "IDLE"}

                devices = SimpleNamespace(framework=idle)
                worker_main._runConsumer(runner, spec, devices, None, token, threading.Event(), None)
        assert caught.value.diagnostics["backgroundTask"]["nodeRunId"] == context.nodeRunId
        assert len(calls) == (1 if mode == "continuous" else 0)
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize("outcome", ["timeout", "stop", "failure"])
def testJobDrainNeverCompletesOnDeadlineStopOrFailure(monkeypatch, tmp_path, outcome):
    cancel, active, release, retired, published = (threading.Event() for _ in range(5))
    _stubComputation(monkeypatch, lambda: None)
    base = worker_main.WorkflowRunner
    if outcome == "timeout":
        monkeypatch.setattr(background_tasks, "_DRAIN_TIMEOUT_SECONDS", .05)

    def write(control):
        active.set()
        try:
            if outcome == "stop":
                assert cancel.wait(5)
            else:
                assert release.wait(5)
            if outcome == "failure":
                raise OSError("disk write failed")
            control.publish(published.set)
        finally:
            retired.set()

    class Runner(base):
        def __init__(self, **kwargs):
            self.tasks = kwargs["backgroundTasks"]

        def run(self, workflowId, inputs, context, token):
            self.tasks.client(context.forNode("save"), "save").submit(write, taskName="write", estimatedBytes=1)
            assert active.wait(5)
            if outcome == "stop":
                cancel.set()
            elif outcome == "failure":
                release.set()

        def pollBackgroundTasks(self):
            self.tasks.raiseUnobservedFailure()

    monkeypatch.setattr(worker_main, "WorkflowRunner", Runner)
    events = queue.Queue()
    try:
        spec = replace(_spec(tmp_path, None), jobWorkspacePath=str(tmp_path / "job"))
        if outcome == "stop":
            worker_main.runJobProcess(spec, cancel, events)
        else:
            with pytest.raises(WorkflowExecutionError):
                worker_main.runJobProcess(spec, cancel, events)
    finally:
        release.set()
        assert retired.wait(5)
    assert not published.is_set()
    terminal, = [e for e in events.queue if e["eventType"] in {"job.completed", "job.failed", "job.aborted"}]
    assert terminal["eventType"] == ("job.aborted" if outcome == "stop" else "job.failed")
    assert terminal["code"] == {"timeout": "E_BACKGROUND_TASK_DRAIN_TIMEOUT", "stop": "E_CANCELLED",
                                "failure": "E_BACKGROUND_TASK_FAILED"}[outcome]
    if outcome == "failure":
        source = terminal["payload"]["diagnostics"]["backgroundTask"]
        assert source["jobId"] == spec.jobId and source["nodeId"] == "save" and source["nodeRunId"]
