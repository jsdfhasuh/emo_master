"""Design-area tabs share the existing search, retention, and detail view."""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest
from PySide2.QtCore import QSettings
from PySide2.QtWidgets import QApplication, QTreeWidget, QSplitter

from emo_master.apps.designer.ui.log_dialog import LogDialog, StructuredLogEntry


def records():
    return [
        StructuredLogEntry(1770000000001, "INFO", "开始执行节点", source="runtime",
                           eventType="node.started", workflowId="capture", workflowName="相机采集",
                           nodeId="preview", nodeName="页面预览", jobId="demo-job"),
        StructuredLogEntry(1770000000002, "WARN", "图片导出超时", source="runtime",
                           eventType="presentation.source.unavailable", code="EXPORT_TIMEOUT",
                           workflowId="capture", workflowName="相机采集", nodeId="preview",
                           nodeName="页面预览", jobId="demo-job", payload={
                               "sourceId": "camera-image", "reasonCode": "EXPORT_TIMEOUT",
                               "reason": "图片导出等待超过时限", "occurrences": 3,
                               "exports": [{"stage": "encoding", "elapsedMs": 520}],
                               "recovered": False, "lastError": None}),
        StructuredLogEntry(1770000000003, "ERROR", "页面预览操作失败", source="page_designer"),
        StructuredLogEntry(1770000000004, "INFO", "已连接 Runtime", source="designer"),
    ]


@pytest.mark.parametrize("source,event,expected", [
    ("runtime", "presentation.source.unavailable", "page"),
    ("page_designer", "", "page"),
    ("runtime", "node.started", "flow"),
    ("runtime", "node.preview.failed", "flow"),
    ("runtime", "preview.snapshot.failed", "flow"),
    ("runtime", "job.failed", "flow"),
    ("editor", "", "flow"),
    ("flow_designer", "", "flow"),
    ("designer", "", "general"),
    ("runtime", "device.connect.failed", "general"),
])
def testAreaUsesOriginNotDisplayedNodeName(source, event, expected):
    entry = StructuredLogEntry(1, "INFO", "页面设计", source=source,
                               eventType=event, nodeName="页面预览")
    assert entry.designArea() == expected


def testTabsCombineWithExistingFiltersAndResetWithoutDeletingHistory(designerApplication):
    dialog = LogDialog()
    entries = records()
    dialog.setEntries(entries)
    view = dialog.view
    view.areaTabs.setCurrentIndex(2)
    assert view._visibleEntries == entries[1:3]
    view.levelFilter.setCurrentText("WARN")
    view.searchInput.setText("超时")
    assert view._visibleEntries == entries[1:2]
    view.areaTabs.setCurrentIndex(1)
    assert view._visibleEntries == []
    assert view.levelFilter.currentText() == "WARN"
    assert view.searchInput.text() == "超时"
    assert view.showErrorsButton.isEnabled()
    view.showErrorsButton.click()
    assert view.areaTabs.currentIndex() == 0
    assert view._visibleEntries == entries
    assert view._detailEntry is entries[2]


def testRestoredAreaAppliesToLiveEntriesAndKeepsTreeSelection(designerApplication):
    entries = records()
    original = LogDialog()
    original.view.areaTabs.setCurrentIndex(2)
    dialog = LogDialog()
    view = dialog.view
    view.restoreSettings(original.view.settings())
    dialog.setEntries(entries)
    dialog.show()
    designerApplication.processEvents()
    assert view._visibleEntries == entries[1:3]
    view.table.setCurrentCell(0, 0)
    assert isinstance(view.detailViewer, QTreeWidget)
    root = view.detailViewer.topLevelItem(0)
    payload = next(root.child(index) for index in range(root.childCount())
                   if root.child(index).text(0) == "payload")
    payload.setExpanded(True)
    selected = payload.child(0)
    view.detailViewer.setCurrentItem(selected)
    view.appendEntry(replace(entries[0], timestampMs=1770000000005))
    view.appendEntry(replace(entries[1], timestampMs=1770000000006))
    view._applyPendingEntries()
    assert len(view._visibleEntries) == 3
    assert view.detailViewer.currentItem() is selected and payload.isExpanded()
    view.copyDetailsButton.click()
    assert json.loads(QApplication.clipboard().text()) == entries[1].detailPayload()
    view.clearView()
    assert view.detailViewer.topLevelItemCount() == 0
    assert not view.copyDetailsButton.isEnabled()


def testUnknownSavedCategoryFallsBackToAll():
    dialog = LogDialog()
    dialog.view.restoreSettings({"area": "removed-category"})
    dialog.setEntries(records())
    assert dialog.view.settings()["area"] == "all"
    assert len(dialog.view._visibleEntries) == 4


def testPageSourceAndAreaSurviveNativeSettingsRoundTrip(tmp_path):
    original = LogDialog()
    original.view.areaTabs.setCurrentIndex(2)
    original.view.sourceFilter.setCurrentText("页面设计")
    path = str(tmp_path / "log.ini")
    settings = QSettings(path, QSettings.IniFormat)
    settings.setValue("view", original.view.settings())
    settings.sync()
    restored = LogDialog()
    restored.view.restoreSettings(QSettings(path, QSettings.IniFormat).value("view"))
    restored.setEntries(records())
    assert restored.view._visibleEntries == records()[2:3]
    assert restored.view.settings()["source"] == "page_designer"
    assert restored.view.settings()["area"] == "page"
    assert restored.view.table.item(0, 2).text() == "页面设计"


def testNativeLogCategoryAndJsonTreeLayout(designerApplication):
    dialog = LogDialog()
    dialog.setWindowTitle("运行日志 · 界面验证（模拟数据，无设备连接）")
    dialog.resize(1100, 820)
    dialog.setEntries(records())
    dialog.show()
    view = dialog.view
    view.areaTabs.setCurrentIndex(2)
    view.table.setCurrentCell(0, 0)
    dialog.findChild(QSplitter).setSizes([180, 420])
    root = view.detailViewer.topLevelItem(0)
    payload = next(root.child(index) for index in range(root.childCount())
                   if root.child(index).text(0) == "payload")
    payload.setExpanded(True)
    exports = next(payload.child(index) for index in range(payload.childCount())
                   if payload.child(index).text(0) == "exports")
    exports.setExpanded(True)
    exports.child(0).setExpanded(True)
    view.detailViewer.setCurrentItem(payload)
    designerApplication.processEvents()
    view.detailViewer.scrollToItem(payload, QTreeWidget.PositionAtTop)
    assert view.areaTabs.width() <= view.width()
    assert view.detailViewer.columnCount() == 3
    assert view.table.rowCount() == 2
    output = os.environ.get("EMO_LOG_UI_ARTIFACTS")
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        assert dialog.grab().save(str(directory / "page-logs-json-tree.png"))
