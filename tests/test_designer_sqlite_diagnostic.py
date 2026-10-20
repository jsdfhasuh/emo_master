"""Failure observation happens before cleanup without changing the original wait."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import designer_sqlite_diagnostic as module


def testWaitPreservesArgumentsResultAndDoesNotCaptureSuccess():
    values = []
    sentinel = object()
    def original(*args, **kwargs):
        values.append((args, kwargs))
        return sentinel
    wrapped = module.observedWait(original, lambda: pytest.fail('unexpected capture'))
    assert wrapped('app', sentinel, timeout=10) is sentinel
    assert values == [(('app', sentinel), {'timeout': 10})]


def testFailureSnapshotBeforeCleanupAndSameOriginalErrorEvenIfCaptureFails():
    error = AssertionError('original watchdog')
    order = []
    def original():
        order.append('original')
        raise error
    def capture():
        order.append('capture')
        raise RuntimeError('diagnostic error')
    with pytest.raises(AssertionError) as caught:
        try:
            module.observedWait(original, capture)()
        finally:
            order.append('cleanup')
    assert caught.value is error and order == ['original', 'capture', 'cleanup']


def testDoesNotCatchOtherFailuresOrReadLocals():
    error = ValueError('original operation')
    def original():
        raise error
    with pytest.raises(ValueError) as caught:
        module.observedWait(original, lambda: pytest.fail('unexpected capture'))()
    assert caught.value is error
    secret = 'private-local-value-must-not-appear'
    output = module.threadStacks(Path(__file__).resolve().parents[1])
    assert secret not in json.dumps(output)
    assert len(output['threads']) <= 32
    for row in output['threads']:
        assert len(row['frames']) <= 32
        assert all(set(frame) == {'file', 'line', 'function'} for frame in row['frames'])
        assert all(not Path(frame['file']).is_absolute() for frame in row['frames'])


def testOnlyExactOriginalTestIsAllowed():
    config = SimpleNamespace(_designer_sqlite={})
    module.pytest_collection_modifyitems(config, [SimpleNamespace(nodeid=module.TARGET)])
    for values in ([], [module.TARGET, module.TARGET], ['other']):
        with pytest.raises(pytest.UsageError):
            module.pytest_collection_modifyitems(config, [SimpleNamespace(nodeid=v) for v in values])


def testFailureSnapshotIsBoundedAndTakenOnlyOnce(tmp_path, monkeypatch):
    observers = module.base.Observers()
    observers.probes = [SimpleNamespace(snapshot=lambda: {'active': ['original phase']})]
    state = {'observers': observers}
    monkeypatch.setattr(module, 'threadStacks', lambda root: {'threads': []})
    module.captureFailure(state, tmp_path)
    first = state['before_cleanup']
    module.captureFailure(state, tmp_path)
    assert state['before_cleanup'] is first
    assert first['stores'] == [{'active': ['original phase']}]
