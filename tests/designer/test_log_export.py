"""Export complete cached diagnostics without changing the live log view."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json

import pytest
from PySide2.QtWidgets import QFileDialog, QMessageBox

from emo_master.apps.designer.services import log_export
from emo_master.apps.designer.ui.log_dialog import LogDialog, StructuredLogEntry
from tests.designer.test_runtime_log_usability import Client, Settings


def entry(index=0, **changes):
    result = StructuredLogEntry(
        timestampMs=1770000000000 + index,
        level="ERROR", message=f"页面图片读取失败：相机 {index}", source="page_designer",
        eventType="presentation.client.image.failed", code="IMAGE_DECODE",
        projectId="内网工程", jobId="job-1", workflowId="capture",
        workflowName="相机采集", workflowRunId="workflow-run-1", nodeId="camera",
        nodeName="图像输入", nodeRunId="node-run-1", iterationPath=(2, 4),
        payload={"reason": "图像格式不正确", "nested": {"valid": False, "lastError": None},
                 "samples": [{"index": value, "text": f"完整记录 {value}"} for value in range(350)]},
    )
    return replace(result, **changes)


def chooseFile(monkeypatch, target, formatName="json", duringChoice=None):
    calls = []

    def choose(*args, **kwargs):
        calls.append((args, kwargs))
        if duringChoice is not None:
            duringChoice()
        filters = args[3] if len(args) > 3 else kwargs["filter"]
        selected = next(option for option in filters.split(";;") if f"*.{formatName})" in option)
        return str(target), selected

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    return calls


def jsonObjects(text):
    """Locate complete JSON objects in a human-readable TXT export."""
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
        except json.JSONDecodeError:
            continue
        yield value


@pytest.mark.parametrize("formatName", ["json", "jsonl", "txt"])
def testFormatsPreserveCompleteChineseNestedDiagnostics(designerApplication, monkeypatch, tmp_path, formatName):
    dialog = LogDialog()
    records = [entry(), entry(1, message="第二条错误\n保留换行")]
    expected = [record.detailPayload() for record in records]
    dialog.setEntries(records)
    view = dialog.view
    notices = []
    view.diagnosticCreated.connect(notices.append)
    target = tmp_path / f"内网日志.{formatName}"
    chooseFile(monkeypatch, target, formatName)

    view.exportLogs()

    text = target.read_text(encoding="utf-8")
    assert "完整记录 349" in text and "内网工程" in text
    if formatName == "json":
        document = json.loads(text)
        assert document["schemaVersion"] and document["exportedAt"]
        assert document["scope"] == "filtered"
        assert document["retainedCount"] == document["entryCount"] == 2
        assert document["filters"]["area"] == "all"
        assert document["entries"] == expected
    elif formatName == "jsonl":
        assert [json.loads(line) for line in text.splitlines()] == expected
    else:
        decoded = list(jsonObjects(text))
        assert all(record in decoded for record in expected)
    assert [(notice["level"], notice["code"]) for notice in notices] == [
        ("INFO", "DESIGNER_LOG_EXPORTED")]
    assert view._entries == records


def testFilteredExportIncludesPendingRowsAndCombinesAreaLevelAndSearch(
        designerApplication, monkeypatch, tmp_path):
    dialog = LogDialog()
    records = [entry(), entry(1, source="flow_designer", eventType="node.failed"),
               entry(2, level="WARN"), entry(3, message="连接失败")]
    dialog.setEntries(records)
    dialog.show()
    designerApplication.processEvents()
    view = dialog.view
    view.areaTabs.setCurrentIndex(2)
    view.levelFilter.setCurrentText("ERROR")
    view.searchInput.setText("图片")
    view.autoScrollCheck.setChecked(False)
    settings = view.settings()
    fresh = entry(4)
    view.appendEntry(fresh)
    assert fresh in view._pendingEntries and fresh not in view._visibleEntries
    target = tmp_path / "筛选日志.json"
    chooseFile(monkeypatch, target)

    view.exportLogs(filtered=True)

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["entries"] == [records[0].detailPayload(), fresh.detailPayload()]
    assert document["retainedCount"] == 5 and document["entryCount"] == 2
    assert document["filters"]["area"] == "page"
    assert document["filters"]["level"] == "ERROR"
    assert document["filters"]["search"] == "图片"
    assert view.settings() == settings


def testSaveDialogChangesCannotReplaceOrMutateTheCapturedSnapshot(designerApplication, monkeypatch, tmp_path):
    dialog = LogDialog()
    original = entry()
    dialog.setEntries([original])
    view = dialog.view
    view.areaTabs.setCurrentIndex(2)
    expected = deepcopy(original.detailPayload())
    target = tmp_path / "快照.json"

    def changeWhileChoosing():
        original.payload["nested"]["lastError"] = "选择文件期间发生的新变化"
        view.appendEntry(entry(1))
        view.clearView()
        view.areaTabs.setCurrentIndex(1)

    chooseFile(monkeypatch, target, duringChoice=changeWhileChoosing)

    view.exportLogs(filtered=True)

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["entries"] == [expected]
    assert document["retainedCount"] == document["entryCount"] == 1
    assert document["filters"]["area"] == "page"
    assert view._entries == [] and view.areaTabs.currentIndex() == 1


def testExportAllMenuUsesCurrentRetentionAndIgnoresFilters(designerApplication, monkeypatch, tmp_path):
    dialog = LogDialog(maximumEntries=2)
    records = [entry(index) for index in range(4)]
    dialog.setEntries(records[:3])
    view = dialog.view
    view.appendEntry(records[3])
    view.areaTabs.setCurrentIndex(1)
    view.searchInput.setText("不会命中任何记录")
    target = tmp_path / "全部缓存.json"
    chooseFile(monkeypatch, target)
    assert view.exportButton.menu() is not None
    assert view.exportFilteredAction in view.exportButton.menu().actions()
    assert view.exportAllAction in view.exportButton.menu().actions()

    view.exportAllAction.trigger()

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["scope"] == "all_cached"
    assert document["entries"] == [record.detailPayload() for record in records[-2:]]
    assert document["retainedCount"] == document["entryCount"] == 2
    assert view.searchInput.text() == "不会命中任何记录"
    assert view.areaTabs.currentIndex() == 1


def testEmptyFilterShowsFeedbackWithoutSaveDialogOrDiagnostic(designerApplication, monkeypatch):
    dialog = LogDialog()
    dialog.setEntries([entry()])
    view = dialog.view
    view.searchInput.setText("不存在的错误码")
    notices = []
    view.diagnosticCreated.connect(notices.append)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: pytest.fail("empty export opened a dialog"))

    view.exportLogs(filtered=True)

    assert "没有可导出" in view.navigationHint.text()
    assert notices == [] and len(view._entries) == 1


def testCancelIsQuietAndPreservesExistingFiles(designerApplication, monkeypatch, tmp_path):
    dialog = LogDialog()
    dialog.setEntries([entry()])
    view = dialog.view
    notices = []
    view.diagnosticCreated.connect(notices.append)
    target = tmp_path / "已有日志.json"
    target.write_bytes(b"previous log export")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("", ""))

    view.exportFilteredAction.trigger()

    assert target.read_bytes() == b"previous log export"
    assert list(tmp_path.iterdir()) == [target]
    assert notices == []


@pytest.mark.parametrize("formatName", ["json", "jsonl", "txt"])
def testSaveFailurePreservesPreviousExportAndCleansTemporaryFile(monkeypatch, tmp_path, formatName):
    target = tmp_path / f"原有日志.{formatName}"
    target.write_bytes(b"previous complete export")

    def blockedReplace(_source, destination):
        assert Path(destination) == target
        raise PermissionError("导出文件正在被其他程序使用")

    monkeypatch.setattr(log_export.os, "replace", blockedReplace)

    with pytest.raises(PermissionError, match="其他程序"):
        log_export.exportLogs(target, [entry().detailPayload()], formatName=formatName,
                              scope="all_cached", filters={}, retainedCount=1)

    assert target.read_bytes() == b"previous complete export"
    assert list(tmp_path.iterdir()) == [target]


def testAddedDefaultSuffixCannotOverwriteRejectedTarget(designerApplication, monkeypatch, tmp_path):
    dialog = LogDialog()
    dialog.setEntries([entry()])
    view = dialog.view
    notices = []
    view.diagnosticCreated.connect(notices.append)
    target = tmp_path / "已有导出.json"
    target.write_bytes(b"keep existing export")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(target.with_suffix("")), ""))
    confirmations = []

    def reject(*args, **_kwargs):
        confirmations.append(args)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", reject)

    view.exportLogs()

    assert len(confirmations) == 1
    assert target.read_bytes() == b"keep existing export"
    assert list(tmp_path.iterdir()) == [target]
    assert notices == []


def testExportFailureEntersMainWindowAndDurableLocalLog(ownedDesignerWindow, monkeypatch, tmp_path):
    directory = tmp_path / "designer-logs"
    monkeypatch.setenv("EMO_DESIGNER_LOG_DIR", str(directory))
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.appendLocalEvent(entry().detailPayload(), source="page_designer")
    blocked = tmp_path / "parent-is-file"
    blocked.write_bytes(b"keep this file")
    target = blocked / "日志.json"
    chooseFile(monkeypatch, target)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args, **_kwargs: QMessageBox.Ok)

    window.logDialog.view.exportLogs(filtered=False)

    failures = [record for record in window.logEntries if record.code == "DESIGNER_LOG_EXPORT_FAILED"]
    assert len(failures) == 1 and failures[0].level == "ERROR"
    assert failures[0].payload["path"] == str(target)
    assert failures[0].payload["reason"]
    assert blocked.read_bytes() == b"keep this file"
    writer = window._logResources["writer"]
    assert writer.waitUntilProcessed(writer.sessionId, writer.sequence)
    persisted = [json.loads(line) for path in directory.glob("designer-*.jsonl")
                 for line in path.read_text(encoding="utf-8").splitlines()]
    saved = [record for record in persisted if record["code"] == "DESIGNER_LOG_EXPORT_FAILED"]
    assert len(saved) == 1 and saved[0]["level"] == "ERROR"
    assert saved[0]["payload"] == failures[0].payload
