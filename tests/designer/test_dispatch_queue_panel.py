"""Qt polling, context fences and read-only rendering for dispatch monitoring."""
from copy import deepcopy
import sys
from threading import Event

import pytest
from PySide2.QtCore import QCoreApplication, QEvent
from PySide2.QtWidgets import QWidget
from shiboken2 import isValid

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.designer.ui.dispatch_queue_panel import DispatchQueuePanel, timestamp
from emo_master.apps.designer.ui.global_variables_dialog import GlobalVariablesDialog
from tests.designer.test_global_counters_dialog import _waitUntil


def state(group="group", *, instance="runtime", job="producer", items=False):
    slot = dict(targetSlotId="slot", workflowId="consumer", workflowName="Consumer", jobId="consumer-job",
        capacity=1, queued=1, processing=0, state="RUNNING", queueFullPolicies=["wait"],
        waitingProducers=[dict(jobId=job, sinceMs=1000)], lastErrorCode="", acceptedTotal=3,
        completedTotal=2, failedTotal=0, cancelledTotal=0, rejectedTotal=0)
    if items:
        slot["items"] = [dict(invocationId="invocation-1234567890", deliveryId="delivery", state="QUEUED",
            position=1, queuedAtMs=1000, startedAtMs=None, frameCount=0, frames=[])]
    return dict(runtimeInstanceId=instance, snapshotAtMs=1000, groups=[dict(runGroupId=group,
        sourceJobId=job, sourceWorkflowName="Producer", state="RUNNING", consumerJobIds=["consumer-job"])],
        selected=dict(runGroupId=group, sourceJobId=job, state="RUNNING", slots=[slot]), state="AVAILABLE")


class Client:
    def __init__(self):
        self.calls = []
        self.entered, self.release = Event(), Event()
        self.block, self.error = False, None
        self.snapshot = state()
        self.variableCalls = 0

    def dispatchQueueState(self, *args):
        self.calls.append(args)
        snapshot = deepcopy(self.snapshot)
        self.entered.set()
        if self.block:
            assert self.release.wait(5)
        if self.error:
            raise self.error
        if args[3]:
            snapshot["selected"]["slots"][0]["items"] = []
        return snapshot

    def globalVariableState(self, *args):
        self.variableCalls += 1
        return dict(variables=[], jobs=[dict(jobId="producer", workflowId="main", ended=False)])


def testOnlyActiveTabPollsAndDetailIsOptIn(designerApplication):
    client = Client()
    dialog = GlobalVariablesDialog(client, lambda: {}, lambda _: None, lambda: False, lambda: "producer")
    try:
        dialog.showForProject("project")
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        assert not client.calls and not dialog.queuePanel.timer.isActive()
        dialog.tabs.setCurrentIndex(1)
        panel = dialog.queuePanel
        _waitUntil(designerApplication, lambda: panel._worker is None and panel.table.rowCount() == 1)
        assert not dialog.timer.isActive() and panel.timer.isActive()
        assert all(not args[3] for args in client.calls)
        assert not dialog.buttons["set"].isVisible() and dialog.buttons["refresh"].isVisible()
        panel.details.setChecked(True)
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert client.calls[-1] == ("project", "group", "slot", True)
        assert "策略 等待容量" in panel.metadata.text()
        assert f"生产等待：producer，自 {timestamp(1000)}" in panel.metadata.text()
        panel.table.selectRow(0)
        panel.refresh()
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert panel.selectedSlot() == "slot" and panel.details.isChecked()
        dialog.hide()
        calls = len(client.calls)
        panel.refresh()
        assert not panel.timer.isActive() and not dialog.timer.isActive() and len(client.calls) == calls
        dialog.showForProject("project")
        _waitUntil(designerApplication, lambda: panel._worker is None)
        dialog.tabs.setCurrentIndex(0)
        _waitUntil(designerApplication, lambda: dialog._worker is None)
        assert dialog.timer.isActive() and not panel.timer.isActive()
        assert dialog.jobs.currentData() == "producer"
    finally:
        dialog.close()


@pytest.mark.parametrize("change", ["project", "group", "connection"])
def testSlowResponseNeverOverwritesNewContext(designerApplication, change):
    client = Client()
    current = [client]
    panel = DispatchQueuePanel(lambda: current[0], lambda: "producer")
    panel.bindProject("project")
    client.block = True
    try:
        panel.setActive(True)
        assert client.entered.wait(2)
        worker = panel._worker
        for _ in range(20):
            panel.refresh()
        assert len(client.calls) == 1 and panel._worker is worker
        replacement = state("new-group", instance="new-runtime")
        if change == "project":
            panel.bindProject("new-project")
            client.snapshot = replacement
        elif change == "group":
            panel.groups.addItem("New group", "new-group")
            panel.groups.setCurrentIndex(1)
            assert panel.table.rowCount() == 0
            client.snapshot = replacement
        else:
            current[0] = Client()
            current[0].snapshot = replacement
        client.release.set()
        _waitUntil(designerApplication, lambda: panel._worker is None and panel.instanceId == "new-runtime")
        assert panel.groups.currentData() == "new-group"
        assert panel.table.rowCount() == 1
    finally:
        client.release.set()
        panel.shutdown()
        _waitUntil(designerApplication, lambda: panel._worker is None)
        panel.close()


def testFailureRetainsStaleSnapshotThenRecoversAndRetirementIsNotZero(designerApplication):
    client = Client()
    panel = DispatchQueuePanel(lambda: client, lambda: "producer")
    panel.bindProject("project")
    try:
        panel.setActive(True)
        _waitUntil(designerApplication, lambda: panel._worker is None)
        panel.timer.stop()
        client.error = RuntimeClientError("UNAVAILABLE", "connection unavailable")
        panel.refresh()
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert "数据已过期" in panel.status.text() and "connection unavailable" in panel.status.text()
        assert panel.table.item(0, 3).text() == "3"
        client.error = None
        panel.refresh()
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert "数据已过期" not in panel.status.text()
        client.snapshot.update(groups=[], selected=None, state="UNAVAILABLE")
        panel.refresh()
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert "未保留队列快照" in panel.status.text() and panel.table.rowCount() == 0
        client.snapshot = state("replacement", instance="restarted")
        panel.refresh()
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert panel.groups.currentData() == "replacement"
        assert panel.groups.findData("group") == -1
    finally:
        panel.shutdown()
        panel.close()


@pytest.mark.parametrize("nativeDeletion", [False, True])
def testHiddenOrDestroyedPanelIgnoresInFlightQuery(designerApplication, monkeypatch, nativeDeletion):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    client = Client()
    client.block = True
    parent = QWidget()
    panel = DispatchQueuePanel(lambda: client, lambda: "producer", parent)
    panel.bindProject("project")
    panel.setActive(True)
    worker = panel._worker
    try:
        assert client.entered.wait(2)
        if nativeDeletion:
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(parent, QEvent.DeferredDelete)
            assert not isValid(panel)
        else:
            panel.shutdown()
        client.release.set()
        assert worker.wait(3000)
        if nativeDeletion:
            def retired():
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                return not isValid(worker)
            _waitUntil(designerApplication, retired)
            assert panel._worker is worker
        else:
            _waitUntil(designerApplication, lambda: panel._worker is None)
            assert panel.table.rowCount() == 0 and not panel.timer.isActive()
        assert not errors
    finally:
        client.release.set()
        if isValid(parent):
            panel.shutdown()
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(parent, QEvent.DeferredDelete)


def testOldRuntimeUnsupportedDoesNotPollOrBlockVariableTab(designerApplication):
    class Legacy:
        def GetDispatchQueueState(self, *_args, **_kwargs):
            raise RuntimeClientError("UNIMPLEMENTED", "old Runtime")
    client = RuntimeClient(Legacy())
    with pytest.raises(RuntimeClientError) as error:
        client.dispatchQueueState("project")
    assert error.value.code == "E_DISPATCH_MONITOR_UNSUPPORTED"
    panel = DispatchQueuePanel(lambda: client, lambda: "")
    panel.bindProject("project")
    try:
        panel.setActive(True)
        _waitUntil(designerApplication, lambda: panel._worker is None)
        assert "不支持队列监控" in panel.status.text() and not panel.timer.isActive()
    finally:
        panel.shutdown()
        panel.close()
