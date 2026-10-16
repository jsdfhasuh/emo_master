import pytest

from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.apps.runtime.workflow.loop_runner import LoopExecutionError
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
