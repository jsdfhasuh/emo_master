from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide2.QtCore import Qt
from shiboken2 import delete

from examples.runtime_pages_p3 import sampleProjectP3
from emo_master.clients.runtime.view_state import JobView
from emo_master.core.presentation.models import Action
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.ui.presentation.job_status import jobStatusText
from emo_master.ui.presentation.renderer import RuntimePages
from tests.ui.presentation.test_renderer import resultView


def job(status='RUNNING', **values):
    return JobView(runtimeInstanceId='runtime', projectId='project', jobId='job',
                   status=status, availability='AVAILABLE', captureEnabled=True,
                   resourcesReleased=False, **values)


@pytest.mark.parametrize('availability', ['CONNECTING', 'UNAVAILABLE', 'NOT_FOUND',
                                         'RESET_REQUIRED', 'INVALID_STATUS'])
def testUnavailableExecutionNeverFormatsOldRunningAsLive(availability):
    text = jobStatusText(replace(job(), availability=availability, detail='状态读取失败'))
    assert '执行状态不可用' in text
    assert '状态读取失败' in text
    assert 'RUNNING' not in text


def testExecutionTerminalAndResourceRetirementAreSeparate():
    text = jobStatusText(job('COMPLETED'))
    assert 'COMPLETED' in text and '尚未全部释放' in text
    text = jobStatusText(replace(job('COMPLETED'), resourcesReleased=True, captureEnabled=False))
    assert '任务资源已释放' in text and '无页面采集数据' in text
    assert '执行状态不等于产品 OK/NG' in text
    assert '不可用' in jobStatusText(None)
    assert '不可用' in jobStatusText(job('unknown'))


class ObservedSession:
    imageDemand = False

    def __init__(self, view):
        self.view = view
        self.pin = None
        self.released = []

    def readSnapshot(self):
        return self.view

    def pins(self):
        return self

    def acquire(self, scope, generation, ttlMs, *, sourceIds):
        self.pin = SimpleNamespace(state='PINNED', scope=scope, error='')
        return 'ticket'

    def read(self, ticket):
        assert ticket == 'ticket'
        return self.pin

    def release(self, ticket):
        self.released.append(ticket)


def testHubPublishesStatusWithoutNewResultAndKeepsFrozenBusinessValue(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    window = RuntimePages(config)
    session = ObservedSession(replace(resultView(count='7', capture=window.expectedCapture), job=job()))
    hub = DisplayHub(session)
    window.hub = hub
    hub.attach(window)
    window.show()
    try:
        assert window.jobStatus.textFormat() == Qt.PlainText
        hub.tick()
        window.act(Action(type='navigate', pageId='detail', context='displayed_result', resultScopeId='root'))
        assert window.frozen == 'ticket'
        frozenKey = window.displayed['root'].result.identity.resultKey
        for status in ('STOPPING', 'COMPLETED'):
            session.view = replace(session.view, job=job(status))
            hub.tick()
            assert status in window.jobStatus.text()
            assert window.displayed['root'].result.identity.resultKey == frozenKey
            assert window.widgets['detail']['detail-count'][1].text() == '7 个'
            assert window.frozen == 'ticket'
        session.view = replace(session.view, job=replace(job('COMPLETED'), resourcesReleased=True))
        hub.tick()
        assert '任务资源已释放' in window.jobStatus.text()
        session.view = replace(session.view, job=replace(job(), status='', availability='UNAVAILABLE', detail='状态超时'))
        hub.tick()
        assert '状态超时' in window.jobStatus.text() and 'RUNNING' not in window.jobStatus.text()
        assert window.status.text().startswith('CONNECTED')
        assert window.frozen == 'ticket'
    finally:
        window.close()
        delete(window)
        delete(hub)
    assert session.released == ['ticket']


def testOfflineSimulationDoesNotPretendJobIsExecuting(qtApp, tmp_path):
    window = RuntimePages(sampleProjectP3(tmp_path).presentation)
    try:
        window.setSimulationState('OK')
        window.submit(replace(resultView(capture=window.expectedCapture), job=job()))
        assert window.jobStatus.text() == '离线模拟 · 无真实任务执行状态'
    finally:
        window.close()
        delete(window)
