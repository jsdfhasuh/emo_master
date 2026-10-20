"""Native wheel gestures scroll schema forms without silently editing drafts."""
from copy import deepcopy

import pytest
from PySide2.QtCore import QPoint, QPointF, Qt
from PySide2.QtGui import QWheelEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import (
    QApplication, QComboBox, QLineEdit, QScrollArea, QSpinBox,
    QStyle, QStyleOptionSpinBox, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.ui.param_form import SchemaParamForm
from emo_master.core.project.device_resources import trustedDeviceTypes


def settle(app):
    for _ in range(4):
        app.processEvents()


def wheel(control, delta=-120):
    position = control.rect().center()
    event = QWheelEvent(QPointF(position), QPointF(control.mapToGlobal(position)),
                        QPoint(), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(control, event)


def scrollForm(app, schema, values=None):
    window = QWidget()
    layout = QVBoxLayout(window)
    other = QLineEdit('Focus outside parameters')
    layout.addWidget(other)
    form = SchemaParamForm()
    form.setSchema(schema, values or {})
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(form)
    layout.addWidget(scroll)
    window.resize(540, 260)
    window.show()
    settle(app)
    return window, form, scroll, other


def assertScrollOnly(app, form, scroll, control):
    before = deepcopy(form.getValues())
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0
    start = bar.value()
    delta = 120 if start > 0 else -120
    # Use Qt's own viewport handling as the scroll distance oracle. This also
    # catches swallowed gestures and accidental double-forwarding.
    wheel(scroll.viewport(), delta)
    settle(app)
    expected = bar.value()
    assert expected != start
    bar.setValue(start)
    wheel(control, delta)
    settle(app)
    assert form.getValues() == before
    assert bar.value() == expected


@pytest.mark.parametrize('field', ['moduleIoNo', 'frameEncoding'])
@pytest.mark.parametrize('focused', [False, True])
def testSlmpConnectionWheelPreservesDraft(designerApplication, field, focused):
    declaration = trustedDeviceTypes()['device.plc.mitsubishi_slmp']
    window, form, scroll, other = scrollForm(designerApplication, declaration.connectionSchema)
    control = form._controls[field]
    scroll.ensureWidgetVisible(control)
    (control if focused else other).setFocus()
    settle(designerApplication)
    assert control.hasFocus() is focused
    assert form.getValues()['moduleIoNo'] == 1023
    assert form.getValues()['frameEncoding'] == 'binary'
    assertScrollOnly(designerApplication, form, scroll, control)
    window.close()


FIELDS = [
    pytest.param({'type': 'integer', 'default': 1023}, id='integer'),
    pytest.param({'type': 'number', 'default': 1.25}, id='number-text'),
    pytest.param({'type': 'integer', 'default': 2 ** 40}, id='wide-integer-text'),
    pytest.param({'type': 'string', 'enum': ['binary', 'ascii'], 'default': 'binary'}, id='enum'),
    pytest.param({'type': 'string', 'xWidget': 'workflow-select', 'xOptions': ['first', 'second'],
                  'default': 'first'}, id='workflow-selector'),
    pytest.param({'type': 'string', 'xWidget': 'port-select', 'enum': ['first', 'second'],
                  'default': 'first'}, id='port-selector'),
]


def paddedSchema(field):
    return {'type': 'object', 'properties': {
        'target': field,
        **{f'padding{i}': {'type': 'string', 'default': str(i)} for i in range(15)},
    }}


@pytest.mark.parametrize('field', FIELDS)
@pytest.mark.parametrize('focused', [False, True])
def testSchemaControlWheelScrollsRegardlessOfFocus(designerApplication, field, focused):
    window, form, scroll, other = scrollForm(designerApplication, paddedSchema(field))
    control = form._controls['target']
    (control if focused else other).setFocus()
    settle(designerApplication)
    assert control.hasFocus() is focused
    assertScrollOnly(designerApplication, form, scroll, control)
    if isinstance(control, QSpinBox):
        assertScrollOnly(designerApplication, form, scroll, control.lineEdit())
    window.close()


@pytest.mark.parametrize('wrapper', ['optional', 'nested'])
def testWrappedControlsAndSchemaReloadKeepWheelGuard(designerApplication, wrapper):
    field = {'type': 'integer', 'default': 1023}
    if wrapper == 'optional':
        field['xOptionalPresence'] = True
        values = {'target': 1023}
    else:
        field = {'type': 'object', 'properties': {'nested': field}}
        values = {'target': {'nested': 1023}}
    schema = paddedSchema(field)
    window, form, scroll, _ = scrollForm(designerApplication, schema, values)
    for _ in range(2):
        control = form.findChild(QSpinBox)
        control.setFocus()
        settle(designerApplication)
        assertScrollOnly(designerApplication, form, scroll, control)
        form.setSchema(schema, form.getValues())
        settle(designerApplication)
    window.close()


def testSchemaWheelGuardPreservesExplicitEditingAndPopupSelection(designerApplication):
    schema = paddedSchema({'type': 'integer', 'default': 1023})
    schema['properties']['mode'] = {'type': 'string', 'enum': ['binary', 'ascii'], 'default': 'binary'}
    window, form, scroll, _ = scrollForm(designerApplication, schema)
    spin = form._controls['target']
    QTest.mouseClick(spin.lineEdit(), Qt.LeftButton)
    QTest.keyClick(spin, Qt.Key_Up)
    assert spin.value() == 1024
    spin.selectAll()
    QTest.keyClicks(spin, '2048')
    QTest.keyClick(spin, Qt.Key_Return)
    assert spin.value() == 2048
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    up = spin.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, spin)
    QTest.mouseClick(spin, Qt.LeftButton, pos=up.center())
    assert spin.value() == 2049
    assertScrollOnly(designerApplication, form, scroll, spin)

    combo = form._controls['mode']
    assert isinstance(combo, QComboBox)
    scroll.ensureWidgetVisible(combo)
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key_Down)
    assert combo.currentData() == 'ascii'
    combo.showPopup()
    settle(designerApplication)
    view = combo.view()
    assert view.isVisible()
    row = view.visualRect(combo.model().index(0, combo.modelColumn()))
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=row.center())
    settle(designerApplication)
    assert combo.currentData() == 'binary'
    assert not view.isVisible()
    assertScrollOnly(designerApplication, form, scroll, combo)
    window.close()


def testOpenComboPopupKeepsItsOwnWheelScrolling(designerApplication):
    choices = [f'option-{index}' for index in range(100)]
    schema = paddedSchema({'type': 'string', 'enum': choices, 'default': choices[0]})
    window, form, scroll, _ = scrollForm(designerApplication, schema)
    combo = form._controls['target']
    combo.showPopup()
    settle(designerApplication)
    view = combo.view()
    assert view.isVisible()
    popupBar = view.verticalScrollBar()
    assert popupBar.maximum() > 0
    pagePosition = scroll.verticalScrollBar().value()
    before = form.getValues()
    wheel(view.viewport())
    settle(designerApplication)
    assert popupBar.value() > 0
    assert scroll.verticalScrollBar().value() == pagePosition
    assert form.getValues() == before
    combo.hidePopup()
    assertScrollOnly(designerApplication, form, scroll, combo)
    window.close()


def testStandaloneSchemaFieldsCannotBeEditedByWheel(designerApplication):
    form = SchemaParamForm()
    form.setSchema({'type': 'object', 'properties': {
        'number': {'type': 'integer', 'default': 1023},
        'mode': {'type': 'string', 'enum': ['binary', 'ascii'], 'default': 'binary'},
    }}, {})
    form.show()
    before = form.getValues()
    for control in form._controls.values():
        control.setFocus()
        settle(designerApplication)
        wheel(control)
        assert form.getValues() == before
    form.close()
