from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.execution.background_tasks import BackgroundTaskService
from emo_master.apps.runtime.execution.errors import WorkflowExecutionError
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.apps.runtime.workflow.loop_runner import LoopExecutionError
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_workflow_while_boolean import booleanPayload, runnerFor, _Advance


def testUnlimitedWhileStillEndsOnFalseBeyondFiniteLimit():
    raw = booleanPayload()
    raw['workflows']['main']['nodes'][1]['loop'].update(unlimited=True, maxIterations=1)
    raw['workflows']['body']['nodes'][1]['params']['limit'] = 1200
    runner = runnerFor(raw)
    result = runner.run('main', dict(count=0, hasNext=True), RunContext.root('job', 'main'), CancellationToken())
    assert result.outputs == dict(count=1200, hasNext=False)
    assert len(_Advance.calls) == 1200
    assert not runner._lifecycleOperators


@pytest.mark.parametrize('value', [1, 'true'])
def testUnlimitedIsNotAnImplicitTruthyFlag(value):
    raw = booleanPayload()
    raw['workflows']['main']['nodes'][1]['loop']['unlimited'] = value
    with pytest.raises(WorkflowCompileError):
        runnerFor(raw)


def testUnlimitedLoopHonorsCancellationAndTimeout(monkeypatch):
    raw = booleanPayload()
    raw['workflows']['main']['nodes'][1]['loop'].update(unlimited=True, timeoutMs=1)
    runner = runnerFor(raw)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancellationRequested):
        runner.run('main', dict(count=0, hasNext=True), RunContext.root('cancel', 'main'), token)
    times = iter([0, 1])
    monkeypatch.setattr('emo_master.apps.runtime.workflow.loop_runner.monotonic', lambda: next(times))
    with pytest.raises(LoopExecutionError, match='timeout'):
        runner.loopRunner.run(runner.compiledProject.workflows['main'].nodes[1],
            dict(count=0, hasNext=True), RunContext.root('timeout', 'main'), CancellationToken())


def _pollingRunner(onRead, *, backgroundTasks=None, **settings):
    class Poll:
        def executeNode(self, inputs, params, runtimeContext):
            onRead()
            return dict(status="ok", outputs={"request": 0})
    payload = WorkflowStore().toPayload()
    main = payload["workflows"]["main"]
    config = dict(contractVersion=2, mode="while", conditionMode="always", bodyWorkflowId="body",
                  unlimited=True, maxIterations=1)
    config.update(settings)
    main["nodes"] = [dict(nodeId="input", kind="workflow_input"), dict(nodeId="while", kind="loop", loop=config),
                     dict(nodeId="output", kind="workflow_output")]
    main["edges"] = []
    payload["workflowOrder"].append("body")
    payload["workflows"]["body"] = dict(name="Single poll", inputs={}, outputs={"request": "integer"}, nodes=[
        dict(nodeId="input", kind="workflow_input"),
        dict(nodeId="read", operatorId="test.poll", outputPorts={"request": "integer"}),
        dict(nodeId="output", kind="workflow_output")], edges=[
        dict(fromNode="read", fromPort="request", toNode="output", toPort="request")])
    registry = {"test.poll": Poll}
    return WorkflowRunner(WorkflowCompiler(registry).compile(ProjectDocument.model_validate(payload)), registry,
                          backgroundTasks=backgroundTasks)


def testStatelessPollingPacesFromPriorStartAndWaitsOutsideTheCycle(monkeypatch):
    now = 0.0
    starts = []
    waits = []
    leased = False
    durations = [0.05, 0.3, 0.01]
    token = CancellationToken()
    def read():
        nonlocal now
        assert leased
        starts.append(now)
        now += durations[len(starts) - 1]
        if len(starts) == 3:
            token.cancel()
    @contextmanager
    def lease(context):
        nonlocal leased
        leased = True
        try:
            yield context
        finally:
            leased = False
    def wait(seconds):
        nonlocal now
        assert not leased
        waits.append(seconds)
        now += seconds
    runner = _pollingRunner(read, minIntervalMs=200, cycleBoundary="iteration")
    runner.cycleScope = lease
    monkeypatch.setattr(runner.loopRunner, "_clock", lambda: now)
    monkeypatch.setattr(token, "wait", wait)
    with pytest.raises(CancellationRequested):
        runner.run("main", {}, RunContext.root("job", "main"), token)
    assert starts == pytest.approx([0, 0.2, 0.5])
    assert waits == pytest.approx([0.15])
    assert not leased


def testStopWakesLongPollingIntervalAndDoesNotReadAgain():
    waiting = Event()
    calls = []
    class NotifyingToken(CancellationToken):
        def wait(self, seconds):
            waiting.set()
            super().wait(seconds)
    runner = _pollingRunner(lambda: calls.append(1), minIntervalMs=10000)
    token = NotifyingToken()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run, "main", {}, RunContext.root("job", "main"), token)
        try:
            assert waiting.wait(2)
        finally:
            token.cancel()
        with pytest.raises(CancellationRequested):
            future.result(timeout=2)
    assert calls == [1]


def testPollingTimeoutExpiresDuringIntervalBeforeNextRead(monkeypatch):
    now = 0.0
    calls = []
    waits = []
    def wait(seconds):
        nonlocal now
        waits.append(seconds)
        now += seconds
    runner = _pollingRunner(lambda: calls.append(1), minIntervalMs=5000, timeoutMs=100)
    monkeypatch.setattr(runner.loopRunner, "_clock", lambda: now)
    token = CancellationToken()
    monkeypatch.setattr(token, "wait", wait)
    with pytest.raises(LoopExecutionError) as raised:
        runner.run("main", {}, RunContext.root("job", "main"), token)
    assert raised.value.code == "E_LOOP_TIMEOUT"
    assert calls == [1]
    assert waits == pytest.approx([0.1])


def testBackgroundFailureDuringWhileIntervalPrecedesTimeout(monkeypatch):
    token = CancellationToken()
    service = BackgroundTaskService(token)
    context = RunContext.root("job", "main")
    client = service.client(context.forNode("save"), "save")
    release = Event()
    now = 0.0

    def failWrite(control):
        assert release.wait(3)
        raise OSError("image write failed")

    def wait(seconds):
        nonlocal now
        now += seconds
        release.set()
        # The FIFO barrier lets the write finish without consuming its failure.
        barrier = client.submit(lambda control: None, taskName="barrier", estimatedBytes=0)
        client.wait(barrier.id)

    runner = _pollingRunner(lambda: client.submit(failWrite, taskName="image.save", estimatedBytes=1),
                            backgroundTasks=service, minIntervalMs=10000, timeoutMs=450)
    monkeypatch.setattr(runner.loopRunner, "_clock", lambda: now)
    monkeypatch.setattr(token, "wait", wait)
    try:
        with pytest.raises(WorkflowExecutionError, match="image write failed") as raised:
            runner.run("main", {}, context, token)
        assert raised.value.code == "E_BACKGROUND_TASK_FAILED"
        assert raised.value.diagnostics["backgroundTask"]["nodeId"] == "save"
        assert 0 < now < .45
    finally:
        release.set()
        service.close()
