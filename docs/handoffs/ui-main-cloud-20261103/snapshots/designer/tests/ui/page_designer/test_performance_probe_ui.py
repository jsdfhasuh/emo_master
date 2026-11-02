import json
import sys

import pytest
from PySide2.QtCore import QEvent, QEventLoop, QPoint, Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from test_workspace import designer  # noqa: F401
from test_responsive_editor import responsivePage, settle  # noqa: F401
from test_responsive_resize_edges import prepare
from emo_master.apps.designer.ui import performance_probe


def waitEvents(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec_()


@pytest.fixture
def probe(designer, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(performance_probe, 'sourceIdentity', lambda: {'fixture': 'synthetic'})
    controller = designer.pageCoordinator.chrome.performanceProbe
    controller.outputRoot = tmp_path / 'reports'
    controller.show()
    yield controller
    controller.shutdown()


def testSharedMenuAndCaptureDoNotChangeProject(designer, probe):  # noqa: F811
    c = designer.pageCoordinator
    action = c.chrome.performanceProbeAction
    assert action in c.chrome.menus['视图'].actions()
    assert action.isVisible() and action.isEnabled()
    before, history = c.session.payload(), len(c.session._undo)
    probe.start()
    probe.dialog.close()  # Keep manual canvas use possible, with bounded auto-stop.
    assert probe.running and probe.limitTimer.isActive()
    QApplication.sendEvent(designer, QEvent(QEvent.LayoutRequest))
    probe.mark()
    waitEvents(60)
    probe.stop()
    assert c.session.payload() == before and len(c.session._undo) == history
    report = json.loads((probe.destination / 'report.json').read_text(encoding='utf-8'))
    assert report['heartbeat']['samples'] > 0 and len(report['marks']) == 1
    assert report['events']['flow.layout'] > 0
    assert report['functions']
    assert not probe.beatTimer.isActive() and not probe.statusTimer.isActive() and not probe.limitTimer.isActive()
    assert not probe.recorder._sampler.is_alive() and sys.getprofile() is None
    oldEvents = dict(probe.recorder.events)
    QApplication.sendEvent(designer, QEvent(QEvent.LayoutRequest))
    assert dict(probe.recorder.events) == oldEvents
    c.showPages()
    assert action.isVisible() and action.isEnabled()
    c.showFlow()
    assert action.isVisible() and action.isEnabled()


@pytest.mark.parametrize('detailed', [False, True])
def testPageNativeResizeAndUndoWhileRecording(responsivePage, probe, detailed):  # noqa: F811
    c, editor, keys = responsivePage
    probe.detailed.setChecked(detailed)
    probe.start()
    probe.dialog.hide()
    before, history = editor.store.snapshot(), len(c.session._undo)
    window, start, _card, _local = prepare(editor, keys[0], .75, (1, 0))
    end = start + QPoint(-30, 0)
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
    assert editor.tools.responsive.resize is not None
    QTest.mouseMove(window, end)
    settle()
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
    settle()
    assert len(c.session._undo) == history + 1
    assert editor.store.snapshot() != before
    c.history()
    assert editor.store.snapshot() == before
    editor.refresh()
    settle()
    # Events inside the proxy widget belong to the capture, despite its separate parent chain.
    QApplication.sendEvent(editor.renderer, QEvent(QEvent.LayoutRequest))
    # The probe's own UI is excluded from the event counts.
    oldEvents = dict(probe.recorder.events)
    QApplication.sendEvent(probe.dialog, QEvent(QEvent.LayoutRequest))
    assert dict(probe.recorder.events) == oldEvents
    probe.markAction.trigger()
    probe.stop()
    report = probe.recorder.report()
    assert report['metadata']['finalView']['page']['layout'] == 'responsive'
    assert report['events']['page.layout'] and report['events']['page.mousePress']
    assert len(report['marks']) == 1
    if detailed:
        assert any(row['file'].endswith('responsive_tools.py') for row in report['functions'])
        assert any(row['area'] == '页面刷新与切换' for row in report['areas'])
    else:
        assert not report['functions']


def testAutoStopRepeatedCaptureAndShutdown(probe):
    probe.detailed.setChecked(False)
    probe.start()
    first = probe.recorder
    probe.limitTimer.start(30)
    waitEvents(100)
    assert not probe.running and probe.destination.is_dir()
    probe.start()
    assert probe.running and probe.recorder is not first
    probe.c.shutdown()
    assert not probe.running and probe.destination.is_dir()
    assert not probe.recorder._sampler.is_alive()


def testExportFailureCanRetryWithoutLosingCapture(probe, tmp_path):
    blocked = tmp_path / 'file-not-directory'
    blocked.write_text('keep', encoding='utf-8')
    probe.outputRoot = blocked
    probe.start()
    probe.mark()
    probe.stop()
    assert '记录仍保留在内存' in probe.status.text()
    assert probe.exportButton.isEnabled() and probe.folderButton.isEnabled()
    captured = probe.recorder.report()
    probe.outputRoot = tmp_path / 'retry'
    probe.export()
    assert probe.destination.is_dir() and probe.startButton.isEnabled()
    assert probe.recorder.report() == captured and blocked.read_text(encoding='utf-8') == 'keep'
