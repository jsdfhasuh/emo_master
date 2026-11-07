"""Same-target Runtime resets invalidate only the old instance's history work."""
from concurrent.futures import Future
from types import SimpleNamespace
import threading

import pytest

from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
from tests.designer.qt_wait import waitUntil
from tests.designer.test_runtime_log_usability import Client, Settings


def historyPage(instance, *, cursor=2, upper=4):
    return dict(ok=True, code="OK", message="", runtimeInstanceId=instance,
        upperEventId=upper, nextEventId=cursor, hasMore=cursor < upper,
        events=[dict(eventId=cursor, event=dict(eventType="node.log", message=instance,
            runtimeInstanceId=instance, projectId="history-project"))])


class HistoryClient(Client):
    runtimeService = object()

    def __init__(self):
        self.instance = "alpha"
        self.requests = []

    def queryRuntimeLogs(self, **options):
        self.requests.append(options)
        expected = options.get("runtimeInstanceId")
        if expected and expected != self.instance:
            return dict(ok=False, code="RESET_REQUIRED", message="Runtime instance changed",
                runtimeInstanceId=self.instance, upperEventId=0,
                nextEventId=options.get("afterEventId", 0), hasMore=False, events=[])
        return historyPage(self.instance, cursor=options.get("afterEventId", 0) + 2)


@pytest.fixture
def historyWindow(ownedDesignerWindow):
    client = HistoryClient()
    window = ownedDesignerWindow(client, settingsStore=Settings())
    window.openLogDialog()
    window._stopGlobalLogs(discard=True)
    # An inert reader exposes the same instance and reset queue as the live
    # reader, without timing sleeps or letting a background query race setup.
    resets = []
    reader = SimpleNamespace(runtimeInstanceId="alpha", supported=True,
        take=lambda: [resets.pop(0) for _ in range(len(resets))], service=client.runtimeService,
        close=lambda: None, cursor=0, finished=threading.Event(), unsupported=False)
    window._logResources["reader"] = reader
    window._drainGlobalLogs(reader)
    view = window.logDialog.view
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("runtime"))
    view.historyInputs["projectId"].setText("history-project")
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert view._historyReply["runtimeInstanceId"] == "alpha"
    assert [entry.message for entry in view._entries] == ["alpha"]
    yield window, view, client, reader, resets


@pytest.mark.parametrize("drainReset", [True, False], ids=["reset-drained", "history-timer-first"])
@pytest.mark.parametrize("replyReady", [True, False], ids=["reply-ready", "reply-late"])
def testSameTargetInstanceResetRejectsOldHistoryCompletion(historyWindow, monkeypatch, drainReset, replyReady):
    window, view, client, reader, resets = historyWindow
    pending = Future()
    monkeypatch.setattr(view._historyExecutor, "submit", lambda *_args: pending)
    view.queryHistory(nextPage=True)
    if replyReady:
        pending.set_result(historyPage("alpha", cursor=4))
    reader.runtimeInstanceId = client.instance = "beta"
    resets.append(("reset", dict(runtimeInstanceId="beta")))
    if drainReset:
        window._drainGlobalLogs(reader)
    if not replyReady:
        pending.set_result(historyPage("alpha", cursor=4))
    view._acceptHistory()
    assert view._historyReply is None
    assert view._entries == []
    assert not view.historyNextButton.isEnabled()
    assert view.historyQueryButton.isEnabled()
    assert "未报告成功" in view.historyStatus.text()


def testInstanceResetPreservesLocalFileView(historyWindow):
    window, view, _client, reader, resets = historyWindow
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData("local"))
    local = StructuredLogEntry(1, "INFO", "local-only", source="designer")
    view.setEntries([local])
    generation = view._historyGeneration
    reader.runtimeInstanceId = "beta"
    resets.append(("reset", dict(runtimeInstanceId="beta")))
    window._drainGlobalLogs(reader)
    assert view._entries == [local]
    assert view._historyGeneration == generation


def testSameInstanceReconnectKeepsHistoryScopeAndAllowsNextPage(historyWindow):
    window, view, client, reader, _resets = historyWindow
    previous, generation = view._historyReply, view._historyGeneration
    window._drainGlobalLogs(reader)
    window.openLogDialog()
    assert view._historyReply is previous
    assert view._historyGeneration == generation
    view.queryHistory(nextPage=True)
    waitUntil(lambda: view._historyFuture is None)
    assert client.requests[-1]["afterEventId"] == 2
    assert client.requests[-1]["upperEventId"] == 4
    assert client.requests[-1]["runtimeInstanceId"] == "alpha"
    assert view._historyReply["nextEventId"] == 4


@pytest.mark.parametrize('previousMode', ['current', 'runtime'])
def testDeviceHistoryStartsCleanScopeAndDropsPreviousPage(historyWindow, monkeypatch, previousMode):
    _window, view, client, _reader, _resets = historyWindow
    view.rangeCombo.setCurrentIndex(view.rangeCombo.findData(previousMode))
    oldFilters = dict(projectId='old-project', jobId='old-job', deviceId='old-device',
        component='operator', workflowId='old-workflow', nodeId='business_log',
        startTimestampMs='10', endTimestampMs='20')
    for key, value in oldFilters.items():
        view.historyInputs[key].setText(value)
    view.restoreSettings(dict(area='page', level='ERROR', source='editor', job='old-job',
        workflow=['old-project', 'old-workflow'], node=['old-project', 'old-workflow', 'business_log'],
        event='node.log', search='COMMITTED', autoScroll=False))
    entered, release = threading.Event(), threading.Event()
    query = client.queryRuntimeLogs
    count = len(client.requests)

    def delayed(**options):
        reply = query(**options)
        entered.set()
        assert release.wait(5)
        return reply

    monkeypatch.setattr(client, 'queryRuntimeLogs', delayed)
    scope = dict(projectId='device-project', deviceId='camera')
    try:
        view.openDeviceHistory(**scope)
        waitUntil(entered.is_set)
        assert len(client.requests) == count + 1
        assert {k: v for k, v in client.requests[-1].items() if k != 'cancellation'} == scope
        assert view.rangeCombo.currentData() == 'runtime'
        assert {k: edit.text() for k, edit in view.historyInputs.items() if edit.text()} == scope
        assert view._historyReply is None and view._historyFilters == {}
        assert view._entries == [] and not view.historyNextButton.isEnabled()
        assert not view.historyExportButton.isEnabled()
        settings = view.settings()
        assert settings['area'] == 'all' and settings['search'] == ''
        assert all(settings[key] == '全部' for key in ('level', 'source', 'job', 'workflow', 'node', 'event'))
        assert settings['autoScroll'] is False
    finally:
        release.set()
    waitUntil(lambda: view._historyFuture is None)
    assert view._historyFilters == scope
    assert len(view._visibleEntries) == 1
    assert len(client.requests) == count + 1


def testNewInstanceHistoryIsNotClearedByDelayedLiveReset(historyWindow):
    window, view, client, reader, resets = historyWindow
    # A fresh history query can discover the new Runtime before live polling.
    client.instance = "beta"
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert view._historyReply["runtimeInstanceId"] == "beta"
    previous, generation = view._historyReply, view._historyGeneration
    reader.runtimeInstanceId = "beta"
    resets.append(("reset", dict(runtimeInstanceId="beta")))
    window._drainGlobalLogs(reader)
    assert view._historyReply is previous
    assert [entry.message for entry in view._entries] == ["beta"]
    assert view._historyGeneration == generation


def testCompletedNewInstancePageSurvivesResetBeforeHistoryTimer(historyWindow, monkeypatch):
    window, view, client, reader, resets = historyWindow
    pending = Future()
    monkeypatch.setattr(view._historyExecutor, "submit", lambda *_args: pending)
    view.queryHistory()
    pending.set_result(historyPage("beta"))
    reader.runtimeInstanceId = client.instance = "beta"
    resets.append(("reset", dict(runtimeInstanceId="beta")))
    window._drainGlobalLogs(reader)
    view._acceptHistory()
    assert view._historyReply["runtimeInstanceId"] == "beta"
    assert [entry.message for entry in view._entries] == ["beta"]
    assert view.historyNextButton.isEnabled()


def testSameInstanceLatePageStillAccepted(historyWindow, monkeypatch):
    window, view, _client, reader, _resets = historyWindow
    pending = Future()
    monkeypatch.setattr(view._historyExecutor, "submit", lambda *_args: pending)
    view.queryHistory(nextPage=True)
    window._drainGlobalLogs(reader)
    pending.set_result(historyPage("alpha", cursor=4))
    view._acceptHistory()
    assert view._historyReply["runtimeInstanceId"] == "alpha"
    assert view._historyReply["upperEventId"] == 4
    assert view._historyReply["nextEventId"] == 4
    assert [entry.message for entry in view._entries] == ["alpha"]


def testInstanceSwitchKeepsPendingExportBoundToOriginalRange(historyWindow, tmp_path, monkeypatch):
    from PySide2.QtWidgets import QFileDialog
    window, view, client, reader, resets = historyWindow
    target = tmp_path / "history.jsonl"
    target.write_text("existing target", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    query = client.queryRuntimeLogs

    def delayed(**options):
        reply = query(**options)
        entered.set()
        assert release.wait(5)
        return reply

    monkeypatch.setattr(client, "queryRuntimeLogs", delayed)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(target), "JSON Lines (*.jsonl)"))
    view.exportHistory()
    try:
        waitUntil(entered.is_set)
        reader.runtimeInstanceId = client.instance = "beta"
        resets.append(("reset", dict(runtimeInstanceId="beta")))
        window._drainGlobalLogs(reader)
    finally:
        release.set()
    waitUntil(lambda: view._historyFuture is None)
    assert target.read_text(encoding="utf-8") == "existing target"
    assert "RESET_REQUIRED" in view.historyStatus.text()
    assert all(request["runtimeInstanceId"] == "alpha" and request["upperEventId"] == 4
               for request in client.requests[1:])
    assert view._historyReply is None and not view._entries
    view.queryHistory()
    waitUntil(lambda: view._historyFuture is None)
    assert view._historyReply["runtimeInstanceId"] == "beta"
    assert "afterEventId" not in client.requests[-1]
    assert "upperEventId" not in client.requests[-1]


def testCompletedExportReportsSuccessAfterInstanceReset(historyWindow, tmp_path, monkeypatch):
    from PySide2.QtWidgets import QFileDialog
    window, view, _client, reader, resets = historyWindow
    target = tmp_path / "completed-history.jsonl"
    target.write_text("existing target", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(target), "JSON Lines (*.jsonl)"))
    view.exportHistory()
    view._historyTimer.stop()
    waitUntil(view._historyFuture.done, pump=False)
    assert target.read_text(encoding="utf-8") != "existing target"
    reader.runtimeInstanceId = "beta"
    resets.append(("reset", dict(runtimeInstanceId="beta")))
    window._drainGlobalLogs(reader)
    view._acceptHistory()
    assert "已导出 2 条日志" in view.historyStatus.text()
    assert "取消" not in view.historyStatus.text()
    assert view._historyReply is None and not view._entries
