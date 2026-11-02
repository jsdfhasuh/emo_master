"""SQLite editing space and on-demand details through the assembled Qt editor."""
import json
import os
from pathlib import Path
import sqlite3

import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import (
    QApplication, QAbstractItemView, QDialogButtonBox, QLineEdit, QPushButton, QScrollArea, QTableWidget,
)

from emo_master.apps.designer.operator_editors import OperatorEditorManager
from tests.sqlite_writer.test_editor import editor  # noqa: F401 - shared temporary Runtime/editor
from tests.sqlite_writer.test_user_path import waitQt


@pytest.fixture(autouse=True)
def projectVariableSources(monkeypatch, retainedQtApplication):
    # The older editor fixture omits this optional production hook; Designer
    # supplies it even for a project that has no global variables yet.
    original = OperatorEditorManager.open
    from emo_master.apps.designer.main import applyDesignerStyle
    applyDesignerStyle(retainedQtApplication)

    def openWithVariables(manager, **kwargs):
        kwargs.setdefault('variableDefinitions', lambda: {})
        return original(manager, **kwargs)

    monkeypatch.setattr(OperatorEditorManager, 'open', openWithVariables)


def settle():
    for _ in range(3):
        QApplication.processEvents()


def constantRows(count):
    return [{'column': f'field_{index}', 'source': {'kind': 'constant', 'value': index},
             'storageType': 'INTEGER', 'missing': 'error'} for index in range(count)]


def screenshot(widget, name):
    output = os.environ.get('EMO_SQLITE_LAYOUT_SCREEN_DIR')
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        settle()
        assert widget.grab().save(str(directory / (name + '.png')))


@pytest.mark.parametrize('width,height', [(640, 480), (980, 720)])
def testMappingsUseMainSpaceAndLastRowRemainsEditable(editor, width, height):  # noqa: F811
    window, controller, _applied, _draft, _manager = editor
    controller._loadRows(constantRows(18))
    controller.fields.selectRow(0)
    controller.fields.scrollToTop()
    controller.tabs.setCurrentWidget(controller.mappingPage)
    # Let the initial show/screen-fit callback finish before testing an
    # explicit user resize; the offscreen desktop otherwise caps it at 552 px.
    settle()
    window.resize(width, height)
    window.show()
    settle()

    assert window.width() <= width and window.height() <= height
    assert controller.fields.isVisible() and not window._bindingScroll.isVisible()
    # Scrolling the main list must not be trapped inside a second scrolling form.
    parent = controller.fields.parentWidget()
    while parent is not window:
        assert not isinstance(parent, QScrollArea)
        parent = parent.parentWidget()
    assert controller.fields.height() > controller.mappingPage.height() * .5
    if width == 980:
        assert controller.fields.viewport().height() >= controller.fields.rowHeight(0) * 10
        assert controller.fields.horizontalScrollBar().maximum() == 0
    for control in (controller.addButton, controller.deleteButton, window._applyButton, window._closeButton):
        assert control.isVisible()
        assert window.rect().contains(control.rect().translated(control.mapTo(window, QPoint())))
    screenshot(window, f'mappings-{width}x{height}')

    last = controller.fields.item(17, 0)
    controller.fields.scrollToItem(last, QAbstractItemView.PositionAtBottom)
    settle()
    assert controller.fields.viewport().rect().contains(controller.fields.visualItemRect(last))
    controller.fields.setCurrentItem(last)
    controller.fields.editItem(last)
    settle()
    editorWidget = QApplication.focusWidget()
    assert isinstance(editorWidget, QLineEdit) and controller.fields.isAncestorOf(editorWidget)
    QTest.keyClick(editorWidget, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(editorWidget, 'last_record')
    QTest.keyClick(editorWidget, Qt.Key_Return)
    settle()
    assert controller.collectParams()['mappings'][-1]['column'] == 'last_record'


def testAdvancedSourcesStayAccessibleAndPreserveDraftBindings(editor):  # noqa: F811
    window, controller, _applied, _draft, _manager = editor
    variables = {'table-source': {'name': '目标表变量', 'type': 'string', 'initialValue': 'records'}}
    window.context.variableDefinitions = lambda: variables
    window.refreshSchema(window.context.paramSchema)
    baseline = controller.collectParams()
    index = controller.tabs.indexOf(window._bindingScroll)
    assert index >= 0 and controller.tabs.tabText(index) == '更多设置'
    QTest.mouseClick(controller.tabs.tabBar(), Qt.LeftButton,
                     pos=controller.tabs.tabBar().tabRect(index).center())
    settle()
    source = window._bindingPanel.fields[('table',)]
    window._bindingScroll.ensureWidgetVisible(source)
    settle()
    assert source.isVisible()
    source.setCurrentIndex(source.findData('table-source'))
    expected = [{'parameterPath': ['table'], 'variableId': 'table-source'}]
    assert window._bindingPanel.bindings() == expected

    controller.tabs.setCurrentWidget(controller.mappingPage)
    variables['table-source']['name'] = '已改名的目标表'
    window.refreshSchema(window.context.paramSchema)
    assert controller.collectParams() == baseline
    assert window._bindingPanel.bindings() == expected
    controller.tabs.setCurrentWidget(window._bindingScroll)
    settle()
    assert '已改名的目标表' in window._bindingPanel.fields[('table',)].currentText()
    assert window._bindingPanel.fields[('table',)].isVisible()
    screenshot(window, 'advanced-parameter-sources')


def testDatabaseShortcutAndSourceSelectionWorkAcrossTabs(editor):  # noqa: F811
    window, controller, _applied, _draft, _manager = editor
    controller._loadRows(constantRows(1))
    controller.tabs.setCurrentWidget(controller.mappingPage)
    shortcut = next(button for button in controller.mappingPage.findChildren(QPushButton)
                    if button.text() == '修改保存位置')
    QTest.mouseClick(shortcut, Qt.LeftButton)
    settle()
    assert controller.tabs.currentWidget() is controller.scroll
    controller.path.setText('changed-target.sqlite3')
    screenshot(window, 'database-settings')
    controller.tabs.setCurrentWidget(controller.mappingPage)
    settle()
    assert 'changed-target.sqlite3' in controller.targetSummary.text()
    QTest.mouseClick(controller.fields.cellWidget(0, 1), Qt.LeftButton)
    settle()
    dialog = QApplication.activeModalWidget()
    assert dialog is not None and hasattr(dialog, 'constant')
    dialog.tabs.setCurrentIndex(1)
    dialog.constant.setPlainText('42')
    QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)
    settle()
    assert controller.collectParams()['mappings'][0]['source'] == {'kind': 'constant', 'value': 42}
    assert controller.collectParams()['databasePath'] == 'changed-target.sqlite3'


def testSchemaSummaryOpensCompleteReadOnlyDetailsAndInvalidatesOnTargetChange(editor, tmp_path):  # noqa: F811
    _window, controller, _applied, _draft, _manager = editor
    database = tmp_path / 'details.sqlite3'
    longDefault = '完整默认值_' * 180
    columns = [f'field_{index} TEXT NOT NULL' for index in range(18)]
    columns[-1] += " DEFAULT '" + longDefault + "'"
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE records (id INTEGER PRIMARY KEY, write_id TEXT NOT NULL UNIQUE, '
                           + ', '.join(columns) + ')')
    controller.tabs.setCurrentWidget(controller.scroll)
    controller.path.setText(str(database))
    controller.scroll.ensureWidgetVisible(controller.inspectButton)
    QTest.mouseClick(controller.inspectButton, Qt.LeftButton)
    waitQt(lambda: controller._pending is None)
    assert not controller.error.text()
    assert '20' in controller.structure.text() and len(controller.structure.text()) < 180
    assert longDefault not in controller.structure.text()
    assert controller.structureButton.isEnabled()
    controller.scroll.ensureWidgetVisible(controller.structureButton)
    QTest.mouseClick(controller.structureButton, Qt.LeftButton)
    settle()
    dialog = QApplication.activeModalWidget()
    assert dialog is not None
    details = dialog.findChild(QTableWidget, 'sqliteStructureTable')
    assert details is not None and details.rowCount() == 20
    assert details.editTriggers() == QAbstractItemView.NoEditTriggers
    contents = [details.item(row, column).text() for row in range(details.rowCount())
                for column in range(details.columnCount()) if details.item(row, column) is not None]
    assert all(f'field_{index}' in contents for index in range(18))
    assert any(longDefault in value for value in contents)
    screenshot(dialog, 'database-structure')
    dialog.reject()
    settle()

    controller.table.setCurrentText('another_table')
    assert not controller.structureButton.isEnabled()
    assert '20' not in controller.structure.text()


def testHidingCreationOptionsKeepsDraftDefaults(editor):  # noqa: F811
    _window, controller, _applied, _draft, _manager = editor
    controller._loadRows(constantRows(2))
    controller.tabs.setCurrentWidget(controller.mappingPage)
    assert not controller.planOptions.isChecked()
    assert controller.fields.isColumnHidden(4) and controller.fields.isColumnHidden(5)
    QTest.mouseClick(controller.planOptions, Qt.LeftButton)
    assert not controller.fields.isColumnHidden(4) and not controller.fields.isColumnHidden(5)
    controller.fields.cellWidget(1, 4).setChecked(True)
    controller.fields.item(1, 5).setText(json.dumps(0))
    baseline = controller.collectParams()
    planned = controller._plan()
    QTest.mouseClick(controller.planOptions, Qt.LeftButton)
    assert controller.fields.isColumnHidden(4) and controller.fields.isColumnHidden(5)
    QTest.mouseClick(controller.planOptions, Qt.LeftButton)
    assert controller.collectParams() == baseline
    assert controller._plan() == planned
    assert controller.fields.cellWidget(1, 4).isChecked()
    assert controller.fields.item(1, 5).text() == '0'
