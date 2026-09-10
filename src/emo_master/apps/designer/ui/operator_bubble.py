from __future__ import annotations

import json
from typing import Callable



CreateHandler = Callable[[dict[str, object]], None]


try:
    from PySide2.QtCore import QByteArray, QEvent, QMimeData, QPoint, QSize, Qt
    from PySide2.QtGui import QColor, QDrag, QPainter
    from PySide2.QtWidgets import (
        QApplication,
        QGridLayout,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
        QStyle, QStyleOptionButton, QSizePolicy,
    )
    from emo_master.apps.designer.ui.icon_map import operatorIcon
    from emo_master.apps.designer.ui.theme import uiFont
    from emo_master.apps.designer.ui.widgets import scrollContent

    OPERATOR_MIME_TYPE = "application/x-emo-operator"

    class _OperatorCardButton(QPushButton):
        def __init__(self, payload: dict[str, object]) -> None:
            displayName = str(payload.get("displayName", ""))
            iconKey = str(payload.get("iconKey", "default"))
            operatorId = str(payload.get("operatorId", ""))
            super().__init__(displayName)
            self._displayName = displayName
            self._operatorId = operatorId
            self._icon = operatorIcon(iconKey)
            self.setAccessibleName(displayName)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.payload = payload
            self._dragStartPos: QPoint | None = None
            self._dragStarted = False
            self.setObjectName(_getCardObjectName(str(payload.get("category", "其他"))))
            self.setToolTip(_buildTooltip(payload))

        def sizeHint(self):
            return QSize(240, max(72, self.fontMetrics().height() * 2 + 30))

        def minimumSizeHint(self):
            return QSize(140, self.sizeHint().height())

        def paintEvent(self, event):
            option = QStyleOptionButton()
            self.initStyleOption(option)
            option.text = ""
            painter = QPainter(self)
            self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
            self._icon.paint(painter, 12, 12, 20, 20)
            painter.setFont(uiFont(bold=True))
            painter.setPen(QColor("#20242b"))
            fm = painter.fontMetrics()
            title = fm.elidedText(self._displayName, Qt.ElideRight, max(0, self.width() - 52))
            painter.drawText(42, 12 + fm.ascent(), title)
            painter.setFont(uiFont(points=10.0))
            painter.setPen(QColor("#626b78"))
            fm = painter.fontMetrics()
            subtitle = fm.elidedText(self._operatorId, Qt.ElideRight, max(0, self.width() - 24))
            painter.drawText(12, self.height() - 14 - fm.descent(), subtitle)

        def mousePressEvent(self, event) -> None:  # type: ignore[override]
            self._dragStartPos = event.pos()
            self._dragStarted = False
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
            if self._dragStartPos is None:
                super().mouseMoveEvent(event)
                return
            if not (event.buttons() & Qt.LeftButton):
                super().mouseMoveEvent(event)
                return
            distance = (event.pos() - self._dragStartPos).manhattanLength()
            if distance < QApplication.startDragDistance():
                super().mouseMoveEvent(event)
                return

            drag = QDrag(self)
            self._dragStarted = True
            mimeData = QMimeData()
            payloadText = json.dumps(self.payload, ensure_ascii=False)
            mimeData.setData(
                OPERATOR_MIME_TYPE, QByteArray(payloadText.encode("utf-8"))
            )
            drag.setMimeData(mimeData)
            drag.setPixmap(self.grab())
            drag.exec_(Qt.CopyAction)
            self._dragStartPos = None

        def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
            if self._dragStarted:
                self._dragStarted = False
                self._dragStartPos = None
                self.setDown(False)
                event.accept()
                return
            self._dragStartPos = None
            super().mouseReleaseEvent(event)

    class OperatorBubble(QWidget):
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
            self._createHandler: CreateHandler | None = None
            self._buttons: list[_OperatorCardButton] = []
            self._operators: list[dict[str, object]] = []
            self._visibleOperators: list[dict[str, object]] = []
            self._searchKeyword = ""
            self._recentOperatorIds: list[str] = []
            self._recentEnabled = True
            self._lastPopupPoint: tuple[int, int] | None = None
            self._grid = QGridLayout()
            self._grid.setContentsMargins(8, 8, 8, 8)
            self._grid.setSpacing(8)
            self._grid.setAlignment(Qt.AlignTop)
            self._columns = 2

            self.searchInput = QLineEdit()
            self.searchInput.setPlaceholderText("搜索节点（名称/ID）")
            self.searchInput.textChanged.connect(self.setSearchKeyword)
            self.searchInput.installEventFilter(self)

            rootLayout = QVBoxLayout()
            rootLayout.addWidget(self.searchInput)
            gridHost = QWidget()
            gridHost.setLayout(self._grid)
            self._scroll = scrollContent(gridHost)
            rootLayout.addWidget(self._scroll, 1)
            self.setLayout(rootLayout)
            self.resize(560, 420)
            self.setMinimumSize(300, 180)
            self.setObjectName("operatorBubble")
            application = QApplication.instance()
            if application is not None:
                application.installEventFilter(self)

        def setCreateHandler(self, handler: CreateHandler | None) -> None:
            self._createHandler = handler

        def setOperators(self, operators: list[dict[str, object]]) -> None:
            self._operators = list(operators)
            self._refreshGrid()

        def setRecentOperatorIds(self, operatorIds: list[str]) -> None:
            self._recentOperatorIds = [
                str(operatorId) for operatorId in operatorIds if str(operatorId) != ""
            ]
            self._refreshGrid()

        def setRecentEnabled(self, enabled: bool) -> None:
            self._recentEnabled = bool(enabled)
            self._refreshGrid()

        def setSearchKeyword(self, keyword: str) -> None:
            self._searchKeyword = keyword.strip().lower()
            self._refreshGrid()

        def getVisibleOperatorIds(self) -> list[str]:
            visibleIds: list[str] = []
            for payload in self._visibleOperators:
                visibleIds.append(str(payload.get("operatorId", "")))
            return visibleIds

        def showAt(self, anchorWidget: object, keepPosition: bool = False) -> None:
            if keepPosition and self._lastPopupPoint is not None:
                self.move(QPoint(self._lastPopupPoint[0], self._lastPopupPoint[1]))
            else:
                mapToGlobal = getattr(anchorWidget, "mapToGlobal", None)
                width = getattr(anchorWidget, "width", None)
                if callable(mapToGlobal) and callable(width):
                    point = mapToGlobal(QPoint(width(), 0))
                    self.move(point)
                    pointX = getattr(point, "x", lambda: 0)()
                    pointY = getattr(point, "y", lambda: 0)()
                    self._lastPopupPoint = (int(pointX), int(pointY))
            screen = self.screen()
            if screen is not None:
                area = screen.availableGeometry().adjusted(12, 12, -12, -12)
                self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
                self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                          max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))
                self._lastPopupPoint = (self.x(), self.y())
            self.show()
            self.raise_()
            self.activateWindow()
            self.searchInput.setFocus(Qt.PopupFocusReason)

        def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                if watched is self.searchInput or self.isVisible():
                    event.accept()
                    self.close()
                    return True
            if event.type() == QEvent.MouseButtonPress and self.isVisible():
                if not self._isBubbleWidget(watched) and not self._isCategoryButton(
                    watched
                ):
                    self.close()
            if event.type() == QEvent.ApplicationDeactivate and self.isVisible():
                self.close()
            return bool(super().eventFilter(watched, event))

        def keyPressEvent(self, event) -> None:  # type: ignore[override]
            if event.key() == Qt.Key_Escape:
                event.accept()
                self.close()
                return
            super().keyPressEvent(event)

        def getPopupPosition(self) -> tuple[int, int] | None:
            return self._lastPopupPoint

        def _isBubbleWidget(self, watched: object) -> bool:
            if watched is self:
                return True
            return isinstance(watched, QWidget) and self.isAncestorOf(watched)

        def _isCategoryButton(self, watched: object) -> bool:
            objectName = getattr(watched, "objectName", None)
            if not callable(objectName):
                return False
            return objectName() in {"sideCategoryButton", "sideCategoryButtonActive"}

        def _refreshGrid(self) -> None:
            self._clearButtons()
            filteredOperators = self._filterAndSortOperators()
            self._visibleOperators = filteredOperators
            for index, payload in enumerate(filteredOperators):
                button = _OperatorCardButton(payload)
                button.clicked.connect(
                    lambda checked=False, current=payload: self._create(current)
                )
                row = index // self._columns
                col = index % self._columns
                self._grid.addWidget(button, row, col)
                self._buttons.append(button)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            columns = 2 if self.width() >= 500 else 1
            if columns != self._columns:
                self._columns = columns
                for index, button in enumerate(self._buttons):
                    self._grid.removeWidget(button)
                    self._grid.addWidget(button, index // columns, index % columns)

        def _filterAndSortOperators(self) -> list[dict[str, object]]:
            filtered: list[dict[str, object]] = []
            keyword = self._searchKeyword
            for payload in self._operators:
                if keyword == "":
                    filtered.append(payload)
                    continue
                displayName = str(payload.get("displayName", "")).lower()
                operatorId = str(payload.get("operatorId", "")).lower()
                if keyword in displayName or keyword in operatorId:
                    filtered.append(payload)

            recentIndex = {
                operatorId: index
                for index, operatorId in enumerate(self._recentOperatorIds)
            }

            def sortKey(payload: dict[str, object]) -> tuple[int, int, str]:
                operatorId = str(payload.get("operatorId", ""))
                if not self._recentEnabled:
                    return (1, 10_000, operatorId)
                if operatorId in recentIndex:
                    return (0, recentIndex[operatorId], operatorId)
                return (1, 10_000, operatorId)

            return sorted(filtered, key=sortKey)

        def _clearButtons(self) -> None:
            for button in self._buttons:
                self._grid.removeWidget(button)
                button.deleteLater()
            self._buttons = []

        def simulateCreate(self, index: int) -> None:
            if index < 0 or index >= len(self._visibleOperators):
                return
            self._create(self._visibleOperators[index])

        def _create(self, payload: dict[str, object]) -> None:
            if self._createHandler is not None:
                self._createHandler(payload)

    def _buildTooltip(payload: dict[str, object]) -> str:
        displayName = str(payload.get("displayName", ""))
        summary = str(payload.get("summary", ""))
        category = str(payload.get("category", ""))
        version = str(payload.get("version", ""))
        lines = [displayName, str(payload.get("operatorId", ""))]
        if summary != "":
            lines.append(summary)
        extra = []
        if category != "":
            extra.append(category)
        if version != "":
            extra.append(f"v{version}")
        if len(extra) > 0:
            lines.append(" | ".join(extra))
        return "\n".join(lines)

    def _getCardObjectName(category: str) -> str:
        if category == "预处理":
            return "operatorBubbleItemPre"
        if category == "检测":
            return "operatorBubbleItemDetect"
        if category == "测量":
            return "operatorBubbleItemMeasure"
        if category == "输出":
            return "operatorBubbleItemOutput"
        if category == "控制流":
            return "operatorBubbleItemFlow"
        return "operatorBubbleItem"

except Exception:  # pragma: no cover
    OPERATOR_MIME_TYPE = "application/x-emo-operator"

    class OperatorBubble:  # type: ignore[no-redef]
        def __init__(self, parent: object | None = None) -> None:
            self._parent = parent
            self._createHandler: CreateHandler | None = None
            self._operators: list[dict[str, object]] = []
            self._visibleOperators: list[dict[str, object]] = []
            self._searchKeyword = ""
            self._recentOperatorIds: list[str] = []
            self._recentEnabled = True
            self._lastPopupPoint: tuple[int, int] | None = None
            self._visible = False
            self._buttons: list[object] = []

        def parentWidget(self) -> object | None:
            return self._parent

        def setCreateHandler(self, handler: CreateHandler | None) -> None:
            self._createHandler = handler

        def setOperators(self, operators: list[dict[str, object]]) -> None:
            self._operators = list(operators)
            self._refreshVisible()

        def setRecentOperatorIds(self, operatorIds: list[str]) -> None:
            self._recentOperatorIds = [
                str(operatorId) for operatorId in operatorIds if str(operatorId) != ""
            ]
            self._refreshVisible()

        def setRecentEnabled(self, enabled: bool) -> None:
            self._recentEnabled = bool(enabled)
            self._refreshVisible()

        def setSearchKeyword(self, keyword: str) -> None:
            self._searchKeyword = keyword.strip().lower()
            self._refreshVisible()

        def getVisibleOperatorIds(self) -> list[str]:
            return [
                str(payload.get("operatorId", "")) for payload in self._visibleOperators
            ]

        def showAt(self, anchorWidget: object, keepPosition: bool = False) -> None:
            if not keepPosition or self._lastPopupPoint is None:
                mapToGlobal = getattr(anchorWidget, "mapToGlobal", None)
                width = getattr(anchorWidget, "width", None)
                if callable(mapToGlobal) and callable(width):
                    point = mapToGlobal(
                        type("Point", (), {"x": lambda: 0, "y": lambda: 0})()
                    )
                    pointX = getattr(point, "x", lambda: 0)()
                    pointY = getattr(point, "y", lambda: 0)()
                    self._lastPopupPoint = (int(pointX), int(pointY))
            self._visible = True

        def getPopupPosition(self) -> tuple[int, int] | None:
            return self._lastPopupPoint

        def close(self) -> None:
            self._visible = False

        def isVisible(self) -> bool:
            return self._visible

        def simulateCreate(self, index: int) -> None:
            if self._createHandler is None:
                return
            if index < 0 or index >= len(self._visibleOperators):
                return
            self._createHandler(self._visibleOperators[index])

        def _refreshVisible(self) -> None:
            keyword = self._searchKeyword
            filtered = [
                payload
                for payload in self._operators
                if keyword == ""
                or keyword in str(payload.get("displayName", "")).lower()
                or keyword in str(payload.get("operatorId", "")).lower()
            ]
            recentIndex = {
                operatorId: index
                for index, operatorId in enumerate(self._recentOperatorIds)
            }

            def sortKey(payload: dict[str, object]) -> tuple[int, int, str]:
                operatorId = str(payload.get("operatorId", ""))
                if not self._recentEnabled:
                    return (1, 10_000, operatorId)
                if operatorId in recentIndex:
                    return (0, recentIndex[operatorId], operatorId)
                return (1, 10_000, operatorId)

            self._visibleOperators = sorted(filtered, key=sortKey)
            self._buttons = [
                _FallbackOperatorButton(payload, self._createHandler)
                for payload in self._visibleOperators
            ]


    class _FallbackOperatorButton:
        def __init__(
            self,
            payload: dict[str, object],
            createHandler: CreateHandler | None,
        ) -> None:
            self._payload = payload
            self._createHandler = createHandler
            self._objectName = (
                "operatorBubbleItemFlow"
                if payload.get("category") == "控制流"
                else "operatorBubbleItem"
            )

        def objectName(self) -> str:
            return self._objectName

        def click(self) -> None:
            if self._createHandler is not None:
                self._createHandler(self._payload)
