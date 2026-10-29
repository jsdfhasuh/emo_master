"""Page-local messages retain their origin in the shared Designer log window."""
import pytest

from test_workspace import designer  # noqa: F401
from test_pending_inputs import setupPage


def testPageFeedbackIsDistinctFromGeneralDesignerMessages(designer):  # noqa: F811
    coordinator = designer.pageCoordinator
    coordinator.showPages()
    designer._clearRuntimeLogCache()
    designer.appendRuntimeLog('INFO', '项目已保存')
    coordinator.preview.message('页面测试准备失败', 'ERROR')

    general, page = designer.logEntries
    assert (general.source, general.message) == ('designer', '项目已保存')
    assert (page.source, page.level, page.message) == ('page_designer', 'ERROR', '页面测试准备失败')
    assert designer.statusBar().currentMessage() == '页面测试准备失败'


def testPageObserverFailureDoesNotRelabelRuntimeEvents(designer):  # noqa: F811
    designer._clearRuntimeLogCache()
    designer.pageCoordinator.preview._appendLogEvents([
        ('error', '页面日志读取失败；正在重连'),
        ('event', {'timestampMs': 1, 'eventType': 'presentation.source.unavailable',
                   'jobId': 'external-job', 'level': 'WARN', 'message': '图片导出超时',
                   'payload': {'sourceId': 'camera'}}),
    ])

    local, runtime = designer.logEntries
    assert (local.source, local.level) == ('page_designer', 'WARN')
    assert (runtime.source, runtime.eventType) == ('runtime', 'presentation.source.unavailable')
    assert runtime.jobId == 'external-job' and runtime.payload['sourceId'] == 'camera'


def testPagePropertyFailureUsesPageSource(designer):  # noqa: F811
    coordinator, editor, _ = setupPage(designer)
    editor.tools.fields['fontSize'].setValue(1)
    before = coordinator.session.payload()
    designer._clearRuntimeLogCache()
    with pytest.raises(ValueError):
        editor.tools.apply()

    entry, = designer.logEntries
    assert (entry.source, entry.level) == ('page_designer', 'ERROR')
    assert 'fontSize' in entry.message
    assert coordinator.session.payload() == before
    editor.tools.fields['fontSize'].setValue(16)
