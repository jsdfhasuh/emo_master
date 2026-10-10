from emo_master.apps.designer.ui.operator_bubble import OperatorBubble


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


def testOperatorBubbleCreateHandlerReceivesPayload() -> None:
    ensureQApp()
    bubble = OperatorBubble()
    captured: dict[str, object] = {}

    def createHandler(payload: dict[str, object]) -> None:
        captured["operatorId"] = payload.get("operatorId")

    bubble.setCreateHandler(createHandler)
    bubble.setOperators(
        [
            {
                "operatorId": "vision.edge.canny",
                "displayName": "Canny",
                "iconKey": "edge",
                "summary": "边缘检测",
            }
        ]
    )
    simulateCreate = getattr(bubble, "simulateCreate", None)
    if callable(simulateCreate):
        simulateCreate(0)
    else:
        buttonList = getattr(bubble, "_buttons", [])
        if len(buttonList) > 0:
            buttonList[0].click()

    assert captured.get("operatorId") == "vision.edge.canny"


def testOperatorBubbleKeepsOwnerWidget() -> None:
    ensureQApp()
    try:
        from PySide2.QtWidgets import QWidget

        owner: object = QWidget()
    except Exception:
        owner = object()

    bubble = OperatorBubble(owner)  # type: ignore[arg-type]

    parentWidget = getattr(bubble, "parentWidget", None)
    assert callable(parentWidget)
    assert parentWidget() is owner


def testOperatorBubbleConsumesEscapeFromSearchInput() -> None:
    ensureQApp()
    try:
        from PySide2.QtCore import QEvent, Qt
        from PySide2.QtGui import QKeyEvent
        from PySide2.QtWidgets import QApplication, QWidget
    except Exception:
        return

    class TrackingOperatorBubble(OperatorBubble):
        def __init__(self, parent) -> None:
            self.closeCount = 0
            super().__init__(parent)

        def close(self) -> bool:  # type: ignore[override]
            self.closeCount += 1
            return bool(super().close())

    owner = QWidget()
    bubble = TrackingOperatorBubble(owner)
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    event.setAccepted(False)

    QApplication.sendEvent(bubble.searchInput, event)

    assert bubble.closeCount == 1
    assert event.isAccepted() is True


def testOperatorBubbleUsesToolWindowInsteadOfNativePopup() -> None:
    ensureQApp()
    try:
        from PySide2.QtCore import Qt
    except Exception:
        return

    bubble = OperatorBubble()
    windowType = bubble.windowFlags() & Qt.WindowType_Mask

    assert int(windowType) == int(Qt.Tool)


def testEscapeKeepsMaximizedOwnerWindowState() -> None:
    ensureQApp()
    try:
        from PySide2.QtCore import QEvent, Qt
        from PySide2.QtGui import QKeyEvent
        from PySide2.QtWidgets import QApplication, QMainWindow, QPushButton
    except Exception:
        return

    application = QApplication.instance()
    assert application is not None
    owner = QMainWindow()
    anchor = QPushButton("控制流", owner)
    owner.showMaximized()
    application.processEvents()
    assert bool(owner.windowState() & Qt.WindowMaximized)

    bubble = OperatorBubble(owner)
    bubble.showAt(anchor)
    application.processEvents()
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    QApplication.sendEvent(bubble.searchInput, event)
    application.processEvents()

    assert bubble.isVisible() is False
    assert bool(owner.windowState() & Qt.WindowMaximized)
    owner.close()


def testOperatorBubbleCardClickReceivesSystemNodePayload() -> None:
    ensureQApp()
    bubble = OperatorBubble()
    captured: list[dict[str, object]] = []
    bubble.setCreateHandler(lambda payload: captured.append(dict(payload)))
    bubble.setOperators(
        [
            {
                "operatorId": "system.repeat",
                "displayName": "Repeat",
                "category": "控制流",
                "systemNodeKind": "loop:repeat",
            }
        ]
    )

    buttons = getattr(bubble, "_buttons", [])
    assert len(buttons) == 1
    buttons[0].click()

    assert captured == [
        {
            "operatorId": "system.repeat",
            "displayName": "Repeat",
            "category": "控制流",
            "systemNodeKind": "loop:repeat",
        }
    ]


def testOperatorBubbleSupportsSearchAndRecentFirst() -> None:
    ensureQApp()
    bubble = OperatorBubble()
    bubble.setOperators(
        [
            {
                "operatorId": "vision.edge.canny",
                "displayName": "Canny",
                "iconKey": "edge",
                "summary": "边缘检测",
            },
            {
                "operatorId": "vision.pre.blur",
                "displayName": "Blur",
                "iconKey": "pre",
                "summary": "模糊",
            },
        ]
    )

    setRecentOperatorIds = getattr(bubble, "setRecentOperatorIds", None)
    setSearchKeyword = getattr(bubble, "setSearchKeyword", None)
    getVisibleOperatorIds = getattr(bubble, "getVisibleOperatorIds", None)
    assert callable(setRecentOperatorIds)
    assert callable(setSearchKeyword)
    assert callable(getVisibleOperatorIds)

    setRecentOperatorIds(["vision.pre.blur"])
    visibleIds = getVisibleOperatorIds()
    assert isinstance(visibleIds, list)
    assert visibleIds[0] == "vision.pre.blur"

    setSearchKeyword("canny")
    searchedByName = getVisibleOperatorIds()
    assert searchedByName == ["vision.edge.canny"]

    setSearchKeyword("vision.pre")
    searchedById = getVisibleOperatorIds()
    assert searchedById == ["vision.pre.blur"]


def testOperatorBubbleCanKeepStablePositionAcrossCategorySwitch() -> None:
    ensureQApp()
    bubble = OperatorBubble()
    getPopupPosition = getattr(bubble, "getPopupPosition", None)
    assert callable(getPopupPosition)

    try:
        from PySide2.QtCore import QPoint
    except Exception:

        class QPoint:  # type: ignore[no-redef]
            def __init__(self, x: int, y: int) -> None:
                self._x = x
                self._y = y

            def x(self) -> int:
                return self._x

            def y(self) -> int:
                return self._y

    class AnchorA:
        def width(self) -> int:
            return 24

        def mapToGlobal(self, point):
            _ = point
            return QPoint(120, 64)

    class AnchorB:
        def width(self) -> int:
            return 24

        def mapToGlobal(self, point):
            _ = point
            return QPoint(420, 64)

    showAt = getattr(bubble, "showAt", None)
    assert callable(showAt)
    showAt(AnchorA())
    firstPos = getPopupPosition()

    showAt(AnchorB(), keepPosition=True)
    secondPos = getPopupPosition()

    assert firstPos == secondPos


def testOperatorBubbleCanDisableRecentPriority() -> None:
    ensureQApp()
    bubble = OperatorBubble()
    bubble.setOperators(
        [
            {
                "operatorId": "vision.edge.canny",
                "displayName": "Canny",
                "iconKey": "edge",
                "summary": "边缘检测",
            },
            {
                "operatorId": "vision.pre.blur",
                "displayName": "Blur",
                "iconKey": "pre",
                "summary": "模糊",
            },
        ]
    )
    setRecentOperatorIds = getattr(bubble, "setRecentOperatorIds", None)
    setRecentEnabled = getattr(bubble, "setRecentEnabled", None)
    getVisibleOperatorIds = getattr(bubble, "getVisibleOperatorIds", None)
    assert callable(setRecentOperatorIds)
    assert callable(setRecentEnabled)
    assert callable(getVisibleOperatorIds)

    setRecentOperatorIds(["vision.pre.blur"])
    prioritized = getVisibleOperatorIds()
    assert isinstance(prioritized, list)
    assert prioritized[0] == "vision.pre.blur"

    setRecentEnabled(False)
    disabledOrder = getVisibleOperatorIds()
    assert isinstance(disabledOrder, list)
    assert disabledOrder[0] == "vision.edge.canny"


def testOperatorBubbleUsesControlFlowCardStyle() -> None:
    ensureQApp()
    bubble = OperatorBubble()
    bubble.setOperators(
        [
            {
                "operatorId": "vision.flow.if",
                "displayName": "If",
                "iconKey": "default",
                "summary": "条件分支",
                "category": "控制流",
            }
        ]
    )
    buttons = getattr(bubble, "_buttons", [])
    assert len(buttons) == 1
    objectName = getattr(buttons[0], "objectName", None)
    assert callable(objectName)
    assert objectName() == "operatorBubbleItemFlow"
