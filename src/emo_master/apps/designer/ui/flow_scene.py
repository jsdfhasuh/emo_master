from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, cast

from emo_master.apps.designer.ui.operator_bubble import OPERATOR_MIME_TYPE


@dataclass
class FlowNodeViewModel:
    nodeId: str
    title: str
    x: float
    y: float
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    operatorId: str = ""


@dataclass(frozen=True)
class FlowEdgeViewModel:
    fromNodeId: str
    fromPort: str
    toNodeId: str
    toPort: str


ConnectionHandler = Callable[[str, str, str, str], FlowEdgeViewModel | None]
ConnectionErrorHandler = Callable[[str], None]
NodeDoubleClickHandler = Callable[[str], None]
CanvasClickHandler = Callable[[], None]
OperatorDropHandler = Callable[[dict[str, object], float, float], None]


try:
    from PySide2.QtCore import QPointF, QRectF, Qt
    from PySide2.QtGui import QBrush, QColor, QPainterPath, QPen, QTransform
    from PySide2.QtWidgets import (
        QGraphicsEllipseItem,
        QGraphicsItem,
        QGraphicsPathItem,
        QGraphicsRectItem,
        QGraphicsScene,
        QGraphicsSceneMouseEvent,
        QGraphicsSimpleTextItem,
    )

    class _NodeItem(QGraphicsRectItem):
        def __init__(self, model: FlowNodeViewModel, scene: object) -> None:
            super().__init__(QRectF(0.0, 0.0, 220.0, 92.0))
            self.model = model
            self._sceneRef = scene
            self.setPos(model.x, model.y)
            variant = self._resolveVariant(model)
            self._variant = variant
            self._runtimeState = "IDLE"
            self.setPen(QPen(self._getBorderColor(variant, self._runtimeState), 1.4))
            self.setBrush(QBrush(self._getFillColor(variant, self._runtimeState)))
            self.setFlag(QGraphicsItem.ItemIsMovable, True)
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
            self.setData(int(Qt.UserRole), model.nodeId)

            titleText = QGraphicsSimpleTextItem(model.title, self)
            titleText.setPos(8.0, 8.0)
            if variant == "if":
                branchText = QGraphicsSimpleTextItem("条件分支", self)
                branchText.setPos(8.0, 28.0)
            elif variant == "switch":
                branchText = QGraphicsSimpleTextItem("多路分支", self)
                branchText.setPos(8.0, 28.0)

        def getVisualStyle(self) -> dict[str, object]:
            return {
                "variant": self._variant,
                "runtimeState": self._runtimeState,
                "fill": self.brush().color().name(),
                "border": self.pen().color().name(),
            }

        def setRuntimeState(self, state: str) -> None:
            self._runtimeState = state
            self.setPen(QPen(self._getBorderColor(self._variant, state), 1.4))
            self.setBrush(QBrush(self._getFillColor(self._variant, state)))

        def _resolveVariant(self, model: FlowNodeViewModel) -> str:
            operatorId = str(getattr(model, "operatorId", ""))
            if operatorId == "vision.flow.if":
                return "if"
            if operatorId == "vision.flow.switch":
                return "switch"
            if operatorId != "":
                return "default"
            outputNames = set(model.outputPorts.keys())
            if outputNames == {"true", "false"}:
                return "if"
            if outputNames == {"case0", "case1", "case2", "case3", "default"}:
                return "switch"
            return "default"

        def _getFillColor(self, variant: str, runtimeState: str) -> QColor:
            if runtimeState == "RUNNING":
                return QColor("#d6f5ff")
            if runtimeState == "SKIPPED":
                return QColor("#f1f5f9")
            if runtimeState == "FAILED":
                return QColor("#fde2e2")
            if runtimeState == "COMPLETED":
                return QColor("#dcfce7")
            if variant == "if":
                return QColor("#fff7d6")
            if variant == "switch":
                return QColor("#efe4ff")
            return QColor("#dfe6e9")

        def _getBorderColor(self, variant: str, runtimeState: str) -> QColor:
            if runtimeState == "RUNNING":
                return QColor("#0284c7")
            if runtimeState == "SKIPPED":
                return QColor("#94a3b8")
            if runtimeState == "FAILED":
                return QColor("#dc2626")
            if runtimeState == "COMPLETED":
                return QColor("#16a34a")
            if variant == "if":
                return QColor("#f39c12")
            if variant == "switch":
                return QColor("#8e44ad")
            return QColor("#2d3436")

        def itemChange(
            self, change: QGraphicsItem.GraphicsItemChange, value: object
        ) -> object:
            result = super().itemChange(change, value)
            if change == QGraphicsItem.ItemPositionHasChanged:
                refreshEdgesForNode = getattr(
                    self._sceneRef, "refreshEdgesForNode", None
                )
                if refreshEdgesForNode is not None:
                    refreshEdgesForNode(self.model.nodeId)
            return result

        def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:
            handleNodeDoubleClick = getattr(
                self._sceneRef, "handleNodeDoubleClick", None
            )
            if handleNodeDoubleClick is not None:
                handleNodeDoubleClick(self.model.nodeId)
            super().mouseDoubleClickEvent(event)

    class _PortItem(QGraphicsEllipseItem):
        def __init__(
            self,
            nodeId: str,
            portName: str,
            portType: str,
            direction: str,
            scene: object,
            x: float,
            y: float,
        ) -> None:
            super().__init__(QRectF(0.0, 0.0, 12.0, 12.0))
            self.nodeId = nodeId
            self.portName = portName
            self.portType = portType
            self.direction = direction
            self._sceneRef = scene
            self.setPos(x, y)
            self.setPen(QPen(QColor("#2d3436"), 1.2))
            fillColor = (
                QColor("#00b894") if direction == "output" else QColor("#e17055")
            )
            self.setBrush(QBrush(fillColor))
            self.setAcceptHoverEvents(True)
            self._defaultFillColor = fillColor
            self._hoverFillColor = (
                QColor("#55efc4") if direction == "output" else QColor("#fab1a0")
            )
            self._snapFillColor = QColor("#0984e3")

        def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
            handlePortHover = getattr(self._sceneRef, "handlePortHover", None)
            if handlePortHover is not None:
                handlePortHover(self, True)
            super().hoverEnterEvent(event)

        def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
            handlePortHover = getattr(self._sceneRef, "handlePortHover", None)
            if handlePortHover is not None:
                handlePortHover(self, False)
            super().hoverLeaveEvent(event)

        def setHoverHint(self, enabled: bool) -> None:
            if enabled:
                self.setBrush(QBrush(self._hoverFillColor))
                self.setPen(QPen(QColor("#0984e3"), 1.8))
                return
            self.setBrush(QBrush(self._defaultFillColor))
            self.setPen(QPen(QColor("#2d3436"), 1.2))

        def setSnapHint(self, enabled: bool) -> None:
            if enabled:
                self.setBrush(QBrush(self._snapFillColor))
                self.setPen(QPen(QColor("#0652dd"), 2.2))
                return
            self.setHoverHint(False)

        def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
            if self.direction == "output":
                startConnectionDrag = getattr(
                    self._sceneRef, "startConnectionDrag", None
                )
                if startConnectionDrag is not None:
                    startConnectionDrag(self)
                event.accept()
                return
            super().mousePressEvent(event)

    class _EdgeItem(QGraphicsPathItem):
        def __init__(
            self, edge: FlowEdgeViewModel, sourcePort: _PortItem, targetPort: _PortItem
        ) -> None:
            super().__init__()
            self.edge = edge
            self.sourcePort = sourcePort
            self.targetPort = targetPort
            self.setPen(QPen(QColor("#0984e3"), 2.0))
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setData(
                int(Qt.UserRole),
                (edge.fromNodeId, edge.fromPort, edge.toNodeId, edge.toPort),
            )
            self._normalPen = QPen(QColor("#0984e3"), 2.0)
            self._selectedPen = QPen(QColor("#0652dd"), 3.2)
            self.refreshPath()

        def itemChange(
            self, change: QGraphicsItem.GraphicsItemChange, value: object
        ) -> object:
            result = super().itemChange(change, value)
            if change == QGraphicsItem.ItemSelectedHasChanged:
                self._applySelectionStyle(bool(value))
            return result

        def setSelectedStyle(self, selected: bool) -> None:
            self.setSelected(selected)
            self._applySelectionStyle(selected)

        def getStyle(self) -> dict[str, object]:
            pen = self.pen()
            return {"width": float(pen.widthF()), "color": str(pen.color().name())}

        def _applySelectionStyle(self, selected: bool) -> None:
            self.setPen(self._selectedPen if selected else self._normalPen)

        def refreshPath(self) -> None:
            start = self.sourcePort.sceneBoundingRect().center()
            end = self.targetPort.sceneBoundingRect().center()
            ctrl1 = QPointF(start.x() + 80.0, start.y())
            ctrl2 = QPointF(end.x() - 80.0, end.y())
            path = QPainterPath(start)
            path.cubicTo(ctrl1, ctrl2, end)
            self.setPath(path)

    class FlowScene(QGraphicsScene):
        def __init__(self) -> None:
            super().__init__()
            self._nodeItems: dict[str, _NodeItem] = {}
            self._portItems: dict[tuple[str, str, str], _PortItem] = {}
            self._edgeItems: dict[tuple[str, str, str, str], _EdgeItem] = {}
            self._inputEdgeIndex: dict[tuple[str, str], tuple[str, str, str, str]] = {}
            self._connectionHandler: ConnectionHandler | None = None
            self._nodeDoubleClickHandler: NodeDoubleClickHandler | None = None
            self._canvasClickHandler: CanvasClickHandler | None = None
            self._operatorDropHandler: OperatorDropHandler | None = None
            self._connectionErrorHandler: ConnectionErrorHandler | None = None
            self._dragHintText: str = ""
            self._dragSourceNodeId: str | None = None
            self._dragSourcePortName: str | None = None
            self._dragSourcePort: _PortItem | None = None
            self._dragPathItem: QGraphicsPathItem | None = None
            self._hoverPort: _PortItem | None = None
            self._snapTargetPort: _PortItem | None = None
            self._dragInvalidReason = ""
            self._dragSnapRadius = 26.0
            self._dragHintText = ""
            self._dragHintItem: QGraphicsSimpleTextItem | None = None

        def setConnectionHandler(self, handler: ConnectionHandler | None) -> None:
            self._connectionHandler = handler

        def setNodeDoubleClickHandler(
            self, handler: NodeDoubleClickHandler | None
        ) -> None:
            self._nodeDoubleClickHandler = handler

        def setCanvasClickHandler(self, handler: CanvasClickHandler | None) -> None:
            self._canvasClickHandler = handler

        def setOperatorDropHandler(self, handler: OperatorDropHandler | None) -> None:
            self._operatorDropHandler = handler

        def setConnectionErrorHandler(
            self, handler: ConnectionErrorHandler | None
        ) -> None:
            self._connectionErrorHandler = handler

        def clearGraph(self) -> None:
            self.clear()
            self._nodeItems = {}
            self._portItems = {}
            self._edgeItems = {}
            self._inputEdgeIndex = {}
            self._dragSourcePort = None
            self._dragPathItem = None
            self._hoverPort = None
            self._snapTargetPort = None
            self._dragInvalidReason = ""
            self._setDragHint("", None)

        def itemAtPoint(self, x: float, y: float):
            return self.itemAt(QPointF(float(x), float(y)), QTransform())

        def addFlowNode(self, model: FlowNodeViewModel) -> None:
            nodeItem = _NodeItem(model, self)
            self.addItem(nodeItem)
            self._nodeItems[model.nodeId] = nodeItem

            inputNames = list(model.inputPorts.items())
            outputNames = list(model.outputPorts.items())
            for index, (portName, portType) in enumerate(inputNames):
                portY = 34.0 + index * 20.0
                portItem = _PortItem(
                    model.nodeId, portName, portType, "input", self, 4.0, portY
                )
                portLabel = QGraphicsSimpleTextItem(portName, nodeItem)
                portLabel.setPos(20.0, portY - 2.0)
                portItem.setParentItem(nodeItem)
                self._portItems[(model.nodeId, "input", portName)] = portItem

            for index, (portName, portType) in enumerate(outputNames):
                portY = 34.0 + index * 20.0
                portItem = _PortItem(
                    model.nodeId, portName, portType, "output", self, 204.0, portY
                )
                portLabel = QGraphicsSimpleTextItem(portName, nodeItem)
                portLabel.setPos(132.0, portY - 2.0)
                portItem.setParentItem(nodeItem)
                self._portItems[(model.nodeId, "output", portName)] = portItem

        def renderEdge(self, edge: FlowEdgeViewModel) -> None:
            sourcePort = self._portItems.get((edge.fromNodeId, "output", edge.fromPort))
            targetPort = self._portItems.get((edge.toNodeId, "input", edge.toPort))
            if sourcePort is None or targetPort is None:
                return

            inputKey = (edge.toNodeId, edge.toPort)
            existingEdgeKey = self._inputEdgeIndex.get(inputKey)
            if existingEdgeKey is not None and existingEdgeKey in self._edgeItems:
                self.removeItem(self._edgeItems[existingEdgeKey])
                del self._edgeItems[existingEdgeKey]

            edgeKey = (edge.fromNodeId, edge.fromPort, edge.toNodeId, edge.toPort)
            edgeItem = _EdgeItem(edge, sourcePort, targetPort)
            self.addItem(edgeItem)
            self._edgeItems[edgeKey] = edgeItem
            self._inputEdgeIndex[inputKey] = edgeKey

        def refreshEdgesForNode(self, nodeId: str) -> None:
            for edgeItem in self._edgeItems.values():
                if (
                    edgeItem.edge.fromNodeId == nodeId
                    or edgeItem.edge.toNodeId == nodeId
                ):
                    edgeItem.refreshPath()

        def removeFlowNode(self, nodeId: str) -> None:
            edgeKeysToRemove = [
                edgeKey
                for edgeKey in self._edgeItems.keys()
                if edgeKey[0] == nodeId or edgeKey[2] == nodeId
            ]
            for edgeKey in edgeKeysToRemove:
                self.removeFlowEdge(*edgeKey)

            for key in list(self._portItems.keys()):
                if key[0] == nodeId:
                    del self._portItems[key]

            nodeItem = self._nodeItems.get(nodeId)
            if nodeItem is not None:
                self.removeItem(nodeItem)
                del self._nodeItems[nodeId]

        def removeFlowEdge(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> None:
            edgeKey = (fromNodeId, fromPort, toNodeId, toPort)
            edgeItem = self._edgeItems.get(edgeKey)
            if edgeItem is None:
                return
            self.removeItem(edgeItem)
            del self._edgeItems[edgeKey]
            inputKey = (toNodeId, toPort)
            existing = self._inputEdgeIndex.get(inputKey)
            if existing == edgeKey:
                del self._inputEdgeIndex[inputKey]

        def getSelectedEdgeKeys(self) -> list[tuple[str, str, str, str]]:
            selectedItems = self.selectedItems()
            edgeKeys: list[tuple[str, str, str, str]] = []
            for selectedItem in selectedItems:
                rawValue = selectedItem.data(int(Qt.UserRole))
                if (
                    isinstance(rawValue, tuple)
                    and len(rawValue) == 4
                    and all(isinstance(part, str) for part in rawValue)
                ):
                    edgeKeys.append(
                        (rawValue[0], rawValue[1], rawValue[2], rawValue[3])
                    )
            return edgeKeys

        def layoutNodesGrid(self, columns: int = 4) -> None:
            if columns <= 0:
                columns = 1
            nodeIds = list(self._nodeItems.keys())
            for index, nodeId in enumerate(nodeIds):
                nodeItem = self._nodeItems[nodeId]
                newX = float(20 + (index % columns) * 240)
                newY = float(20 + (index // columns) * 130)
                nodeItem.setPos(newX, newY)
                self.refreshEdgesForNode(nodeId)

        def startConnectionDrag(self, sourcePort: _PortItem) -> None:
            self._dragSourcePort = sourcePort
            self._dragInvalidReason = ""
            self._applyConnectableInputHighlight(sourcePort)
            self._setDragHint(
                "拖拽到输入端口以连接", sourcePort.sceneBoundingRect().center()
            )
            if self._dragPathItem is not None:
                self.removeItem(self._dragPathItem)
            pathItem = QGraphicsPathItem()
            pathItem.setPen(QPen(QColor("#74b9ff"), 2.0, Qt.DashLine))
            self.addItem(pathItem)
            self._dragPathItem = pathItem

        def handlePortHover(self, portItem: _PortItem, entered: bool) -> None:
            if portItem.direction != "output":
                return
            if self._dragSourcePort is not None:
                return
            self._clearInputHints()
            if entered:
                self._hoverPort = portItem
                self._applyConnectableInputHighlight(portItem)
            else:
                self._hoverPort = None

        def handleNodeDoubleClick(self, nodeId: str) -> None:
            if self._nodeDoubleClickHandler is None:
                return
            self._nodeDoubleClickHandler(nodeId)

        def simulateCanvasClick(self) -> None:
            if self._canvasClickHandler is None:
                return
            self._canvasClickHandler()

        def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
            if self._dragSourcePort is not None and self._dragPathItem is not None:
                start = self._dragSourcePort.sceneBoundingRect().center()
                end = event.scenePos()
                path = QPainterPath(start)
                path.cubicTo(
                    QPointF(start.x() + 80.0, start.y()),
                    QPointF(end.x() - 80.0, end.y()),
                    end,
                )
                self._dragPathItem.setPath(path)
                self._updateDragTargetState(end)
                self._moveDragHint(end)
            super().mouseMoveEvent(event)

        def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
            if self._dragSourcePort is None and self._canvasClickHandler is not None:
                itemsAtPos = self.items(event.scenePos())
                if len(itemsAtPos) == 0:
                    self._canvasClickHandler()
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
            if self._dragSourcePort is not None:
                targetPort = self._snapTargetPort
                if targetPort is None:
                    targetPort = self._findNearestInputPort(
                        event.scenePos(), self._dragSnapRadius
                    )
                if targetPort is not None and self._connectionHandler is not None:
                    preview = self.previewConnectionTarget(
                        self._dragSourcePort.nodeId,
                        self._dragSourcePort.portName,
                        targetPort.nodeId,
                        targetPort.portName,
                    )
                    isValid = (
                        bool(preview.get("valid", False))
                        if isinstance(preview, dict)
                        else False
                    )
                    if isValid:
                        createdEdge = self._connectionHandler(
                            self._dragSourcePort.nodeId,
                            self._dragSourcePort.portName,
                            targetPort.nodeId,
                            targetPort.portName,
                        )
                        if createdEdge is not None:
                            self.renderEdge(createdEdge)
                    else:
                        reason = (
                            str(preview.get("reason", "连线无效"))
                            if isinstance(preview, dict)
                            else "连线无效"
                        )
                        self._emitConnectionError(reason)
                elif self._dragInvalidReason != "":
                    self._emitConnectionError(self._dragInvalidReason)

                if self._dragPathItem is not None:
                    self.removeItem(self._dragPathItem)
                self._dragPathItem = None
                self._dragSourcePort = None
                self._snapTargetPort = None
                self._dragInvalidReason = ""
                self._clearInputHints()
                self._setDragHint("", None)
            super().mouseReleaseEvent(event)

        def dragEnterEvent(self, event) -> None:  # type: ignore[override]
            mimeData = event.mimeData()
            if mimeData is not None and mimeData.hasFormat(OPERATOR_MIME_TYPE):
                event.acceptProposedAction()
                return
            super().dragEnterEvent(event)

        def dragMoveEvent(self, event) -> None:  # type: ignore[override]
            mimeData = event.mimeData()
            if mimeData is not None and mimeData.hasFormat(OPERATOR_MIME_TYPE):
                event.acceptProposedAction()
                return
            super().dragMoveEvent(event)

        def dropEvent(self, event) -> None:  # type: ignore[override]
            mimeData = event.mimeData()
            if mimeData is None or not mimeData.hasFormat(OPERATOR_MIME_TYPE):
                super().dropEvent(event)
                return
            if self._operatorDropHandler is None:
                super().dropEvent(event)
                return

            rawPayload = bytes(mimeData.data(OPERATOR_MIME_TYPE)).decode("utf-8")
            try:
                payload = json.loads(rawPayload)
            except json.JSONDecodeError:
                super().dropEvent(event)
                return
            if not isinstance(payload, dict):
                super().dropEvent(event)
                return

            scenePos = event.scenePos()
            self._operatorDropHandler(payload, float(scenePos.x()), float(scenePos.y()))
            event.acceptProposedAction()

        def getSelectedNodeId(self) -> str | None:
            selectedNodeIds = self.getSelectedNodeIds()
            if len(selectedNodeIds) != 1:
                return None
            return selectedNodeIds[0]

        def getSelectedNodeIds(self) -> list[str]:
            selectedItems = self.selectedItems()
            nodeIds: list[str] = []
            for selectedItem in selectedItems:
                nodeId = selectedItem.data(int(Qt.UserRole))
                if isinstance(nodeId, str):
                    nodeIds.append(nodeId)
            return nodeIds

        def hasNode(self, nodeId: str) -> bool:
            return nodeId in self._nodeItems

        def getNodePositions(self) -> dict[str, tuple[float, float]]:
            positions: dict[str, tuple[float, float]] = {}
            for nodeId, nodeItem in self._nodeItems.items():
                point = nodeItem.pos()
                positions[nodeId] = (float(point.x()), float(point.y()))
            return positions

        def getNodeCenter(self, nodeId: str) -> tuple[float, float] | None:
            nodeItem = self._nodeItems.get(nodeId)
            if nodeItem is None:
                return None
            center = nodeItem.sceneBoundingRect().center()
            return (float(center.x()), float(center.y()))

        def getNodeVisualStyle(self, nodeId: str) -> dict[str, object]:
            nodeItem = self._nodeItems.get(nodeId)
            if nodeItem is None:
                return {}
            return nodeItem.getVisualStyle()

        def setNodeRuntimeState(self, nodeId: str, state: str) -> None:
            nodeItem = self._nodeItems.get(nodeId)
            if nodeItem is None:
                return
            nodeItem.setRuntimeState(state)

        def getContentBounds(self) -> tuple[float, float, float, float] | None:
            if len(self._nodeItems) == 0:
                return None
            minX = 0.0
            minY = 0.0
            maxX = 0.0
            maxY = 0.0
            initialized = False
            for nodeItem in self._nodeItems.values():
                rect = nodeItem.sceneBoundingRect()
                if not initialized:
                    minX = float(rect.left())
                    minY = float(rect.top())
                    maxX = float(rect.right())
                    maxY = float(rect.bottom())
                    initialized = True
                    continue
                minX = min(minX, float(rect.left()))
                minY = min(minY, float(rect.top()))
                maxX = max(maxX, float(rect.right()))
                maxY = max(maxY, float(rect.bottom()))
            padding = 80.0
            return (
                minX - padding,
                minY - padding,
                (maxX - minX) + padding * 2.0,
                (maxY - minY) + padding * 2.0,
            )

        def setNodeSelected(self, nodeId: str) -> None:
            for currentNodeId, nodeItem in self._nodeItems.items():
                nodeItem.setSelected(currentNodeId == nodeId)

        def drawBackground(self, painter, rect: QRectF) -> None:  # type: ignore[override]
            painter.fillRect(rect, QColor("#f5f6fa"))

        def simulateOperatorDrop(
            self, payload: dict[str, object], x: float, y: float
        ) -> None:
            if self._operatorDropHandler is None:
                return
            self._operatorDropHandler(payload, x, y)

        def describeConnectionAttempt(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> str:
            sourcePort = self._portItems.get((fromNodeId, "output", fromPort))
            if sourcePort is None:
                return f"无效输出端口：{fromNodeId}.{fromPort}"
            targetPort = self._portItems.get((toNodeId, "input", toPort))
            if targetPort is None:
                return f"无效输入端口：{toNodeId}.{toPort}"
            if sourcePort.portType != targetPort.portType:
                return (
                    "端口类型不匹配："
                    f"{fromNodeId}.{fromPort}({sourcePort.portType}) -> {toNodeId}.{toPort}({targetPort.portType})"
                )
            return ""

        def previewConnectionTarget(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> dict[str, object]:
            reason = self.describeConnectionAttempt(
                fromNodeId, fromPort, toNodeId, toPort
            )
            if reason != "":
                return {
                    "valid": False,
                    "reason": reason,
                    "targetKey": (toNodeId, "input", toPort),
                }
            return {
                "valid": True,
                "reason": "",
                "targetKey": (toNodeId, "input", toPort),
            }

        def previewDragAt(
            self, fromNodeId: str, fromPort: str, x: float, y: float
        ) -> dict[str, object]:
            sourcePort = self._portItems.get((fromNodeId, "output", fromPort))
            if sourcePort is None:
                return {
                    "valid": False,
                    "reason": f"无效输出端口：{fromNodeId}.{fromPort}",
                    "targetKey": None,
                }
            nearestPort = self._findNearestInputPort(
                QPointF(float(x), float(y)), self._dragSnapRadius
            )
            if nearestPort is None:
                return {
                    "valid": False,
                    "reason": "未找到可连接输入端口",
                    "targetKey": None,
                }
            return self.previewConnectionTarget(
                sourcePort.nodeId,
                sourcePort.portName,
                nearestPort.nodeId,
                nearestPort.portName,
            )

        def beginDragPreview(self, fromNodeId: str, fromPort: str) -> bool:
            sourcePort = self._portItems.get((fromNodeId, "output", fromPort))
            if sourcePort is None:
                return False
            self._dragSourcePort = sourcePort
            self._applyConnectableInputHighlight(sourcePort)
            self._setDragHint(
                "拖拽到输入端口以连接", sourcePort.sceneBoundingRect().center()
            )
            return True

        def updateDragPreview(self, x: float, y: float) -> str:
            if self._dragSourcePort is None:
                return ""
            targetPoint = QPointF(float(x), float(y))
            self._updateDragTargetState(targetPoint)
            self._moveDragHint(targetPoint)
            return self._dragHintText

        def endDragPreview(self) -> None:
            self._dragSourcePort = None
            self._snapTargetPort = None
            self._dragInvalidReason = ""
            self._clearInputHints()
            self._setDragHint("", None)

        def getDragHintText(self) -> str:
            return self._dragHintText

        def setEdgeSelected(
            self, edgeKey: tuple[str, str, str, str], selected: bool
        ) -> None:
            edgeItem = self._edgeItems.get(edgeKey)
            if edgeItem is None:
                return
            edgeItem.setSelectedStyle(selected)

        def getEdgeStyle(self, edgeKey: tuple[str, str, str, str]) -> dict[str, object]:
            edgeItem = self._edgeItems.get(edgeKey)
            if edgeItem is None:
                return {}
            return edgeItem.getStyle()

        def _applyConnectableInputHighlight(self, sourcePort: _PortItem) -> None:
            self._clearInputHints()
            for portItem in self._portItems.values():
                if portItem.direction != "input":
                    continue
                isCompatible = (
                    sourcePort.portType == portItem.portType
                    and sourcePort.nodeId != portItem.nodeId
                )
                portItem.setHoverHint(isCompatible)

        def _clearInputHints(self) -> None:
            for portItem in self._portItems.values():
                if portItem.direction == "input":
                    portItem.setHoverHint(False)

        def _updateDragTargetState(self, position: QPointF) -> None:
            if self._dragSourcePort is None:
                return
            targetPort = self._findNearestInputPort(position, self._dragSnapRadius)
            if targetPort is None:
                self._snapTargetPort = None
                self._dragInvalidReason = ""
                self._applyConnectableInputHighlight(self._dragSourcePort)
                self._setDragHint("未找到可连接输入端口", position)
                return

            preview = self.previewConnectionTarget(
                self._dragSourcePort.nodeId,
                self._dragSourcePort.portName,
                targetPort.nodeId,
                targetPort.portName,
            )
            isValid = (
                bool(preview.get("valid", False))
                if isinstance(preview, dict)
                else False
            )
            self._dragInvalidReason = (
                str(preview.get("reason", "")) if isinstance(preview, dict) else ""
            )
            self._applyConnectableInputHighlight(self._dragSourcePort)
            if isValid:
                targetPort.setSnapHint(True)
                self._snapTargetPort = targetPort
                self._setDragHint(
                    f"可连接：{targetPort.nodeId}.{targetPort.portName}",
                    position,
                )
            else:
                targetPort.setHoverHint(True)
                self._snapTargetPort = None
                hintText = (
                    self._dragInvalidReason
                    if self._dragInvalidReason != ""
                    else "连线无效"
                )
                self._setDragHint(hintText, position)

        def _setDragHint(self, text: str, position: QPointF | None) -> None:
            self._dragHintText = text
            if text == "":
                if self._dragHintItem is not None:
                    self.removeItem(self._dragHintItem)
                    self._dragHintItem = None
                return
            if self._dragHintItem is None:
                self._dragHintItem = QGraphicsSimpleTextItem()
                self.addItem(self._dragHintItem)
            hintItem = cast(QGraphicsSimpleTextItem, self._dragHintItem)
            if hintItem is None:
                return
            hintItem.setText(text)
            if position is not None:
                self._moveDragHint(position)

        def _moveDragHint(self, position: QPointF) -> None:
            if self._dragHintItem is None:
                return
            self._dragHintItem.setPos(position.x() + 14.0, position.y() + 12.0)

        def _emitConnectionError(self, reason: str) -> None:
            if reason == "":
                return
            if self._connectionErrorHandler is None:
                return
            self._connectionErrorHandler(reason)

        def _findInputPortAt(self, position: QPointF) -> _PortItem | None:
            itemsAtPos = self.items(position)
            for item in itemsAtPos:
                if isinstance(item, _PortItem) and item.direction == "input":
                    return item
            return None

        def _findNearestInputPort(
            self, position: QPointF, radius: float
        ) -> _PortItem | None:
            nearest: _PortItem | None = None
            minDistanceSquared = radius * radius
            for portItem in self._portItems.values():
                if portItem.direction != "input":
                    continue
                center = portItem.sceneBoundingRect().center()
                dx = center.x() - position.x()
                dy = center.y() - position.y()
                distanceSquared = dx * dx + dy * dy
                if distanceSquared > minDistanceSquared:
                    continue
                minDistanceSquared = distanceSquared
                nearest = portItem
            return nearest

except Exception:  # pragma: no cover

    class FlowScene:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._nodes: dict[str, FlowNodeViewModel] = {}
            self._edges: list[FlowEdgeViewModel] = []
            self._selectedNodeId: str | None = None
            self._connectionHandler: ConnectionHandler | None = None
            self._nodeDoubleClickHandler: NodeDoubleClickHandler | None = None
            self._canvasClickHandler: CanvasClickHandler | None = None
            self._operatorDropHandler: OperatorDropHandler | None = None
            self._connectionErrorHandler: ConnectionErrorHandler | None = None
            self._dragHintText: str = ""
            self._dragSourceNodeId: str | None = None
            self._dragSourcePortName: str | None = None
            self._sceneRect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

        def setSceneRect(self, x: float, y: float, width: float, height: float) -> None:
            self._sceneRect = (float(x), float(y), float(width), float(height))

        def sceneRect(self):
            class _Rect:
                def __init__(self, rect: tuple[float, float, float, float]) -> None:
                    self._rect = rect

                def width(self) -> float:
                    return float(self._rect[2])

                def height(self) -> float:
                    return float(self._rect[3])

            return _Rect(self._sceneRect)

        def setConnectionHandler(self, handler: ConnectionHandler | None) -> None:
            self._connectionHandler = handler

        def setNodeDoubleClickHandler(
            self, handler: NodeDoubleClickHandler | None
        ) -> None:
            self._nodeDoubleClickHandler = handler

        def setCanvasClickHandler(self, handler: CanvasClickHandler | None) -> None:
            self._canvasClickHandler = handler

        def setOperatorDropHandler(self, handler: OperatorDropHandler | None) -> None:
            self._operatorDropHandler = handler

        def setConnectionErrorHandler(
            self, handler: ConnectionErrorHandler | None
        ) -> None:
            self._connectionErrorHandler = handler

        def clearGraph(self) -> None:
            self._nodes = {}
            self._edges = []
            self._selectedNodeId = None
            self._dragHintText = ""
            self._dragSourceNodeId = None
            self._dragSourcePortName = None

        def itemAtPoint(self, x: float, y: float):
            for node in self._nodes.values():
                if (
                    float(node.x) <= float(x) <= float(node.x) + 220.0
                    and float(node.y) <= float(y) <= float(node.y) + 92.0
                ):
                    return node
            return None

        def addFlowNode(self, model: FlowNodeViewModel) -> None:
            self._nodes[model.nodeId] = model
            self._selectedNodeId = model.nodeId

        def renderEdge(self, edge: FlowEdgeViewModel) -> None:
            self._edges = [
                existing
                for existing in self._edges
                if not (
                    existing.toNodeId == edge.toNodeId
                    and existing.toPort == edge.toPort
                )
            ]
            self._edges.append(edge)

        def removeFlowNode(self, nodeId: str) -> None:
            if nodeId in self._nodes:
                del self._nodes[nodeId]
            self._edges = [
                edge
                for edge in self._edges
                if edge.fromNodeId != nodeId and edge.toNodeId != nodeId
            ]
            if self._selectedNodeId == nodeId:
                self._selectedNodeId = None

        def removeFlowEdge(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> None:
            self._edges = [
                edge
                for edge in self._edges
                if not (
                    edge.fromNodeId == fromNodeId
                    and edge.fromPort == fromPort
                    and edge.toNodeId == toNodeId
                    and edge.toPort == toPort
                )
            ]

        def getSelectedEdgeKeys(self) -> list[tuple[str, str, str, str]]:
            return []

        def layoutNodesGrid(self, columns: int = 4) -> None:
            if columns <= 0:
                columns = 1
            nodeIds = list(self._nodes.keys())
            for index, nodeId in enumerate(nodeIds):
                current = self._nodes[nodeId]
                self._nodes[nodeId] = FlowNodeViewModel(
                    nodeId=current.nodeId,
                    title=current.title,
                    x=float(20 + (index % columns) * 240),
                    y=float(20 + (index // columns) * 130),
                    inputPorts=current.inputPorts,
                    outputPorts=current.outputPorts,
                    operatorId=current.operatorId,
                )

        def getSelectedNodeId(self) -> str | None:
            return self._selectedNodeId

        def getSelectedNodeIds(self) -> list[str]:
            if self._selectedNodeId is None:
                return []
            return [self._selectedNodeId]

        def hasNode(self, nodeId: str) -> bool:
            return nodeId in self._nodes

        def getNodePositions(self) -> dict[str, tuple[float, float]]:
            positions: dict[str, tuple[float, float]] = {}
            for nodeId, node in self._nodes.items():
                positions[nodeId] = (float(node.x), float(node.y))
            return positions

        def getNodeCenter(self, nodeId: str) -> tuple[float, float] | None:
            node = self._nodes.get(nodeId)
            if node is None:
                return None
            return (float(node.x) + 110.0, float(node.y) + 46.0)

        def getNodeVisualStyle(self, nodeId: str) -> dict[str, object]:
            node = self._nodes.get(nodeId)
            if node is None:
                return {}
            if node.operatorId == "vision.flow.if":
                return {"variant": "if"}
            if node.operatorId == "vision.flow.switch":
                return {"variant": "switch"}
            if node.operatorId != "":
                return {"variant": "default"}
            if set(node.outputPorts.keys()) == {"true", "false"}:
                return {"variant": "if"}
            if set(node.outputPorts.keys()) == {"case0", "case1", "case2", "case3", "default"}:
                return {"variant": "switch"}
            return {"variant": "default"}

        def getContentBounds(self) -> tuple[float, float, float, float] | None:
            if len(self._nodes) == 0:
                return None
            minX = 0.0
            minY = 0.0
            maxX = 0.0
            maxY = 0.0
            initialized = False
            for node in self._nodes.values():
                left = float(node.x)
                top = float(node.y)
                right = left + 220.0
                bottom = top + 92.0
                if not initialized:
                    minX = left
                    minY = top
                    maxX = right
                    maxY = bottom
                    initialized = True
                    continue
                minX = min(minX, left)
                minY = min(minY, top)
                maxX = max(maxX, right)
                maxY = max(maxY, bottom)
            padding = 80.0
            return (
                minX - padding,
                minY - padding,
                (maxX - minX) + padding * 2.0,
                (maxY - minY) + padding * 2.0,
            )

        def setNodeSelected(self, nodeId: str) -> None:
            if nodeId not in self._nodes:
                return
            self._selectedNodeId = nodeId

        def simulateDragConnection(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> FlowEdgeViewModel | None:
            if self._connectionHandler is None:
                return None
            createdEdge = self._connectionHandler(
                fromNodeId, fromPort, toNodeId, toPort
            )
            if createdEdge is not None:
                self.renderEdge(createdEdge)
            return createdEdge

        def simulateNodeDoubleClick(self, nodeId: str) -> None:
            if self._nodeDoubleClickHandler is None:
                return
            self._nodeDoubleClickHandler(nodeId)

        def simulateCanvasClick(self) -> None:
            if self._canvasClickHandler is None:
                return
            self._canvasClickHandler()

        def simulateOperatorDrop(
            self, payload: dict[str, object], x: float, y: float
        ) -> None:
            if self._operatorDropHandler is None:
                return
            self._operatorDropHandler(payload, x, y)

        def describeConnectionAttempt(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> str:
            sourceNode = self._nodes.get(fromNodeId)
            if sourceNode is None or fromPort not in sourceNode.outputPorts:
                return f"无效输出端口：{fromNodeId}.{fromPort}"
            targetNode = self._nodes.get(toNodeId)
            if targetNode is None or toPort not in targetNode.inputPorts:
                return f"无效输入端口：{toNodeId}.{toPort}"
            sourceType = sourceNode.outputPorts[fromPort]
            targetType = targetNode.inputPorts[toPort]
            if sourceType != targetType:
                return f"端口类型不匹配：{fromNodeId}.{fromPort}({sourceType}) -> {toNodeId}.{toPort}({targetType})"
            return ""

        def previewConnectionTarget(
            self, fromNodeId: str, fromPort: str, toNodeId: str, toPort: str
        ) -> dict[str, object]:
            reason = self.describeConnectionAttempt(
                fromNodeId, fromPort, toNodeId, toPort
            )
            return {
                "valid": reason == "",
                "reason": reason,
                "targetKey": (toNodeId, "input", toPort),
            }

        def previewDragAt(
            self, fromNodeId: str, fromPort: str, x: float, y: float
        ) -> dict[str, object]:
            sourceNode = self._nodes.get(fromNodeId)
            if sourceNode is None or fromPort not in sourceNode.outputPorts:
                return {
                    "valid": False,
                    "reason": f"无效输出端口：{fromNodeId}.{fromPort}",
                    "targetKey": None,
                }

            bestMatch: tuple[str, str] | None = None
            bestDistanceSquared = 26.0 * 26.0
            for nodeId, node in self._nodes.items():
                if nodeId == fromNodeId:
                    continue
                inputNames = list(node.inputPorts.keys())
                for index, portName in enumerate(inputNames):
                    portX = node.x + 10.0
                    portY = node.y + 40.0 + float(index * 20)
                    dx = portX - float(x)
                    dy = portY - float(y)
                    distanceSquared = dx * dx + dy * dy
                    if distanceSquared > bestDistanceSquared:
                        continue
                    bestDistanceSquared = distanceSquared
                    bestMatch = (nodeId, portName)

            if bestMatch is None:
                return {
                    "valid": False,
                    "reason": "未找到可连接输入端口",
                    "targetKey": None,
                }

            return self.previewConnectionTarget(
                fromNodeId,
                fromPort,
                bestMatch[0],
                bestMatch[1],
            )

        def beginDragPreview(self, fromNodeId: str, fromPort: str) -> bool:
            sourceNode = self._nodes.get(fromNodeId)
            if sourceNode is None or fromPort not in sourceNode.outputPorts:
                return False
            self._dragSourceNodeId = fromNodeId
            self._dragSourcePortName = fromPort
            self._dragHintText = "拖拽到输入端口以连接"
            return True

        def updateDragPreview(self, x: float, y: float) -> str:
            if self._dragSourceNodeId is None or self._dragSourcePortName is None:
                return ""
            preview = self.previewDragAt(
                self._dragSourceNodeId,
                self._dragSourcePortName,
                x,
                y,
            )
            if bool(preview.get("valid", False)):
                targetKey = preview.get("targetKey")
                if isinstance(targetKey, tuple) and len(targetKey) == 3:
                    self._dragHintText = f"可连接：{targetKey[0]}.{targetKey[2]}"
                    return self._dragHintText
            self._dragHintText = str(preview.get("reason", ""))
            return self._dragHintText

        def endDragPreview(self) -> None:
            self._dragSourceNodeId = None
            self._dragSourcePortName = None
            self._dragHintText = ""

        def getDragHintText(self) -> str:
            return self._dragHintText

        def setEdgeSelected(
            self, edgeKey: tuple[str, str, str, str], selected: bool
        ) -> None:
            _ = edgeKey
            _ = selected

        def getEdgeStyle(self, edgeKey: tuple[str, str, str, str]) -> dict[str, object]:
            _ = edgeKey
            return {"width": 2.0, "color": "#0984e3"}
