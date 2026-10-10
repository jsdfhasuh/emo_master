"""The painted category selection must agree with the catalog filter."""
import pytest
import emo_master  # noqa: F401 - preload Windows dependency DLLs before Qt
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from tests.designer.test_floating_toolbox import makeWindow


@pytest.fixture
def categoryWindow(designerApplication):
    window, calls = makeWindow()
    window.expandSidebar()
    yield window, calls
    window.close()


def assertSelection(window, category):
    # Move away from the buttons so hover does not obscure the selected state.
    viewport = window.flowView.viewport()
    QTest.mouseMove(viewport, QPoint(viewport.width() - 3, viewport.height() - 3))
    QApplication.processEvents()
    assert window.activeOperatorCategory == category
    for name, button in window.categoryButtons.items():
        assert button.isChecked() is (name == category)
        image = button.grab().toImage()
        ratio = image.devicePixelRatio()
        # The last row can contain a short button. Sample its empty left
        # padding, away from native ClearType text and the rounded border.
        color = image.pixelColor(round(3 * ratio),
                                 round(button.height() / 2 * ratio)).name()
        assert color == ('#edf3ff' if name == category else '#ffffff'), (name, category, color)
    expected = {row['operatorId'] for row in window._getOperatorsByCategory()}
    assert set(window.operatorBubble.getVisibleOperatorIds()) == expected


@pytest.mark.parametrize('category', ['全部', '预处理', '检测', '测量', '输出', '控制流', '其他'])
def testCategoryClickPaintsExactlyOneSelectionAndKeepsRepeatedSelection(categoryWindow, category):
    window, calls = categoryWindow
    for name in ('全部', category, category, '检测', '全部'):
        QTest.mouseClick(window.categoryButtons[name], Qt.LeftButton)
        assertSelection(window, name)
    assert calls == []


def testKeyboardSelectionSurvivesTabsSearchAndToolboxReopen(categoryWindow):
    window, calls = categoryWindow
    button = window.categoryButtons['预处理']
    button.setFocus()
    QTest.keyClick(button, Qt.Key_Space)
    assertSelection(window, '预处理')
    window.operatorBubble.searchInput.setText('missing-operator')
    assert window.operatorBubble.getVisibleOperatorIds() == []
    assert window.categoryButtons['预处理'].isChecked()
    window.operatorBubble.searchInput.clear()
    for index in (1, 2, 0):
        window.floatingToolbox.tabs.setCurrentIndex(index)
    window.collapseSidebar()
    window.expandSidebar()
    assertSelection(window, '预处理')
    assert calls == []
