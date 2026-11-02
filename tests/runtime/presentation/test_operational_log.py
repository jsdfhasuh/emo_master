"""Display failures use the existing event history and operational JSONL sink."""
from collections import deque
import json
import threading
import time
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.collector import unavailable
from emo_master.apps.runtime.presentation.operational_log import PresentationLog
from tests.runtime.presentation.test_closure_failures import assembler, opened, seal
from tests.runtime.presentation.test_images import item
from tests.runtime.test_jsonl_logging import _records


BUSY = 'source image lane is busy; capture never waits'
GOOD = {'sourceId': 'n', 'state': 'AVAILABLE', 'valueJson': '1'}


def sourceItem(ordinal=1):
    value = item(ordinal)
    value['workflowId'] = 'main'
    value['sourceOrigins'] = {'n': {'workflowId': 'camera', 'nodeId': 'preview', 'port': 'image'}}
    value['sourceObservations'] = {'n': {'nodeRunId': 'node-run', 'workflowRunId': 'flow-run', 'iterationPath': [3]}}
    return value


@pytest.mark.parametrize('code,reason,level,kind', [
    ('BUDGET_EXCEEDED', BUSY, 'INFO', 'IMAGE_BUSY'),
    ('BUDGET_EXCEEDED', 'raw image 6220800 bytes exceeds source lane limit 4194304; no implicit resize', 'WARN', 'DISPLAY_LIMIT'),
    ('BUDGET_EXCEEDED', 'image slab quarantined until actual owner retirement', 'ERROR', 'IMAGE_QUARANTINED'),
    ('EXPORT_TIMEOUT', 'encoder deadline', 'WARN', 'EXPORT_TIMEOUT'),
    ('EXPORT_FAILED', 'encoder failed', 'ERROR', 'EXPORT_FAILED'),
    ('IPC_ERROR', 'reader failed', 'ERROR', 'IPC_ERROR'),
])
def testSourceFailureLevelsAndResultContext(code, reason, level, kind):
    store = EventStore()
    log = PresentationLog(store.append, 'project')
    log.observe(sourceItem(), [unavailable('n', code, reason)])
    event, = store.read('job')
    assert (event.level, event.code) == (level, kind)
    assert (event.projectId, event.workflowId, event.nodeId, event.nodeRunId) == ('project', 'camera', 'preview', 'node-run')
    payload = json.loads(event.payloadJson)
    assert payload['identity']['resultKey'] == 'job-scope-1'
    assert payload['reasonCode'] == code and payload['reason'] == reason
    assert payload['sourceId'] == 'n' and payload['occurrences'] == 1
    assert 'valueJson' not in payload


def testBusyHealthyAlternationIsSummarizedAndStableSuccessRecovers():
    now = [0.0]
    store = EventStore()
    log = PresentationLog(store.append, 'project', clock=lambda: now[0])
    for ordinal in range(100):
        now[0] = ordinal * .1
        log.observe(sourceItem(ordinal * 2 + 1), [unavailable('n', 'BUDGET_EXCEEDED', BUSY)])
        log.observe(sourceItem(ordinal * 2 + 2), [GOOD])
    assert len(store.read('job')) == 1
    now[0] = 30.0
    log.observe(sourceItem(201), [unavailable('n', 'BUDGET_EXCEEDED', BUSY)])
    summary = store.read('job')[-1]
    assert summary.level == 'WARN'
    assert json.loads(summary.payloadJson)['occurrencesSinceLastLog'] == 100
    now[0] = 31.0
    log.observe(sourceItem(202), [GOOD])
    now[0] = 32.1
    log.observe(sourceItem(203), [GOOD])
    recovered = store.read('job')[-1]
    assert recovered.eventType == 'presentation.source.recovered' and recovered.level == 'INFO'
    assert json.loads(recovered.payloadJson)['identity']['resultOrdinal'] == 203
    assert not log.issues
    log.observe(sourceItem(204), [GOOD])
    assert len(store.read('job')) == 3


def testNormalAbsenceCancelAndBusinessNodeFailureDoNotDuplicateLogs():
    store = EventStore()
    log = PresentationLog(store.append, 'project')
    for ordinal, code in enumerate(('OPTIONAL_ABSENT', 'BRANCH_SKIPPED', 'EXECUTION_CANCELLED', 'NODE_FAILED'), 1):
        log.observe(sourceItem(ordinal), [unavailable('n', code)])
    log.observe(sourceItem(5), [GOOD])
    log.finish()
    assert store.read('job') == [] and not log.issues


def testLateOlderSuccessCannotRecoverANewerFailure():
    now = [0.0]
    store = EventStore()
    log = PresentationLog(store.append, 'project', clock=lambda: now[0])
    log.observe(sourceItem(2), [unavailable('n', 'BUDGET_EXCEEDED', BUSY)])
    now[0] = 3.0
    log.observe(sourceItem(1), [GOOD])
    log.observe(sourceItem(2), [GOOD])  # Duplicate deliveries do not change state either.
    log.finish()
    events = store.read('job')
    assert len(events) == 1 and events[0].eventType == 'presentation.source.unavailable'
    assert json.loads(events[0].payloadJson)['identity']['resultOrdinal'] == 2


def testLateExportFailureIsLoggedAfterNewerBusyResultCloses():
    store = EventStore()
    service, credit = assembler()
    credit.semaphore = threading.BoundedSemaphore(2)
    service.logs = {'job': PresentationLog(store.append, 'project')}
    older = opened(service, credit, 1)
    pending = seal(older, {'sourceId': 'n', 'pendingImage': True})
    pending['sealedAt'] = time.monotonic()
    service._consume('job', pending)
    newer = opened(service, credit, 2)
    service._consume('job', seal(newer, unavailable('n', 'BUDGET_EXCEEDED', BUSY)))
    service._exported({'key': older, 'sourceId': 'n'}, None, 'EXPORT_FAILED')
    service.logs['job'].finish()
    events = store.read('job')
    assert [(event.level, event.code) for event in events] == [('INFO', 'IMAGE_BUSY'), ('ERROR', 'EXPORT_FAILED')]
    late = json.loads(events[-1].payloadJson)
    assert late['identity']['resultOrdinal'] == 1 and late['currentResultOrdinal'] == 2
    assert late['lateResult'] is True
    assert credit.released == 2


def testLateFailureEvidenceIsDeduplicatedWithoutChangingCurrentRecovery():
    now = [0.0]
    store = EventStore()
    log = PresentationLog(store.append, 'project', clock=lambda: now[0])
    failure = unavailable('n', 'EXPORT_FAILED', 'encoder failed')
    log.observe(sourceItem(3), [unavailable('n', 'BUDGET_EXCEEDED', BUSY)])
    log.observe(sourceItem(2), [failure])
    log.observe(sourceItem(2), [failure])
    now[0] = 1.0
    log.observe(sourceItem(4), [GOOD])
    now[0] = 2.1
    log.observe(sourceItem(5), [GOOD])
    assert not log.issues
    log.observe(sourceItem(1), [failure])
    log.observe(sourceItem(1), [failure])
    log.finish()
    events = store.read('job')
    assert [event.code for event in events] == ['IMAGE_BUSY', 'EXPORT_FAILED', 'PRESENTATION_RECOVERED', 'EXPORT_FAILED']
    assert json.loads(events[-1].payloadJson)['occurrences'] == 2
    assert json.loads(events[-1].payloadJson)['occurrencesSinceLastLog'] == 1
    assert sum(event.eventType.endswith('.recovered') for event in events) == 1


def testFinishFlushesCountsOnceWithoutInventingRecovery():
    store = EventStore()
    log = PresentationLog(store.append, 'project', clock=lambda: 0)
    for ordinal in range(5):
        log.observe(sourceItem(ordinal), [unavailable('n', 'EXPORT_TIMEOUT')])
    log.finish()
    log.finish()
    events = store.read('job')
    assert len(events) == 2
    assert all(event.eventType == 'presentation.source.unavailable' for event in events)
    assert json.loads(events[-1].payloadJson)['occurrencesSinceLastLog'] == 4


def testClosedResultLogsPersistInTheExistingSqliteAndJsonl(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs', logDirectory=tmp_path / 'logs')
    service, credit = assembler()
    now = [0.0]
    service.runtime = runtime
    service.logs = {'job': PresentationLog(runtime.eventStore.append, 'project', clock=lambda: now[0])}
    service.readers = {}
    try:
        for ordinal, source in [(1, unavailable('n', 'BUDGET_EXCEEDED', BUSY)), (2, GOOD)]:
            assert credit.acquire()
            value = sourceItem(ordinal)
            service._consume('job', {'eventType': 'display.open', 'item': value})
            service._consume('job', seal(value['identity']['resultKey'], source))
            now[0] += 2
        assert service.waitForLogDrain('job')
        events = runtime.eventStore.read('job')
        assert [event.eventType for event in events] == ['presentation.source.unavailable', 'presentation.source.recovered']
        assert credit.released == 2 and not service.pending
        persisted = runtime.sqliteStore.listJobEventsAfter('job')
        assert [(e.sequence, e.code) for e in persisted] == [(1, 'IMAGE_BUSY'), (2, 'PRESENTATION_RECOVERED')]
    finally:
        runtime.close()
    records = _records(tmp_path / 'logs')
    assert [row['sequence'] for row in records] == [1, 2]
    assert records[0]['payload']['reason'] == BUSY
    assert records[1]['eventType'] == 'presentation.source.recovered'


def testLogAppendFailureCannotPreventResultAndCreditRetirement():
    service, credit = assembler()
    errors = deque(maxlen=64)
    service.runtime = SimpleNamespace(jobSupervisor=SimpleNamespace(presentationErrors=errors))

    def broken(*_args, **_kwargs):
        raise OSError('log disk busy')

    service.logs = {'job': PresentationLog(broken, 'project')}
    key = opened(service, credit)
    service._consume('job', seal(key, unavailable('n', 'EXPORT_FAILED')))
    assert credit.released == 1 and not service.pending and not service.store.open
    assert service.store.history[-1].sources[0].reasonCode == 'EXPORT_FAILED'
    assert errors and 'log disk busy' in errors[-1]


def testDrainWaitsForOnlyItsReaderAndSupportsCancellation():
    service, _ = assembler()
    released = threading.Event()
    reader = threading.Thread(target=lambda: released.wait(2))
    service.readers = {'job': reader}
    reader.start()
    try:
        assert not service.waitForLogDrain('job', timeoutSeconds=.01)
        assert service.waitForLogDrain('unrelated', timeoutSeconds=.01)
        assert not service.waitForLogDrain('job', isActive=lambda: False)
        released.set()
        assert service.waitForLogDrain('job')
    finally:
        released.set()
        reader.join(2)
