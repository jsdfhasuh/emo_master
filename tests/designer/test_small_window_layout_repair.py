"""Production-assembled PLC editor and painted toolbar overflow regressions."""
import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QLabel, QMenu, QToolButton

from tests.designer.test_plc_editor import editor, _Context, _pump  # noqa: F401
from tests.designer.test_toolbar_width import settle
from tests.designer.test_visual_layout import makeWindow


@pytest.mark.parametrize('width,height', [(640, 474), (800, 478)])
@pytest.mark.parametrize('directory', ['plc_slmp_read', 'plc_slmp_write'])
def testProductionPlcFieldsRemainReachable(editor, designerApplication, width, height, directory):  # noqa: F811 - imported pytest fixture
    context = _Context(directory)
    context.variableDefinitions = lambda: {}
    context.variableBindings = []
    controller, _, window = editor(directory, window=True, context=context)
    window.bindDebug(lambda *args: None)
    settle(designerApplication)
    controller.tabs.setCurrentWidget(controller.runtimePage)
    controller.advancedButton.setChecked(True)
    window.resize(width, height)
    settle(designerApplication)
    assert window._bindingPanel is not None
    baseline = controller.collectParams()
    bindings = window._bindingPanel.bindings()
    assert controller.advancedScroll.verticalScrollBar().maximum() == 0
    for control in [controller.hostEdit, controller.portSpin, controller.deviceCombo,
                    controller.addressSpin, controller.dataTypeCombo, controller.countSpin,
                    *controller._endpointControls.values()]:
        # Scroll the actual assembled page, not a reduced editor fixture.
        controller.runtimeScroll.ensureWidgetVisible(control)
        settle(designerApplication)
        viewport = controller.runtimeScroll.viewport()
        bounds = control.rect().translated(control.mapTo(viewport, QPoint()))
        assert viewport.rect().contains(bounds), (control.objectName(), bounds, viewport.rect())
    for caption in ('PLC 软元件', '起始地址', '数据类型', '数量'):
        labels = [label for label in controller.runtimePage.findChildren(QLabel)
                  if label.text().strip() == caption]
        assert labels, caption
        label = labels[0]
        controller.runtimeScroll.ensureWidgetVisible(label)
        settle(designerApplication)
        assert label.width() >= label.fontMetrics().horizontalAdvance(label.text()), caption
        assert label.height() >= label.heightForWidth(label.width()), caption
    assert window._applyButton.isVisible()
    assert controller.collectParams() == baseline
    assert window._bindingPanel.bindings() == bindings
    assert controller.tabs.tabText(controller.tabs.indexOf(window._bindingScroll)) == '参数来源'
    controller.tabs.setCurrentWidget(window._bindingScroll)
    settle(designerApplication)
    assert window._bindingScroll.isVisible()
    for control in window._bindingPanel.fields.values():
        window._bindingScroll.ensureWidgetVisible(control)
        settle(designerApplication)
        viewport = window._bindingScroll.viewport()
        bounds = control.rect().translated(control.mapTo(viewport, QPoint()))
        assert viewport.rect().contains(bounds)
    controller.tabs.setCurrentWidget(controller.runtimePage)
    settle(designerApplication)
    assert controller.collectParams() == baseline


@pytest.mark.parametrize('width', [480, 589, 658, 800])
def testOverflowButtonHasPaintedArrowAndSharedTailActions(designerApplication, width):
    window = makeWindow()
    try:
        window.show()
        window.resize(width, 500)
        settle(designerApplication)
        toolbar = window.mainToolbar
        extension = toolbar.findChild(QToolButton, 'qt_toolbar_ext_button')
        assert extension.isVisible()
        assert toolbar.rect().contains(extension.geometry())
        # isVisible() alone missed a zero-sized arrow paint rect under global QSS.
        shot = extension.grab().toImage()
        dark = 0
        for y in range(shot.height()):
            for x in range(shot.width()):
                color = shot.pixelColor(x, y)
                if max(color.red(), color.green(), color.blue()) < 180:
                    dark += 1
        assert dark >= 4, 'overflow exists but has no visible arrow'
        QTest.mouseClick(extension, Qt.LeftButton)
        chrome = window.pageCoordinator.chrome
        _pump(designerApplication, lambda: toolbar.widgetForAction(chrome.testAction).isVisible())
        for action in (window._toolbarActions['自动布局'], chrome.testAction, chrome.redo):
            button = toolbar.widgetForAction(action)
            assert button.isVisible()
            assert toolbar.rect().contains(button.geometry())
        layoutAction = window._toolbarActions['自动布局']
        activated = []
        layoutAction.triggered.connect(lambda: activated.append(True))
        QTest.mouseClick(toolbar.widgetForAction(layoutAction), Qt.LeftButton)
        assert len(activated) == 1
        QTest.mouseClick(extension, Qt.LeftButton)
        settle(designerApplication)
    finally:
        window.close()


def testMenuCaptionAndShortcutColumnsHaveReadableGap(designerApplication):
    from emo_master.apps.designer.ui.icon_map import icon
    menu = QMenu()
    try:
        redo = menu.addAction('重做')
        redo.setShortcut('Ctrl+Shift+Z')
        copy = menu.addAction(icon('layout-grid'), '复制所选算子')
        copy.setShortcut('Ctrl+D')
        menu.addAction('复制当前工作流')
        delete = menu.addAction('删除选中元素')
        delete.setShortcut('Del')
        menu.popup(QPoint(0, 0))
        settle(designerApplication)
        fm = menu.fontMetrics()
        # Global QSS padding + icon + the longest shortcut must not consume
        # the caption column. The original 240 px row fails this measurement.
        caption = max(fm.horizontalAdvance(a.text()) for a in menu.actions())
        shortcut = max(fm.horizontalAdvance(a.shortcut().toString()) for a in menu.actions())
        required = caption + shortcut + 2 * 26 + 16 + fm.horizontalAdvance(' ')*2
        assert menu.actionGeometry(copy).width() >= required
        assert copy.shortcut().toString() == 'Ctrl+D'
        assert delete.shortcut().toString() == 'Del'
    finally:
        menu.close()
