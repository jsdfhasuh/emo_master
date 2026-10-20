import json
import threading
import ast
import builtins
from pathlib import Path
from types import SimpleNamespace as Fields

import pytest

from tests.ui.presentation import image_demand_diagnostics as evidence


def owners():
    window = Fields(lastView=Fields(connection='DEADLINE_EXCEEDED', detail='deadline', generation=1),
        widgets={'overview': {'overview-image': (None, Fields(message='DEADLINE_EXCEEDED: deadline', key=''))}})
    result = Fields(identity=Fields(resultKey='result', resultOrdinal=1))
    session = Fields(lock=threading.RLock(), connection='CONNECTED', connectionDetail='', generation=1,
        high={'root': 1}, expired={}, loading={}, latest={'root': (result, {'image': object()}, {})})
    return window, session


def testFailureEvidenceKeepsSubmittedErrorSeparateFromRecoveredSession():
    window, session = owners()
    value = json.loads(evidence.imageDemandFailureDetails(window, session, 'result'))
    assert 'non-atomic' in value['diagnostics']
    assert value['last_view']['connection'] == 'DEADLINE_EXCEEDED'
    assert value['session']['connection'] == 'CONNECTED'
    assert value['session']['cached'] == {'key': 'result', 'ordinal': 1,
        'same_expected_key': True, 'image_present': True}


def testMissingFieldsAreUnknownWhileKnownAbsenceIsNone():
    window, session = owners()
    window.lastView = None
    window.widgets = {}
    session.latest.clear()
    value = json.loads(evidence.imageDemandFailureDetails(window, session, 'result'))
    assert value['last_view'] is None and value['session']['cached'] is None
    assert value['image'] == {'unknown': 'unavailable_or_changed'}
    assert json.loads(evidence.imageDemandFailureDetails(object(), object(), 'result'))['session']['unknown']


def testBusyLockDoesNotWaitOrReadClientFields(monkeypatch):
    class BusyLock:
        def acquire(self, *, blocking):
            assert blocking is False
            return False
        def release(self):
            raise AssertionError('must not release an unacquired lock')
    monkeypatch.setattr(evidence, 'RLock', BusyLock)
    value = json.loads(evidence.imageDemandFailureDetails(object(), Fields(lock=BusyLock()), 'result'))
    assert value['session'] == {'unknown': 'lock_unavailable'}


def testSamplingFailureReleasesAcquiredLock(monkeypatch):
    released = []
    class Lock:
        def acquire(self, *, blocking):
            assert blocking is False
            return True
        def release(self):
            released.append(True)
    monkeypatch.setattr(evidence, 'RLock', Lock)
    value = json.loads(evidence.imageDemandFailureDetails(object(), Fields(lock=Lock()), 'result'))
    assert value['session'] == {'unknown': 'unavailable_or_changed'} and released == [True]


def testOversizedScalarJsonIsBoundedAndValid():
    window, session = owners()
    large = '\U0001f642' * 1000
    window.lastView.connection = window.lastView.detail = large
    image = window.widgets['overview']['overview-image'][1]
    image.message = image.key = large
    session.connection = session.connectionDetail = large
    text = evidence.imageDemandFailureDetails(window, session, large)
    assert json.loads(text) == {'unknown': 'output_budget_exceeded'}
    assert len((evidence.PREFIX + text + '\n').encode()) <= evidence.MAX_OUTPUT_BYTES


@pytest.mark.parametrize('broken', ['sampling', 'output'])
def testDiagnosticFailurePreservesOriginalAssertion(monkeypatch, broken):
    def fail(*_args, **_kwargs):
        raise RuntimeError('diagnostic unavailable')
    monkeypatch.setattr(evidence, 'imageDemandFailureDetails' if broken == 'sampling' else 'print', fail, raising=False)
    original = AssertionError('original image predicate')
    with pytest.raises(AssertionError) as caught:
        try:
            raise original
        except AssertionError:
            evidence.emitImageDemandFailure(*owners(), 'result')
            raise
    assert caught.value is original


def testActualCallsitePreservesAssertionWhenDiagnosticImportFails(monkeypatch):
    path = Path(__file__).parents[2] / 'ui/presentation/test_image_demand_views.py'
    module = ast.parse(path.read_text(encoding='utf-8'))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
        and node.name == 'testThousandHideShowCyclesReuseSameImageAndReleaseDemandOwner')
    block = next(node for node in ast.walk(function) if isinstance(node, ast.Try)
        and node.handlers and isinstance(node.body[0], ast.Assert))
    code = compile(ast.Module(body=[block], type_ignores=[]), str(path), 'exec')
    original = AssertionError('original predicate failure')
    imports = []
    originalImport = builtins.__import__

    def failingImport(name, *args, **kwargs):
        if name == 'tests.ui.presentation.image_demand_diagnostics':
            imports.append(name)
            raise ImportError('diagnostic unavailable')
        return originalImport(name, *args, **kwargs)

    def failedPredicate(_window):
        raise original

    monkeypatch.setattr(builtins, '__import__', failingImport)
    with pytest.raises(AssertionError) as caught:
        exec(code, {'imageReady': failedPredicate, 'window': object()})
    assert caught.value is original and len(imports) == 1
