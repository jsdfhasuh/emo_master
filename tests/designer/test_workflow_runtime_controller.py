from types import SimpleNamespace

from emo_master.apps.designer.controllers.workflow_runtime_controller import WorkflowRuntimeController


def testStopAllAttemptsEveryChildWhenOneRequestRaises():
    manager = object.__new__(WorkflowRuntimeController)
    calls, logs = [], []
    def stopFirst():
        calls.append('first')
        raise RuntimeError('transport failed')
    manager.runs = {
        key: SimpleNamespace(workflowId=key, controller=SimpleNamespace(
            _jobActive=True, _startUncertain=False, _worker=None, _stopWorker=None,
            stopJob=stop))
        for key, stop in [('first', stopFirst), ('second', lambda: calls.append('second'))]
    }
    manager.callbacks = {'appendLog': lambda *args: logs.append(args)}
    manager.stopAll()
    assert calls == ['first', 'second']
    assert len(logs) == 1 and 'first' in logs[0][1] and 'transport failed' in logs[0][1]
