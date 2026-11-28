"""Isolated Windows QWidget/QTest visual probe (not a frozen/device acceptance)."""
import argparse
import json
import os
from pathlib import Path
import tempfile

import emo_master  # noqa: F401 - DLL bootstrap
from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QInputDialog

from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.core.project.models import ProjectDocument
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
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    screen = app.primaryScreen()
    area = screen.availableGeometry()
    evidence = dict(platform=app.platformName(), scale=os.environ.get("QT_SCALE_FACTOR"),
                    devicePixelRatio=screen.devicePixelRatio(), screenGeometry=screen.geometry().getRect(),
                    availableGeometry=area.getRect(), checks=[], screens=[])

    def shot(window, name):
        app.processEvents()
        path = args.output / (name + ".png")
        assert window.grab().save(str(path))
        evidence["screens"].append(str(path))

    with tempfile.TemporaryDirectory(prefix="emo-runtime-native-") as directory:
        root = Path(directory).resolve()
        longRoot = root / ("内部资料 目录_" + "长路径" * 12)
        recent, _document = currentProductionProject(root / '试验工程')
        missing = root / '曾经的工程.emoproj'
        prefs = preferences(root)
        prefs.setValue('recentProjects', json.dumps([str(missing), str(recent)]))
        window = OperatorWindow(dataRoot=longRoot, preferences=prefs)
        window.show()
        try:
            waitFor(lambda: window.controller and not window.busy)
            assert not window.error and not window.controller.runtime.jobRepository.all()
            for width, height in ((1060, min(800, area.height() - 75)), (700, min(620, area.height() - 75))):
                window.resize(width, height)
                waitMs(180)
                assert (window.width(), window.height()) == (width, height), (window.size(), window.minimumSize())
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
            tabs = window.logView.sources
            QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(2).center())
            assert tabs.currentIndex() == 2
            waitFor(lambda: window.logFeed.activePath is not None
                    and window.logView.fileCombo.findData(str(window.logFeed.activePath)) > 0
                    and any('E_SYNTHETIC_NATIVE' in str(row.data) for row in window.logFeed.records))
            active = window.logFeed.activePath
            index = window.logView.fileCombo.findData(str(active))
            assert index > 0
            window.logView.fileCombo.setCurrentIndex(index)
            waitFor(lambda: bool(window.logFeed.records) and window.logFeed.worker is None)
            assert window.logView.statusLabel.text().startswith("本次活动文件（手动固定，不跟随轮转）")
            assert window.startupPage.recentLogs.toPlainText().startswith("本次活动文件（手动固定，不跟随轮转）")
            window.logView.levelCombo.setCurrentIndex(1)
            waitFor(lambda: window.logView.model.rowCount() > 0)
            window.logView.table.selectRow(window.logView.model.rowCount() - 1)
            window.logView.rawToggle.setChecked(True)
            assert "E_SYNTHETIC_NATIVE" in window.logView.detail.toPlainText()
            shot(window, "logs-error-details")
            QTest.mouseClick(window.backButton, Qt.LeftButton)
            assert window.contentStack.currentWidget() is window.startupPage
            assert panel.nextEdit.text() == str(root / "未保存 下次目录")
            assert "未保存" in panel.feedback.text()
            assert panel.nextEdit.hasFocus()
            evidence["checks"].extend(["home-default-and-narrow", "long-path-settings-draft", "log-error-and-raw",
                                       "manual-active-log-label", "back-preserves-draft", "keyboard-focus"])
            for index in (0, 1):
                button = window.startupPage.recentOpenButtons[index]
                window.startupPage.ensureWidgetVisible(button)
                app.processEvents()
                QTest.mouseClick(button, Qt.LeftButton)
                if not index:
                    waitFor(lambda: window.error and not window.busy)
                    assert prefs.value('projectPath') is None and window.openAction.isEnabled()
                    QTest.mouseClick(window.copyErrorButton, Qt.LeftButton)
                    assert app.clipboard().text() == window.error
                    shot(window, 'recent-missing-project')
                else:
                    previousError = window.error
                    waitFor(lambda: not window.busy and (window.pages is not None or window.error != previousError))
                    assert window.pages is not None and not window.error, window.error
                    assert Path(prefs.value('projectPath')) == recent.resolve()
                    assert Path(window.startupPage.recentPaths[0]) == recent.resolve()
                    assert not window.controller.runtime.jobRepository.all()
            evidence['checks'].append('recent-failure-keeps-recovery-then-valid-project-loads')
        finally:
            closeWindow(window)
        assert window.logFeed.idle and window.controller.closed

        # Current 2.10/1.3 project, local image and actual DisplaySession.
        project, document = currentProductionProject(root / 'project', mode='continuous', autoStart=True)
        payload = document.model_dump(mode='json')
        payload['project']['name'] = '产线项目名称长文本 ' * 14
        label = '工程配置的停止检测按钮'
        shell = payload['presentation']['shell'] = dict(showControls=True,
            stopControl=dict(label=label, appearance=dict(minWidth=280, showIcon=True, iconSize=25,
                customStyle={'qtQss': {'enabled': True, 'code': 'QPushButton { background:#AF3150; max-width:280px; }'}})))
        for visible in (True, False):
            shell['showControls'] = visible
            saveProject(project, ProjectDocument.model_validate(payload).model_dump(mode='json'))
            runtimePrefs = preferences(root / ('visible-prefs' if visible else 'hidden-prefs'))
            running = OperatorWindow(project, dataRoot=root / ('visible-data' if visible else 'hidden-data'),
                                     preferences=runtimePrefs)
            running.resize(700, min(620, area.height() - 75))
            running.show()
            originalDialog = QInputDialog.getItem
            try:
                waitFor(lambda: running.pages and running.pages.displayed or running.error)
                assert not running.error
                waitFor(lambda: not running.busy and running.state['state'] == 'RUNNING')
                job, pages, session, hub = running.controller.jobId, running.pages, running.session, running.hub
                review = pages.historyReviewState()
                assert running.stopButton.isVisible() is visible and running.stopButton.text() == label
                QTest.mouseClick(running.logsButton, Qt.LeftButton)
                tabs = running.logView.sources
                QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(2).center())
                assert tabs.currentIndex() == 2
                waitFor(lambda: running.logView.model.rowCount() > 0)
                assert running.stopButton.isVisible() and running.stopButton.isEnabled()
                assert running.stopButton.text() == '停止' and '#AF3150' not in running.stopButton.styleSheet()
                for button in (running.stopButton, running.backButton):
                    assert running.rect().contains(button.mapTo(running, button.rect().bottomRight()))
                assert payload['project']['name'] in running.projectLabel.toolTip()
                shot(running, 'running-logs-custom' if visible else 'running-logs-hidden-controls')
                QTest.mouseClick(running.settingsButton, Qt.LeftButton)
                if visible:
                    network = running.startupPage.network
                    runtime, server = running.controller.runtime, running.controller.server
                    current = network.currentLabel.text()
                    network.subscriptions.setValue(network.subscriptions.value() + 1)
                    assert '未保存' in network.feedback.text()
                    running.startupPage.ensureWidgetVisible(network.saveButton)
                    app.processEvents()
                    QTest.mouseClick(network.saveButton, Qt.LeftButton)
                    assert '已保存' in network.feedback.text() and network.currentLabel.text() == current
                    assert (running.controller.runtime, running.controller.server, running.controller.jobId) == (runtime, server, job)
                    shot(running, 'running-listener-next-start-saved')
                for target in (running.logView, running.businessHost):
                    QTest.mouseClick(running.backButton, Qt.LeftButton)
                    assert running.contentStack.currentWidget() is target
                assert (running.controller.jobId, running.pages, running.session, running.hub) == (job, pages, session, hub)
                assert pages.historyReviewState() == review and not session.stop.is_set()
                assert running.stopButton.isVisible() is visible and running.stopButton.text() == label
                assert running.stopButton.iconSize().width() == 25 and '#AF3150' in running.stopButton.styleSheet()
                if visible:
                    shot(running, 'returned-authored-running-page')
                QTest.mouseClick(running.logsButton, Qt.LeftButton)
                tabs = running.logView.sources
                QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(2).center())
                assert tabs.currentIndex() == 2

                def unexpectedDialog(*_args):
                    raise AssertionError('The single active Job should stop directly')

                QInputDialog.getItem = unexpectedDialog
                QTest.mouseClick(running.stopButton, Qt.LeftButton)
                waitFor(lambda: not running.busy and running.state['canLoad'])
                assert running.controller.runtime.jobRepository.get(job).isTerminal
                assert len(running.controller.runtime.jobRepository.all()) == 1
                if visible:
                    shot(running, 'software-stop-from-logs-complete')
                    normal = running.normalGeometry()
                    running.showMaximized()
                    waitFor(running.isMaximized)
            finally:
                QInputDialog.getItem = staticmethod(originalDialog)
                closeWindow(running)
            if visible:
                reopened = OperatorWindow(dataRoot=root / 'reopened-data', preferences=runtimePrefs)
                reopened.show()
                try:
                    assert reopened.isMaximized()
                    reopened.showNormal()
                    waitFor(lambda: not reopened.isMaximized())
                    assert reopened.normalGeometry() == normal
                    shot(reopened, 'reopened-normal-window')
                finally:
                    closeWindow(reopened)
        evidence['checks'].extend(['tools-preserve-job-page-session-hub', 'single-job-stops-from-logs',
                                   'narrow-dpi-hidden-custom-stop', 'listener-setting-save-during-run',
                                   'window-normal-geometry-and-maximized-restored'])

        target = root / "blocked-target"
        target.write_text("ordinary path collision")
        failed = OperatorWindow(dataRoot=target, preferences=preferences(root / "failure-prefs"))
        failed.show()
        try:
            waitFor(lambda: failed.initializationFailed and not failed.busy)
            assert failed.startupPage.retryButton.isVisible()
            shot(failed, "initialization-failed")
            QTest.mouseClick(failed.logsButton, Qt.LeftButton)
            tabs = failed.logView.sources
            QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(2).center())
            assert tabs.currentIndex() == 2
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
