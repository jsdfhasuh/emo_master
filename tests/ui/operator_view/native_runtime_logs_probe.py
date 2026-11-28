"""Run the Runtime R4 log checks on the Windows QWidget platform."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import emo_master  # noqa: F401 - DLL bootstrap
from PySide2 import __version__ as qtVersion
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QAbstractItemView, QApplication, QFileDialog

from emo_master.apps.operator_runtime.log_reader import LogRecord, MAX_RECORDS, ReadCursor, ReadResult
from emo_master.apps.operator_runtime.main import OperatorWindow
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_view import waitFor


def combinedFilters(window, directory, shot):
    view, feed = window.logView, window.logFeed
    deviceId = "tcp-inspection-gateway-probe"
    path = feed.directory / "runtime-r4-synthetic.jsonl"
    records = []
    for index in range(24):
        channel = index % 3 + 1
        stamp = 1795831000123 + index * 1000
        records.append(dict(
            timestamp=datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds"),
            timestampMs=stamp, level="WARN" if index % 2 == 0 else "INFO",
            message=f"工位 {index:02d} 心跳检查 · 通道 {channel} 仍在等待回复",
            component="communication", eventType="device.channel.probe", jobId="synthetic-cycle",
            payload={"deviceId": deviceId, "channel": channel, "expected": "ACK", "password": "redaction-probe"},
        ))
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
    QTest.mouseClick(window.logsButton, Qt.LeftButton)
    assert view.sources.currentIndex() == 0
    QTest.mouseClick(view.sources, Qt.LeftButton, pos=view.sources.tabRect(2).center())
    assert view.sources.currentIndex() == 2
    waitFor(lambda: view.fileCombo.findData(str(path)) > 0)
    view.fileCombo.setCurrentIndex(view.fileCombo.findData(str(path)))
    waitFor(lambda: view.model.rowCount() == 24 and feed.worker is None)
    view.levelCombo.setCurrentIndex(view.levelCombo.findData("WARN"))
    view.componentCombo.setCurrentIndex(view.componentCombo.findData("communication"))
    QTest.keyClicks(view.keywordEdit, "synthetic-cycle")
    view.setDevice(deviceId, "工位 TCP 网关（模拟）")
    waitFor(lambda: not view.keywordTimer.isActive() and view.model.rowCount() == 12)
    assert view.deviceLabel.toolTip() == deviceId
    view.table.selectRow(7)
    view.table.scrollTo(view.model.index(7, 0), QAbstractItemView.PositionAtCenter)
    assert view.copyDetailButton.isEnabled() and view.copyRawButton.isEnabled()
    assert "redaction-probe" not in view.raw.toPlainText() and "<redacted>" in view.raw.toPlainText()
    shot(window, "logs-combined-filters")

    initial = window.size()
    view.rawToggle.setChecked(True)
    window.resize(min(820, initial.width()), 440)
    QApplication.processEvents()
    assert window.height() == 440 and window.topBar.isVisible()
    assert view.scrollArea.verticalScrollBar().maximum() > 0
    view.scrollArea.ensureWidgetVisible(view.folderButton)
    visible = view.folderButton.mapTo(view.scrollArea.viewport(), view.folderButton.rect().center())
    assert view.scrollArea.viewport().rect().contains(visible)
    shot(window, "logs-narrow-scroll-to-tools")
    view.rawToggle.setChecked(False)
    window.resize(initial)
    view.scrollArea.verticalScrollBar().setValue(0)

    scopes = (view.exportCurrentAction, view.exportFileAction, view.exportFilesAction)
    for index, action in enumerate(scopes):
        target = directory / f"native-export-{index}.json"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "JSON (*.json)")):
            action.trigger()
        waitFor(lambda: "已导出 12 条" in feed.exportMessage and target.is_file())
        content = json.loads(target.read_text(encoding="utf-8"))
        assert content["entryCount"] == 12 and content["filters"]["keywordApplied"]
        assert all(row["payload"]["password"] == "<redacted>" for row in content["entries"])
        assert [row["timestamp"] for row in content["entries"]] == [record["timestamp"] for record in records[::2]]
    selected = view.model.records[view.table.currentIndex().row()]
    QTest.mouseClick(window.settingsButton, Qt.LeftButton)
    assert window.contentStack.currentWidget() is window.startupPage
    QTest.mouseClick(window.logsButton, Qt.LeftButton)
    assert window.contentStack.currentWidget() is view and view.sources.currentIndex() == 0
    QTest.mouseClick(view.sources, Qt.LeftButton, pos=view.sources.tabRect(2).center())
    assert view.sources.currentIndex() == 2 and view.deviceId == deviceId
    assert view.model.records[view.table.currentIndex().row()] is selected
    QTest.mouseClick(window.settingsButton, Qt.LeftButton)
    window.startupPage.ensureWidgetVisible(window.startupPage.allLogsButton)
    QTest.mouseClick(window.startupPage.allLogsButton, Qt.LeftButton)
    assert view.sources.currentIndex() == 0
    assert window.contentStack.currentWidget() is view and view.model.rowCount() == 24
    assert not view.deviceId and not view.keywordEdit.text()
    assert view.componentCombo.currentIndex() == view.levelCombo.currentIndex() == 0
    assert feed.historyPath == path and view.fileCombo.currentData() == str(path)
    return path


def capAnchors(window, path, shot):
    view, feed = window.logView, window.logFeed
    QTest.mouseClick(view.sources, Qt.LeftButton, pos=view.sources.tabRect(2).center())
    assert view.sources.currentIndex() == 2
    feed.setVisible(False)
    waitFor(lambda: feed.worker is None)

    def append(start, count, *, reset=False):
        # Inject a bounded reader result to isolate the on-screen update from disk timing.
        rows = tuple(LogRecord(path, index, dict(
            timestampMs=1795831000123 + index, level="INFO", originApp="runtime",
            component="workflow", eventType="operator.workflow.probe",
            message=f"循环 {index:05d} · 合成追加记录 · 工位检测完成",
        )) for index in range(start, start + count))
        feed._accept((feed.generation, path, (path,), ReadResult(ReadCursor(path, start + count), rows, reset), ""))
        QApplication.processEvents()

    def top():
        row = view.table.rowAt(0)
        return view.model.records[row].identity, view.table.rowViewportPosition(row)

    view.followLatest.setChecked(False)
    append(0, MAX_RECORDS - 50, reset=True)
    view.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.table.selectRow(4003)
    view.table.scrollTo(view.model.index(4000, 0), QAbstractItemView.PositionAtTop)
    vertical, horizontal = view.table.verticalScrollBar(), view.table.horizontalScrollBar()
    vertical.setValue(vertical.value() + 8)
    selection, anchor = view.model.records[view.table.currentIndex().row()], top()
    assert anchor == ((str(path), 4000), -8)
    append(MAX_RECORDS - 50, 100)
    assert len(feed.records) == MAX_RECORDS and feed.records[0].offset == 50
    assert top() == anchor and view.model.records[view.table.currentIndex().row()] is selection

    shot(window, "logs-cap-reading-after-eviction")
    width = view.table.columnWidth(0)
    view.table.setColumnWidth(0, max(1000, view.width()))
    QApplication.processEvents()
    horizontal.setValue(horizontal.maximum())
    assert horizontal.maximum() > 0
    before = horizontal.value()
    append(MAX_RECORDS + 50, 100)
    assert horizontal.value() == before and top() == anchor
    QTest.mouseClick(view.followLatest, Qt.LeftButton)
    assert vertical.value() == vertical.maximum() and horizontal.value() == before
    view.table.setColumnWidth(0, width)
    horizontal.setValue(0)
    shot(window, "logs-cap-following-latest")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assert os.environ.get("QT_QPA_PLATFORM") == "windows"
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication([])
    screen = app.primaryScreen()
    bounds = screen.availableGeometry()
    evidence = dict(
        head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        workingTreeChanges=bool(subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=no"], text=True).strip()),
        python=sys.version.split()[0], pyside2=qtVersion, platform=app.platformName(),
        qtScaleFactor=os.environ.get("QT_SCALE_FACTOR"), screenPixelRatio=screen.devicePixelRatio(),
        screenLogicalDpi=screen.logicalDotsPerInch(),
        screenAvailable=[bounds.width(), bounds.height()], screenshots=[],
    )

    def shot(window, name):
        app.processEvents()
        image = window.grab()
        file = args.output / f"{name}.png"
        assert image.save(str(file))
        evidence["screenshots"].append(dict(file=file.name, logicalSize=[window.width(), window.height()],
                                           imageSize=[image.width(), image.height()]))

    with tempfile.TemporaryDirectory(prefix="emo-native-r4-logs-") as directory:
        root = Path(directory)
        window = OperatorWindow(dataRoot=root / "data", preferences=preferences(root))
        window.resize(max(500, min(960, bounds.width() - 40)), max(460, min(740, bounds.height() - 80)))
        window.show()
        try:
            waitFor(lambda: window.controller is not None and not window.busy)
            assert not window.error
            path = combinedFilters(window, root, shot)
            capAnchors(window, path, shot)
        finally:
            closeWindow(window)
    evidence["checks"] = dict(
        E="合成 JSONL，四条件、原始脱敏详情／可复制状态、多范围 JSON 实读导出、往返保留、起始页恢复全部",
        F="合成有界读取结果，10,000 条窗口，淘汰后像素锚点、选择、水平位置与跟随最新",
        narrow="440 逻辑像素高；原始 JSON 展开，顶栏可见且日志区域可滚动到底部操作",
    )
    evidence["result"] = "PASS"
    (args.output / "logs-result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
