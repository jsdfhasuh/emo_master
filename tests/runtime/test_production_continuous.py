import threading
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.jobs.worker_main import _runContinuous
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.production_fixture import productionProject, saveDocument, waitFor
from tests.runtime.test_operator_lifecycle import _sequentialProject


class SessionOperator:
    initialized = disposed = calls = 0
    failure = False

    def initOperator(self, context):
        type(self).initialized += 1

    def executeNode(self, inputs, params, context):
        type(self).calls += 1
        if self.failure:
            raise RuntimeError("execution failure")
        return dict(status="ok", outputs={})

    def disposeOperator(self):
        type(self).disposed += 1


@pytest.mark.parametrize("failure", [False, True])
def testRetainedRunnerOwnsLifecycleAcrossCyclesAndReleasesOnExit(failure):
    SessionOperator.initialized = SessionOperator.disposed = SessionOperator.calls = 0
    SessionOperator.failure = failure
    registry = {"test.tracked": SessionOperator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(ProjectDocument.model_validate(_sequentialProject(("state",))))
    events, cancellation = [], threading.Event()
    contexts = []
    def publish(eventType, context, **kwargs):
        events.append(eventType)
        if eventType == "workflow.completed":
            contexts.append(context.workflowRunId)
            if len(contexts) == 3:
                cancellation.set()
    runner = WorkflowRunner(compiled, registry, eventPublisher=publish, retainOperators=True)
    spec = SimpleNamespace(jobId="session", workflowId="main", jobWorkspacePath="", projectId="project", cycleIntervalMs=10)
    with pytest.raises(RuntimeError if failure else CancellationRequested):
        _runContinuous(runner, spec, {}, CancellationToken(cancellation), cancellation)
    assert SessionOperator.initialized == SessionOperator.disposed == 1
    assert SessionOperator.calls == (1 if failure else 3)
    assert len(set(contexts)) == len(contexts)
    assert runner._lifecycleOperators == {}


def testContinuousWaitIsCancellableAndDoesNotReuseInputMutations():
    cancelled = threading.Event()
    inputs = {"values": []}
    seen = []
    class Runner:
        def run(self, workflowId, values, context, token):
            seen.append(len(values["values"]))
            values["values"].append(1)
            if len(seen) == 2:
                cancelled.set()
        def closeSession(self, context, primary):
            assert isinstance(primary, CancellationRequested)
    spec = SimpleNamespace(jobId="j", workflowId="main", jobWorkspacePath="", projectId="p", cycleIntervalMs=50)
    with pytest.raises(CancellationRequested):
        _runContinuous(Runner(), spec, inputs, CancellationToken(cancelled), cancelled)
    assert seen == [0, 0] and inputs == {"values": []}


def testCleanupFailureIsReportedWithoutReplacingPrimaryFailure():
    runner = WorkflowRunner(SimpleNamespace(), {}, retainOperators=True)
    class FailingDispose:
        def disposeOperator(self):
            raise RuntimeError("cleanup failure")
    runner._lifecycleOperators[("main", "n")] = FailingDispose()
    runner._lifecycleOrder.append(("main", "n"))
    primary = WorkflowExecutionError("E_PRIMARY", "primary failure")
    runner.closeSession(RunContext.root("j", "main", ""), primary)
    assert primary.code == "E_PRIMARY"
    assert primary.diagnostics["resourceCleanup"]["code"] == "E_RESOURCE_CLEANUP_FAILED"


def testDisposalFailureDuringStopIsFailureNotSuccessfulCancellation():
    runner = WorkflowRunner(SimpleNamespace(), {}, retainOperators=True)
    class FailingDispose:
        def disposeOperator(self):
            raise RuntimeError("device did not release")
    runner._lifecycleOperators[("main", "device")] = FailingDispose()
    runner._lifecycleOrder.append(("main", "device"))
    with pytest.raises(WorkflowExecutionError) as error:
        runner.closeSession(RunContext.root("j", "main", ""), CancellationRequested("stop"))
    assert error.value.code == "E_RESOURCE_CLEANUP_FAILED"


def testRealSpawnContinuousOneJobPathsResultsStopAndRestart(tmp_path, monkeypatch):
    root = tmp_path / "project"
    productionProject(root)
    monkeypatch.chdir(tmp_path)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root / "project.json")
        first = owner.start()
        def cycles():
            return [event for event in owner.runtime.eventStore.read(first) if event.eventType == "workflow.completed"]
        waitFor(lambda: len(cycles()) >= 4)
        record = owner.runtime.jobRepository.get(first)
        assert record.status == "RUNNING" and record.pid > 0
        assert owner.runtime.jobSupervisor._handles[first][2]._maxsize == 64
        assert len(owner.runtime.jobRepository.all()) == 1
        assert len({event.workflowRunId for event in cycles()}) == len(cycles())
        assert (root / "outputs/result.png").is_file()
        assert not (tmp_path / "outputs/result.png").exists()
        workspace = owner.runtime._workspacePaths[first]
        assert not (workspace / "artifacts").exists()
        assert not list(workspace.rglob("*.png"))
        waitFor(lambda: bool(owner.presentation.store.latest))
        result = next(iter(owner.presentation.store.latest.values()))
        assert next(source.valueJson for source in result.sources if source.sourceId == "count") == "2"
        # Capture and diagnostic queues are independent: preserve the exact
        # captured invocation assertion, but await its event's persistence.
        waitFor(lambda: result.identity.invocationId in {event.workflowRunId for event in cycles()})
        with pytest.raises(ValueError, match="stop the current"):
            owner.start()
        with pytest.raises(ValueError, match="stop the current"):
            owner.load(root)
        owner.stop()
        assert owner.status()["canStart"] and not owner.runtime.jobSupervisor.ownsJobResources(first)
        second = owner.start()
        assert second != first
        waitFor(lambda: any(e.eventType == "workflow.completed" for e in owner.runtime.eventStore.read(second)))
        assert len(owner.presentation.jobs) == 1
        owner.stop()
    finally:
        owner.close()
    assert owner.closed and owner.runtime._closed


def testOrdinaryRuntimeDoesNotApplyProjectContinuousPolicy(tmp_path):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    root = tmp_path / "project"
    document = productionProject(root, save=False)
    document.resources.parameterBindings.clear()
    document.workflows["main"].nodes[1].params["imagePath"] = str(root / "input.png")
    saveDocument(root, document)
    runtime = RuntimeService(dbPath=tmp_path / "ordinary/runtime.sqlite3")
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None).ok
        reply = runtime.StartJob(pb.StartJobRequest(project_id=document.project.projectId), None)
        assert reply.ok
        waitFor(lambda: runtime.jobRepository.get(reply.job_id).isTerminal)
        assert runtime.jobRepository.get(reply.job_id).status == "COMPLETED"
        assert len([e for e in runtime.eventStore.read(reply.job_id) if e.eventType == "workflow.completed"]) == 1
    finally:
        runtime.close()


def testOutputFaultStopsSessionNoRetriesAndCanRestartAfterReload(tmp_path):
    root = tmp_path / "project"
    document = productionProject(root, overwrite=False)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        job = owner.start()
        waitFor(lambda: owner.status()["canLoad"])
        assert owner.status()["state"] == "FAULT"
        assert owner.runtime.jobRepository.get(job).errorCode == "E_OUTPUT_EXISTS"
        completed = [e for e in owner.runtime.eventStore.read(job) if e.eventType == "workflow.completed"]
        assert len(completed) == 1
        count = len(owner.runtime.jobRepository.all())
        time.sleep(.3)
        assert len(owner.runtime.jobRepository.all()) == count
        next(n for n in document.workflows["main"].nodes if n.nodeId == "save").params["overwrite"] = True
        saveDocument(root, document)
        owner.load(root)
        nextJob = owner.start()
        waitFor(lambda: len([e for e in owner.runtime.eventStore.read(nextJob) if e.eventType == "workflow.completed"]) >= 2)
        owner.stop()
    finally:
        owner.close()


def testUncertainStartNeverPermitsAnotherAdmission(tmp_path, monkeypatch):
    owner = ProductionRuntime(tmp_path / "data")
    root = tmp_path / "project"
    productionProject(root, mode="single")
    calls = []
    def fail(request, context):
        calls.append(request.start_request_id)
        raise RuntimeError("unknown start outcome")
    try:
        owner.load(root)
        monkeypatch.setattr(owner.runtime, "StartJob", fail)
        with pytest.raises(RuntimeError, match="unknown start"):
            owner.start()
        assert not owner.status()["canStart"]
        with pytest.raises(RuntimeError, match="Start outcome unknown"):
            owner.start()
        assert len(calls) == 1
    finally:
        owner.close()
