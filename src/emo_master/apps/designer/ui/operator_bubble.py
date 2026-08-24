from __future__ import annotations

import json
from typing import Callable

from emo_master.apps.designer.ui.icon_map import getOperatorGlyph


CreateHandler = Callable[[dict[str, object]], None]


try:
    from PySide2.QtCore import QByteArray, QMimeData, QPoint, Qt
    from PySide2.QtGui import QDrag
    from PySide2.QtWidgets import (
        QApplication,
        QGridLayout,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    OPERATOR_MIME_TYPE = "application/x-emo-operator"

    class _OperatorCardButton(QPushButton):
        def __init__(self, payload: dict[str, object]) -> None:
            displayName = str(payload.get("displayName", ""))
            iconKey = str(payload.get("iconKey", "default"))
            operatorId = str(payload.get("operatorId", ""))
            super().__init__(
                f"{getOperatorGlyph(iconKey)}  {displayName}\n{operatorId}"
            )
            self.payload = payload
            self._dragStartPos: QPoint | None = None
            self.setObjectName(_getCardObjectName(str(payload.get("category", "其他"))))
            self.setToolTip(_buildTooltip(payload))

        def mousePressEvent(self, event) -> None:  # type: ignore[override]
            self._dragStartPos = event.pos()
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
            mimeData = QMimeData()
            payloadText = json.dumps(self.payload, ensure_ascii=False)
            mimeData.setData(
                OPERATOR_MIME_TYPE, QByteArray(payloadText.encode("utf-8"))
            )
            drag.setMimeData(mimeData)
            drag.setPixmap(self.grab())
            drag.exec_(Qt.CopyAction)
            self._dragStartPos = None

    class OperatorBubble(QWidget):
        def __init__(self) -> None:
            super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
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

            self.searchInput = QLineEdit()
            self.searchInput.setPlaceholderText("搜索算子（名称/ID）")
            self.searchInput.textChanged.connect(self.setSearchKeyword)

            rootLayout = QVBoxLayout()
            rootLayout.addWidget(self.searchInput)
            rootLayout.addLayout(self._grid)
            self.setLayout(rootLayout)
            self.setObjectName("operatorBubble")

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
            self.show()
            self.raise_()
            self.activateWindow()

        def getPopupPosition(self) -> tuple[int, int] | None:
            return self._lastPopupPoint

        def _refreshGrid(self) -> None:
            self._clearButtons()
            filteredOperators = self._filterAndSortOperators()
            self._visibleOperators = filteredOperators
            for index, payload in enumerate(filteredOperators):
                button = _OperatorCardButton(payload)
                row = index // 2
                col = index % 2
                self._grid.addWidget(button, row, col)
                self._buttons.append(button)

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
            if self._createHandler is None:
                return
            if index < 0 or index >= len(self._visibleOperators):
                return
            self._createHandler(self._visibleOperators[index])

    def _buildTooltip(payload: dict[str, object]) -> str:
        displayName = str(payload.get("displayName", ""))
        summary = str(payload.get("summary", ""))
        category = str(payload.get("category", ""))
        version = str(payload.get("version", ""))
        lines = [displayName]
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
        def __init__(self) -> None:
            self._createHandler: CreateHandler | None = None
            self._operators: list[dict[str, object]] = []
            self._visibleOperators: list[dict[str, object]] = []
            self._searchKeyword = ""
            self._recentOperatorIds: list[str] = []
            self._recentEnabled = True
            self._lastPopupPoint: tuple[int, int] | None = None
            self._visible = False
            self._buttons: list[object] = []

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
                _FallbackOperatorButton(payload) for payload in self._visibleOperators
            ]


    class _FallbackOperatorButton:
        def __init__(self, payload: dict[str, object]) -> None:
            self._objectName = (
                "operatorBubbleItemFlow"
                if payload.get("category") == "控制流"
                else "operatorBubbleItem"
            )

        def objectName(self) -> str:
            return self._objectName
