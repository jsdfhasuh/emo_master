"""Isolated Windows QWidget/QTest visual probe (not a frozen/device acceptance)."""
import argparse
import json
import os
from pathlib import Path
import tempfile

import emo_master  # noqa: F401 - DLL bootstrap
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from emo_master.apps.operator_runtime.main import OperatorWindow
from tests.runtime.current_production_fixture import currentProductionProject
from tests.ui.operator_view.test_production_window import closeWindow, preferences
from tests.ui.operator_view.test_runtime_log_view import waitMs
from tests.ui.operator_view.test_view import waitFor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("QT_QPA_PLATFORM") == "windows"
    args.output.mkdir(parents=True, exist_ok=True)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication.instance() or QApplication([])
    evidence = dict(platform=app.platformName(), scale=os.environ.get("QT_SCALE_FACTOR"), checks=[], screens=[])

    def shot(window, name):
        app.processEvents()
        path = args.output / (name + ".png")
        assert window.grab().save(str(path))
        evidence["screens"].append(str(path))

    with tempfile.TemporaryDirectory(prefix="emo-runtime-native-") as directory:
        root = Path(directory)
        longRoot = root / ("内部资料 目录_" + "长路径" * 24)
        window = OperatorWindow(dataRoot=longRoot, preferences=preferences(root))
        window.show()
        try:
            waitFor(lambda: window.controller and not window.busy)
            assert not window.error and not window.controller.runtime.jobRepository.all()
            for width, height in ((1060, 800), (900, 700)):
                window.resize(width, height)
                waitMs(180)
                assert window.width() == width and window.height() == height
                panel = window.startupPage.storage
                assert panel.saveButton.isEnabled()
                shot(window, f"home-{width}x{height}")
            QTest.mouseClick(panel.detailsButton, Qt.LeftButton)
            waitMs(100)  # Let the expanded document update its scroll geometry.
            window.startupPage.ensureWidgetVisible(panel.nextEdit)
            waitMs(100)
            position = panel.nextEdit.mapTo(window.startupPage.viewport(), panel.nextEdit.rect().center())
            assert window.startupPage.viewport().rect().contains(position)
            QTest.mouseClick(panel.nextEdit, Qt.LeftButton)
            panel.nextEdit.setText(str(root / "未保存 下次目录"))
            assert panel.nextEdit.hasFocus() and "未保存" in panel.feedback.text()
            shot(window, "settings-expanded-long-path")
            window._diagnostic("operator.project.load.failed", "合成读取故障：路径包含中文与空格；" + "长原因 " * 80,
                               level="ERROR", payload={"phase": "synthetic native probe", "diagnostics": {"code": "E_SYNTHETIC_NATIVE"}})
            QTest.mouseClick(window.logsButton, Qt.LeftButton)
            waitFor(lambda: bool(window.logFeed.records) and window.logView.table.rowCount() > 0)
            window.logView.levelCombo.setCurrentIndex(1)
            waitFor(lambda: window.logView.table.rowCount() > 0)
            window.logView.table.selectRow(window.logView.table.rowCount() - 1)
            window.logView.rawToggle.setChecked(True)
            assert "E_SYNTHETIC_NATIVE" in window.logView.detail.toPlainText()
            shot(window, "logs-error-details")
            QTest.mouseClick(window.backButton, Qt.LeftButton)
            assert window.contentStack.currentWidget() is window.startupPage
            assert panel.nextEdit.text() == str(root / "未保存 下次目录")
            assert "未保存" in panel.feedback.text()
            assert panel.nextEdit.hasFocus()
            evidence["checks"].extend(["home-default-and-narrow", "long-path-settings-draft", "log-error-and-raw",
                                       "back-preserves-draft", "keyboard-focus"])
        finally:
            closeWindow(window)
        assert window.logFeed.idle and window.controller.closed

        # Actual production entry, current 2.7, real local image and DisplaySession; no devices.
        project, _document = currentProductionProject(root / "project", mode="continuous", autoStart=True)
        running = OperatorWindow(project, dataRoot=root / "running-data", preferences=preferences(root / "running-prefs"))
        running.show()
        try:
            waitFor(lambda: running.pages and running.pages.displayed or running.error)
            assert not running.error
            waitFor(lambda: not running.busy and running.state["state"] == "RUNNING")
            job, pages, session = running.controller.jobId, running.pages, running.session
            QTest.mouseClick(running.logsButton, Qt.LeftButton)
            waitFor(lambda: running.logView.table.rowCount() > 0)
            shot(running, "running-logs-stop-visible")
            QTest.mouseClick(running.backButton, Qt.LeftButton)
            assert running.contentStack.currentWidget() is running.businessHost
            assert (running.controller.jobId, running.pages, running.session) == (job, pages, session)
            assert running.stopButton.isEnabled() and not session.stop.is_set()
            shot(running, "returned-running-image")
            evidence["checks"].extend(["continuous-running-tools-preserve-job-page-session", "stop-remains-visible"])
        finally:
            closeWindow(running)

        target = root / "blocked-target"
        target.write_text("ordinary path collision")
        failed = OperatorWindow(dataRoot=target, preferences=preferences(root / "failure-prefs"))
        failed.show()
        try:
            waitFor(lambda: failed.initializationFailed and not failed.busy)
            assert failed.startupPage.retryButton.isVisible()
            shot(failed, "initialization-failed")
            QTest.mouseClick(failed.logsButton, Qt.LeftButton)
            assert "未写入日志文件" in failed.logView.diagnostics.toPlainText()
            shot(failed, "failure-window-only-diagnostics")
            evidence["checks"].append("initialization-failure-still-has-tools-and-unwritten-reason")
        finally:
            closeWindow(failed)
    evidence["result"] = "PASS"
    (args.output / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
