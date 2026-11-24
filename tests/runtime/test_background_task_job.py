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
from unittest.mock import patch

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


def holdImageAtPublication(control, target):
    from emo_master.plugins.builtins.image_saver.operator import _saveInBackground
    from tests.runtime.production_fixture import waitFor

    publish = control.publish

    def pause(finalize):
        target.with_suffix(".ready").touch()
        waitFor(target.with_suffix(".release").is_file, seconds=30)
        return publish(finalize)

    with patch.object(control, "publish", pause):
        return _saveInBackground(control, np.zeros((8, 8), np.uint8), target)


def holdTask(control, marker, releaseMarker=None):
    path = Path(marker)
    path.touch()
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            control.raiseIfCancelled()
            if releaseMarker and Path(releaseMarker).is_file():
                return
            time.sleep(.02)
        raise AssertionError("Job did not cancel or release the active task")
    finally:
        path.unlink()


class WorkerProbe:
    meta = SimpleNamespace(inputPorts={"afterSave": {"type": "json", "required": False}}, outputPorts={})

    def validateParams(self, params):
        return None

    def executeNode(self, inputs, params, context):
        tasks = context["backgroundTasks"]
        if "stagedImage" in params:
            from tests.runtime.production_fixture import waitFor
            target = Path(params["stagedImage"])
            tasks.submit(holdImageAtPublication, target, taskName="stalled save", estimatedBytes=64)
            if params.get("failWhenStaged"):
                waitFor(target.with_suffix(".fail").is_file)
                return dict(status="error", error=dict(code="E_DOWNSTREAM", message="downstream failed"))
            return dict(status="ok", outputs={})
        if params.get("stopMarker"):
            if params.get("artifactMarker"):
                from emo_master.apps.runtime.artifacts.store import ArtifactStore
                from tests.runtime.production_fixture import waitFor
                marker = Path(params["artifactMarker"])
                register = ArtifactStore.registerPath

                def pausedRegister(store, *args, **kwargs):
                    ArtifactStore.registerPath = register
                    marker.touch()
                    waitFor(marker.with_suffix(".release").is_file, seconds=15)
                    return register(store, *args, **kwargs)

                ArtifactStore.registerPath = pausedRegister
            tasks.submit(holdTask, params["stopMarker"], params.get("releaseMarker"),
                         taskName="hold", estimatedBytes=0)
            return dict(status="ok", outputs={})
        task = tasks.submit(workerIdentity, taskName="probe", estimatedBytes=0)
        pid, worker = tasks.wait(task.id)
        return dict(status="ok", outputs={}, diagnostics=dict(
            pid=pid, worker=worker, execution=threading.get_ident(), taskId=task.id))


def probeRuntime(tmp_path):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService

    probes = tmp_path / "probes"
    probes.mkdir()
    manifest = dict(operatorId="test.worker_probe", displayName="Worker Probe", version="1.0.0",
                    entry=f"{__name__}:WorkerProbe", runtimeCapabilities=["backgroundTasks"],
                    inputPorts=WorkerProbe.meta.inputPorts, outputPorts={}, paramSchema={"type": "object", "properties": {
                        "stopMarker": {"type": "string", "title": "Stop marker"},
                        "releaseMarker": {"type": "string", "title": "Release marker"},
                        "artifactMarker": {"type": "string", "title": "Pause artifact registration"},
                        "stagedImage": {"type": "string", "title": "Paused save target"},
                        "failWhenStaged": {"type": "boolean", "title": "Fail after staging"}}},
                    minCoreVersion="0.1.0", maxCoreVersion="1.x")
    (probes / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    builtins = Path(__file__).resolve().parents[2] / "src/emo_master/plugins"
    return RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs",
                          pluginRootPaths=(str(builtins), str(probes)))


@pytest.mark.parametrize("stop", [False, True], ids=["sharing-and-terminal-order", "graceful-stop"])
def testSpawnedBackgroundJob(tmp_path, stop):
    from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.core.project.models import WorkflowNode
    from tests.runtime.current_production_fixture import currentProductionProject, node, savedPixels
    from tests.runtime.production_fixture import waitFor
    from tests.runtime.runtime_test_utils import waitForTerminal

    runtime = probeRuntime(tmp_path)
    try:
        path, doc = currentProductionProject(tmp_path / "project")
        node(doc, "save").params.update(saveMode="background", namingMode="unique")
        for name in ("probe1", "probe2"):
            doc.workflows["main"].nodes.insert(-1, WorkflowNode(nodeId=name, operatorId="test.worker_probe"))
        doc.dependencies.operators.append(dict(operatorId="test.worker_probe", version="1.0.0"))
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


@pytest.mark.parametrize("force", [True, False], ids=["forced-stop", "failure-cleanup-timeout"])
def testJobRetirementReclaimsOnlyItsInterruptedImage(tmp_path, force):
    from emo_master.apps.designer.state.project_store import saveProject
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.core.project.models import WorkflowEdge, WorkflowNode
    from tests.runtime.current_production_fixture import currentProductionProject, node, savedPixels
    from tests.runtime.production_fixture import waitFor
    from tests.runtime.runtime_test_utils import waitForTerminal

    runtime = probeRuntime(tmp_path)
    output = tmp_path / "images"
    output.mkdir()
    target = output / "interrupted.png"
    neighbor = output / "someone-else.tmp"
    neighbor.write_bytes(b"other writer")
    confirmed = output / "confirmed.png"
    try:
        project, doc = currentProductionProject(tmp_path / "project")
        node(doc, "save").params["outputPath"] = str(confirmed)
        doc.workflows["main"].nodes.insert(-1, WorkflowNode(nodeId="interrupt", operatorId="test.worker_probe",
            params={"stagedImage": str(target), "failWhenStaged": not force}))
        doc.workflows["main"].edges.append(WorkflowEdge(fromNode="save", fromPort="result",
                                                         toNode="interrupt", toPort="afterSave"))
        doc.dependencies.operators.append(dict(operatorId="test.worker_probe", version="1.0.0"))
        saveProject(project, doc.model_dump(mode="json"))
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(project)), None).ok
        started = runtime.StartJob(pb.StartJobRequest(project_id=doc.project.projectId, workflow_id="main"), None)
        assert started.ok, started.message
        waitFor(target.with_suffix(".ready").is_file)
        staged, = output.glob(".emo-image-stage-*/.emo-image-*.tmp")
        assert staged.stat().st_size and not target.exists()
        if force:
            stopped = runtime.StopJob(pb.StopJobRequest(job_id=started.job_id, mode="force"), None)
            assert stopped.ok, stopped.message
        else:
            target.with_suffix(".fail").touch()
        terminal = waitForTerminal(runtime, started.job_id)
        assert terminal.status == ("ABORTED" if force else "FAILED"), terminal.message
        runtime.jobSupervisor.waitForRetirement(10, jobId=started.job_id)
        if not force:
            failed, = [e for e in runtime.eventStore.readMerged(started.job_id) if e.eventType == "job.failed"]
            assert failed.code == "E_DOWNSTREAM"
            assert json.loads(failed.payloadJson)["backgroundTaskCleanup"]["code"] == "E_BACKGROUND_TASK_CLEANUP_TIMEOUT"
        assert not staged.exists() and not target.exists()
        np.testing.assert_array_equal(savedPixels(confirmed), savedPixels(project.parent / "input.png"))
        assert neighbor.read_bytes() == b"other writer"
    finally:
        target.with_suffix(".release").touch(exist_ok=True)
        runtime.close()


@pytest.mark.parametrize("boundary", ["interval", "parked-cycle", "cancel-while-parked"])
def testConfigurationDrainFinishesAcceptedImageBeforeStoppingJob(tmp_path, monkeypatch, boundary):
    from emo_master.apps.designer.state.project_store import saveProject
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.core.project.models import WorkflowNode
    from tests.runtime.current_production_fixture import currentProductionProject, node, savedPixels
    from tests.runtime.production_fixture import waitFor
    from tests.runtime.test_project_configuration import finish

    runtime = probeRuntime(tmp_path)
    release = tmp_path / "release"
    registering = tmp_path / "registering"
    registered = registering.with_suffix(".release")
    try:
        path, doc = currentProductionProject(tmp_path / "project", mode="continuous")
        doc.production.cycleIntervalMs = 10000 if boundary == "interval" else 800
        node(doc, "save").params.update(saveMode="background", namingMode="unique")
        doc.workflows["main"].nodes.insert(1, WorkflowNode(nodeId="hold", operatorId="test.worker_probe",
            params={"stopMarker": str(tmp_path / "holding"), "releaseMarker": str(release),
                    "artifactMarker": "" if boundary == "cancel-while-parked" else str(registering)}))
        doc.dependencies.operators.append(dict(operatorId="test.worker_probe", version="1.0.0"))
        saveProject(path, doc.model_dump(mode="json"))
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        started = runtime.StartJob(pb.StartJobRequest(workflow_id="main", follow_project_run_mode=True), None)
        assert started.ok, started.message
        jobId = started.job_id
        def events():
            return runtime.eventStore.readMerged(jobId)

        waitFor(lambda: any(e.eventType == "workflow.completed" for e in events()))
        save, = [e for e in events() if e.eventType == "node.completed" and e.nodeId == "save"]
        target = Path(json.loads(save.payloadJson)["outputs"]["result"]["path"])
        assert not target.exists()

        draining = threading.Event()
        registrationChecked = threading.Event()
        reportedIdle = threading.Event()
        pending = runtime.jobSupervisor.hasPendingBackgroundTasks
        def observe(owner):
            busy = pending(owner)
            if owner == jobId and busy:
                draining.set()
                if registering.exists():
                    registrationChecked.set()
            elif owner == jobId and release.exists() and not registered.exists():
                reportedIdle.set()
            return busy
        monkeypatch.setattr(runtime.jobSupervisor, "hasPendingBackgroundTasks", observe)
        config = runtime.projectConfiguration
        snapshot = config.snapshot()
        candidate = snapshot["payload"]
        candidate["workflows"]["main"]["nodes"].insert(-1, dict(nodeId="added", operatorId="vision.value.number",
                                                              params=dict(value=1)))
        config.start(dict(requestId="drain", expectedRuntimeInstanceId=runtime.runtimeInstanceId,
            expectedRevision=snapshot["documentRevision"], localPayload=candidate,
            selectedPaths=["/workflows/main/nodes/added"], replacementPolicy="drainAffected"))
        assert draining.wait(5), config.status("drain")
        if boundary != "interval":
            waitFor(lambda: jobId in config.parkedOwners)
        assert not target.exists()
        if boundary == "cancel-while-parked":
            finishedCycles = sum(e.eventType == "workflow.completed" for e in events())
            config.cancel("drain")
            assert finish(config, "drain")["state"] == "CANCELLED"
            waitFor(lambda: sum(e.eventType == "workflow.completed" for e in events()) > finishedCycles)
            assert not target.exists() and runtime.jobSupervisor.hasPendingBackgroundTasks(jobId)
            return
        release.touch()
        waitFor(lambda: registrationChecked.is_set() or reportedIdle.is_set())
        assert not reportedIdle.is_set(), "configuration declared the Job idle before artifact registration"
        assert target.is_file() and registering.is_file()
        registered.touch()
        assert finish(config, "drain")["state"] == "COMMITTED"
        np.testing.assert_array_equal(savedPixels(target), savedPixels(path.parent / "input.png"))
        history = events()
        artifact, = [e for e in history if e.eventType == "artifact.created" and e.nodeId == "save"]
        terminal, = [e for e in history if e.eventType == "job.aborted"]
        assert terminal.code == "E_CANCELLED"
        assert history.index(artifact) < history.index(terminal)
        # ABORTED retirement removes the private Job copy after registration.
        reference = json.loads(artifact.payloadJson)["artifact"]
        assert Path(reference["path"]).parent == runtime.workspaceRoot / jobId / "artifacts"
        assert reference["sizeBytes"] == target.stat().st_size
        assert reference["checksum"] == hashlib.sha256(target.read_bytes()).hexdigest()
    finally:
        release.touch(exist_ok=True)
        registered.touch(exist_ok=True)
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


@pytest.mark.parametrize("stalled", [False, True], ids=["cleaned", "cleanup-timeout"])
def testDownstreamFailureAllowsImageSaveCleanupAndKeepsPrimaryError(monkeypatch, tmp_path, stalled):
    from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator

    ready, release = threading.Event(), threading.Event()
    publish = background_tasks.TaskControl.publish
    service = None

    def heldPublish(control, finalize):
        # The real ImageSaver has closed its temporary file and will unlink it
        # in its finally block when publication is rejected.
        ready.set()
        with service._condition:
            assert service._condition.wait_for(lambda: service._publicationClosed, 5)
        if stalled:
            assert release.wait(5)
        return publish(control, finalize)

    monkeypatch.setattr(background_tasks.TaskControl, "publish", heldPublish)
    if stalled:
        monkeypatch.setattr(background_tasks, "_FAILURE_CLEANUP_SECONDS", .05)
    _stubComputation(monkeypatch, lambda: None)

    class Runner(worker_main.WorkflowRunner):
        def __init__(self, **kwargs):
            nonlocal service
            service = kwargs["backgroundTasks"]

        def run(self, workflowId, inputs, context, token):
            client = service.client(context.forNode("save"), "save")
            ImageSaverOperator().executeNode({"image": np.zeros((8, 8), np.uint8)},
                {"outputPath": str(tmp_path / "frame.png"), "saveMode": "background", "namingMode": "unique"},
                {"backgroundTasks": client})
            assert ready.wait(5) and list(tmp_path.glob("*.tmp"))
            raise WorkflowExecutionError("E_DOWNSTREAM", "next node failed")

    monkeypatch.setattr(worker_main, "WorkflowRunner", Runner)
    events = queue.Queue()
    try:
        with pytest.raises(WorkflowExecutionError) as primary:
            worker_main.runJobProcess(_spec(tmp_path, None), threading.Event(), events)
        assert primary.value.code == "E_DOWNSTREAM"
        failed, = [event for event in events.queue if event["eventType"] == "job.failed"]
        assert failed["code"] == "E_DOWNSTREAM"
        assert not list(tmp_path.glob("*.png"))
        if stalled:
            assert failed["payload"]["backgroundTaskCleanup"]["code"] == "E_BACKGROUND_TASK_CLEANUP_TIMEOUT"
        else:
            assert not list(tmp_path.glob("*.tmp"))
            assert "backgroundTaskCleanup" not in failed["payload"]
    finally:
        release.set()
        if service is not None:
            service._thread.join(5)


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
