"""Scrolling a PLC page must not edit its draft or live debug destination."""
import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QWheelEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QComboBox, QSpinBox

from tests.designer.test_plc_editor import editor  # noqa: F401
from tests.designer.test_toolbar_width import settle


def wheel(control):
    position = control.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(control.mapToGlobal(position)),
                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(control, event)


def value(control):
    return control.value() if isinstance(control, QSpinBox) else control.currentData(), (
        control.currentIndex() if isinstance(control, QComboBox) else None)


@pytest.mark.parametrize('directory', ['plc_slmp_read', 'plc_slmp_write'])
@pytest.mark.parametrize('page', ['parameters', 'runtime'])
@pytest.mark.parametrize('focused', [False, True])
def testWheelScrollsWithoutChangingPlcValues(editor, designerApplication, directory, page, focused):  # noqa: F811
    controller, context, window = editor(directory, window=True)
    window.resize(640, 474)
    if page == 'runtime':
        controller.tabs.setCurrentWidget(controller.runtimePage)
        controller.advancedButton.setChecked(True)
        content = controller.runtimePage
        scroll = controller.runtimeScroll
    else:
        content = controller.paramForm
        scroll = content.parentWidget().parentWidget()
    settle(designerApplication)
    controls = content.findChildren(QSpinBox) + content.findChildren(QComboBox)
    assert controls
    baseline = controller.collectParams()
    for control in controls:
        scroll.ensureWidgetVisible(control)
        settle(designerApplication)
        scroll.verticalScrollBar().setValue(0)
        if focused:
            control.setFocus()
        else:
            window._applyButton.setFocus()
        before = value(control)
        wheel(control)
        settle(designerApplication)
        assert value(control) == before, control.objectName()
        assert scroll.verticalScrollBar().value() > 0, control.objectName()
    assert controller.collectParams() == baseline
    assert not context.calls  # No connect, read, write, or automatic Apply.


def testExplicitKeyboardEditingSurvivesSchemaReload(editor, designerApplication):  # noqa: F811
    controller, _, _ = editor(window=True)
    controller.loadParams({'connectTimeoutMs': 2345})
    spin = controller.paramForm._controls['connectTimeoutMs']
    spin.setFocus()
    QTest.keyClick(spin, Qt.Key_Up)
    assert spin.value() == 2346
    wheel(spin)
    assert spin.value() == 2346
    controller.tabs.setCurrentWidget(controller.runtimePage)
    settle(designerApplication)
    controller.portSpin.setFocus()
    before = controller.portSpin.value()
    QTest.keyClick(controller.portSpin, Qt.Key_Up)
    assert controller.portSpin.value() == before + 1
