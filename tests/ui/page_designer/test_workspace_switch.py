"""Pointer/keyboard workspace choices follow accepted state and retire animation."""
from PySide2.QtCore import Qt, QAbstractAnimation, QCoreApplication, QEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QAction, QActionGroup, QApplication, QMessageBox
from shiboken2 import isValid

from emo_master.apps.designer.page_designer.workspace_switch import WorkspaceSwitch
from test_workspace import designer, Client  # noqa: F401
from test_pending_inputs import setupPage


def standalone():
    actions = [QAction('流程设计'), QAction('页面设计')]
    group = QActionGroup(QApplication.instance())
    for action in actions:
        action.setCheckable(True)
        group.addAction(action)
    actions[0].setChecked(True)
    widget = WorkspaceSwitch(actions)
    group.setParent(widget)
    for action in actions:
        action.setParent(widget)
    widget.actionGroup = group
    widget.resize(widget.sizeHint())
    widget.show()
    QApplication.processEvents()
    return widget


def testConfirmedSelectionSlidesAndRepeatedRefreshDoesNotRestart(qtApp):
    switch = standalone()
    switch.setCurrentIndex(1)
    assert switch.animation.state() == QAbstractAnimation.Running
    switch.animation.pause()
    switch.animation.setCurrentTime(70)
    intermediate = switch.selectedRect().x()
    assert switch.buttons[0].x() < intermediate < switch.buttons[1].x()
    switch.setCurrentIndex(1)
    assert switch.animation.currentTime() == 70
    assert switch.selectedRect().x() == intermediate
    switch.animation.setCurrentTime(switch.animation.duration())
    assert switch.selectedRect().x() == switch.buttons[1].x()


def testRapidReverseContinuesFromCurrentPositionAndHideSettles(qtApp):
    switch = standalone()
    switch.setCurrentIndex(1)
    switch.animation.pause()
    switch.animation.setCurrentTime(60)
    intermediate = switch.slidePosition
    switch.setCurrentIndex(0)
    assert switch.animation.startValue() == intermediate
    assert switch.slidePosition == intermediate
    switch.hide()
    assert switch.animation.state() == QAbstractAnimation.Stopped
    assert switch.slidePosition == 0
    switch.show()
    assert switch.selectedRect().x() == switch.buttons[0].x()


def testDestroyDuringAnimationRetiresNativeOwner(qtApp):
    switch = standalone()
    animation = switch.animation
    switch.setCurrentIndex(1)
    switch.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not isValid(switch) and not isValid(animation)
    QApplication.processEvents()


def testDirectButtonsSwitchWorkspacesWithoutAddingHistoryOrJobs(designer):  # noqa: F811
    c, e, key = setupPage(designer)
    switch = c.chrome.selector
    before, history = c.session.payload(), len(c.session._undo)
    assert [b.text() for b in switch.buttons] == ['流程设计', '页面设计']
    assert all(b.menu() is None for b in switch.buttons)
    QTest.mouseClick(switch.buttons[0], Qt.LeftButton)
    assert not c.pageActive() and switch.currentIndex() == 0
    assert switch.buttons[0].isChecked() and not switch.buttons[1].isChecked()
    QTest.mouseClick(switch.buttons[1], Qt.LeftButton)
    assert c.pageActive() and switch.currentIndex() == 1
    assert switch.buttons[1].isChecked() and not switch.buttons[0].isChecked()
    assert e.tools.selected == key
    assert c.session.payload() == before and len(c.session._undo) == history
    assert c.preview.session is None and c.preview.backend is None
    assert designer.currentJobId is None


def testInvalidPendingInputCannotMoveSelectionPill(designer):  # noqa: F811
    c, e, key = setupPage(designer)
    switch = c.chrome.selector
    switch.animation.stop()
    switch.slidePosition = 1
    e.tools.fields['fontSize'].setValue(1)
    before, history = c.session.payload(), len(c.session._undo)
    QTest.mouseClick(switch.buttons[0], Qt.LeftButton)
    assert c.pageActive() and switch.currentIndex() == 1 and switch.slidePosition == 1
    assert switch.buttons[1].isChecked() and not switch.buttons[0].isChecked()
    assert e.tools.selected == key and e.tools.fields['fontSize'].value() == 1
    assert c.session.payload() == before and len(c.session._undo) == history
    e.tools.fields['fontSize'].setValue(0)


def testKeyboardAndDisabledChoiceRespectExistingActions(designer):  # noqa: F811
    c, _e, _key = setupPage(designer)
    switch = c.chrome.selector
    switch.buttons[0].setFocus()
    QTest.keyClick(switch.buttons[0], Qt.Key_Space)
    assert not c.pageActive() and switch.currentIndex() == 0
    c.chrome.workspaceActions[1].setEnabled(False)
    QTest.mouseClick(switch.buttons[1], Qt.LeftButton)
    assert not c.pageActive() and switch.currentIndex() == 0
    c.chrome.workspaceActions[1].setEnabled(True)
    switch.buttons[1].setFocus()
    QTest.keyClick(switch.buttons[1], Qt.Key_Space)
    assert c.pageActive() and switch.currentIndex() == 1


def testDecliningFirstPageEnableKeepsFlowAndOriginalProject(qtApp, monkeypatch):
    from emo_master.apps.designer.ui.main_window import MainWindow
    monkeypatch.delenv('EMO_PAGE_DESIGNER', raising=False)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    window = MainWindow(Client())
    window.show()
    coordinator = window.pageCoordinator
    switch = coordinator.chrome.selector
    QTest.mouseClick(switch.buttons[1], Qt.LeftButton)
    assert not coordinator.pageActive() and coordinator.editor is None
    assert switch.currentIndex() == 0 and switch.slidePosition == 0
    assert switch.buttons[0].isChecked() and not switch.buttons[1].isChecked()
    assert coordinator.session.document().schemaVersion == '2.1'
    assert not coordinator.session.dirty
    window.close()
