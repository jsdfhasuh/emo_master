import pytest

from emo_master.apps.designer.ui.operator_bubble import OperatorBubble

pytest.importorskip("PySide2.QtWidgets")
from PySide2.QtCore import QEvent, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QPushButton, QWidget


@pytest.fixture
def popup(designerApplication):
    owner = QWidget()
    owner.resize(1000, 700)
    anchor = QPushButton("All", owner)
    anchor.setObjectName("sideCategoryButton")
    anchor.setGeometry(20, 20, 100, 40)
    outside = QPushButton("Outside", owner)
    outside.setGeometry(20, 600, 100, 40)
    bubble = OperatorBubble(owner)
    bubble.setOperators([
        {"operatorId": "vision.edge.canny", "displayName": "Canny"},
        {"operatorId": "vision.inference.yolo", "displayName": "YOLO Inference"},
    ])
    owner.show()
    bubble.showAt(anchor)
    designerApplication.processEvents()
    try:
        yield owner, bubble, anchor, outside
    finally:
        bubble.close()
        owner.close()


def nativeClick(widget):
    window = widget.window()
    handle = window.windowHandle()
    assert handle is not None
    position = widget.mapTo(window, widget.rect().center())
    # A QWidget-only click bypasses the native QWindow event that caused the bug.
    QTest.mouseClick(handle, Qt.LeftButton, Qt.NoModifier, position)
    QApplication.processEvents()


def testNativeWindowClickKeepsSearchEditable(popup):
    _, bubble, _, _ = popup
    nativeClick(bubble.searchInput)
    assert bubble.isVisible()
    QTest.keyClicks(bubble.searchInput, "YO")
    nativeClick(bubble.searchInput)
    QTest.keyClick(bubble.searchInput, Qt.Key_End)
    QTest.keyClicks(bubble.searchInput, "LO")
    assert bubble.isVisible()
    assert bubble.searchInput.text() == "YOLO"
    assert bubble.getVisibleOperatorIds() == ["vision.inference.yolo"]


def testNativeWindowCardClickCreatesExactlyOnce(popup):
    _, bubble, _, _ = popup
    created = []
    bubble.setCreateHandler(created.append)
    nativeClick(bubble._buttons[0])
    assert bubble.isVisible()
    assert [item["operatorId"] for item in created] == ["vision.edge.canny"]


def testNativeWindowBlankContentClickStaysOpen(popup):
    _, bubble, _, _ = popup
    nativeClick(bubble._scroll.viewport())
    assert bubble.isVisible()


def testNativeCategoryClickKeepsPopupOpenUntilCategoryHandler(popup):
    _, bubble, anchor, _ = popup
    states = []
    anchor.clicked.connect(lambda: states.append(bubble.isVisible()))
    nativeClick(anchor)
    assert states == [True]
    assert bubble.isVisible()


def testNativeSameCategoryClickCanTogglePopupClosed(popup):
    _, bubble, anchor, _ = popup
    anchor.clicked.connect(lambda: bubble.close() if bubble.isVisible() else bubble.showAt(anchor))
    nativeClick(anchor)
    assert not bubble.isVisible()


def testNativeOutsideClickClosesPopupAndReachesItsTarget(popup):
    _, bubble, _, outside = popup
    clicked = []
    outside.clicked.connect(lambda: clicked.append(True))
    nativeClick(outside)
    assert not bubble.isVisible()
    assert clicked == [True]


def testEscapeAndApplicationDeactivateStillClosePopup(popup):
    _, bubble, anchor, _ = popup
    QTest.keyClick(bubble.searchInput, Qt.Key_Escape)
    assert not bubble.isVisible()
    bubble.showAt(anchor)
    assert bubble.isVisible()
    QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.ApplicationDeactivate))
    assert not bubble.isVisible()
