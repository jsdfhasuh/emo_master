from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Callable, cast

from emo_master.apps.designer.ui.operator_bubble import OPERATOR_MIME_TYPE
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.apps.designer.ui.node_geometry import gridPositions, measureNode
from emo_master.apps.designer.ui.flow_layout import LayoutNode, flowPositions
from emo_master.apps.designer.ui.flow_routing import OrthogonalRouter, Rect, Route, RouteRequest


@dataclass
class FlowNodeViewModel:
    nodeId: str
    title: str
    x: float
    y: float
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    operatorId: str = ""
    kind: str = "operator"
    summaryLines: tuple[tuple[str, str | None], ...] = ()
    outputPortLabels: dict[str, str] = field(default_factory=dict)
    inputPortLabels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FlowEdgeViewModel:
    fromNodeId: str
    fromPort: str
    toNodeId: str
    toPort: str


ConnectionHandler = Callable[[str, str, str, str], FlowEdgeViewModel | None]
ConnectionErrorHandler = Callable[[str], None]
NodeDoubleClickHandler = Callable[[str], None]
NodeContextMenuHandler = Callable[[str, int, int], None]
CanvasClickHandler = Callable[[], None]
OperatorDropHandler = Callable[[dict[str, object], float, float], None]


try:
    from PySide2.QtCore import QPointF, QRectF, Qt, QTimer
    from shiboken2 import isValid
    from PySide2.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPen, QTransform
    from emo_master.apps.designer.ui.theme import uiFont
    from emo_master.apps.designer.ui.icon_map import operatorIcon
    from PySide2.QtWidgets import (
        QGraphicsEllipseItem,
        QGraphicsItem,
        QGraphicsPathItem,
        QGraphicsRectItem,
        QGraphicsScene,
        QGraphicsSceneContextMenuEvent,
        QGraphicsSceneMouseEvent,
        QGraphicsSimpleTextItem,
    )

    class _WorkflowReferenceText(QGraphicsSimpleTextItem):
        def __init__(self, text, parent, workflowId, scene):
            super().__init__(text, parent)
            self.workflowId = workflowId
            self.sceneRef = scene
            self.setCursor(Qt.PointingHandCursor)

        def mousePressEvent(self, event):
            event.accept()

        def mouseDoubleClickEvent(self, event):
            if event.button() == Qt.LeftButton and self.sceneRef.workflowOpenHandler:
                # Navigation replaces the current scene; wait until this event returns.
                handler, workflowId = self.sceneRef.workflowOpenHandler, self.workflowId
                QTimer.singleShot(0, lambda: handler(workflowId))
            event.accept()

    class _NodeItem(QGraphicsRectItem):
        def __init__(self, model: FlowNodeViewModel, scene: object) -> None:
            self.bodyFont = uiFont()
            self.titleFont = uiFont(bold=True)
            metrics = QFontMetricsF(self.bodyFont)
            self.geometry = measureNode(model, metrics.horizontalAdvance,
                                        QFontMetricsF(self.titleFont).horizontalAdvance, metrics.height())
            super().__init__(QRectF(0.0, 0.0, self.geometry.width, self.geometry.height))
            self.model = model
            self._sceneRef = scene
            self.setPos(model.x, model.y)
            variant = self._resolveVariant(model)
            self._variant = variant
            self._runtimeState = "IDLE"
            self._bindingSource = False
            self._sqliteSummary = None
            self._operatorIcon = operatorIcon("default" if getattr(model, "kind", "operator") == "operator" else "flow")
            self.setPen(QPen(self._getBorderColor(variant, self._runtimeState), 1.4))
            self.setBrush(QBrush(self._getFillColor(variant, self._runtimeState)))
            self.setFlag(QGraphicsItem.ItemIsMovable, True)
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
            self.setData(int(Qt.UserRole), model.nodeId)

            title = QFontMetricsF(self.titleFont).elidedText(model.title, Qt.ElideRight, self.geometry.width - 60)
            titleText = QGraphicsSimpleTextItem(title, self)
            titleText.setFont(self.titleFont)
            titleText.setBrush(QColor("#20242b"))
            tooltip = model.title
            if model.kind in {"workflow_input", "workflow_output"}:
                side = "输入" if model.kind == "workflow_input" else "输出"
                tooltip += f"\n双击配置工作流{side}接口"
            titleText.setToolTip(tooltip)
            titleText.setPos(44.0, 10.0)
            titleText.setAcceptedMouseButtons(Qt.NoButton)
            self.setToolTip(tooltip)
            if getattr(model, 'operatorId', '') == 'vision.io.sqlite_writer':
                self._sqliteSummary = QGraphicsSimpleTextItem('未配置目标 · 0 个映射', self)
                self._sqliteSummary.setFont(self.bodyFont)
                self._sqliteSummary.setBrush(QColor('#b45309'))
                self._sqliteSummary.setPos(16, self.geometry.header - metrics.height() - 6)
                self._sqliteSummary.setAcceptedMouseButtons(Qt.NoButton)
            for index, (text, workflowId) in enumerate(model.summaryLines):
                shown = metrics.elidedText(text, Qt.ElideRight, self.geometry.width - 32)
                if workflowId:
                    summary = _WorkflowReferenceText(shown, self, workflowId, scene)
                else:
                    summary = QGraphicsSimpleTextItem(shown, self)
                    summary.setAcceptedMouseButtons(Qt.NoButton)
                summary.setFont(self.bodyFont)
                summary.setBrush(QColor("#2563eb" if workflowId else "#626b78"))
                summary.setToolTip(text + (f"\n工作流 ID：{workflowId}\n双击打开工作流" if workflowId else ""))
                summary.setPos(16, 14 + metrics.height() + index * (metrics.height() + 4))
            if variant == "if" and not model.summaryLines:
                branchText = QGraphicsSimpleTextItem("条件分支", self)
                branchText.setFont(self.bodyFont)
                branchText.setPos(16.0, 14.0 + metrics.height())
            elif variant == "switch" and not model.summaryLines:
                branchText = QGraphicsSimpleTextItem("多路分支", self)
                branchText.setFont(self.bodyFont)
                branchText.setPos(16.0, 14.0 + metrics.height())

        def paint(self, painter, option, widget=None):
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(self.brush())
            painter.setPen(QPen(QColor("#2563eb"), 2.0) if self.isSelected() else
                           QPen(QColor('#8b5cf6'), 2.5) if self._bindingSource else self.pen())
            painter.drawRoundedRect(self.rect(), 6.0, 6.0)
            self._operatorIcon.paint(painter, 16, 10, 20, 20)
            painter.setPen(QPen(QColor("#e5e7eb"), 1.0))
            painter.drawLine(QPointF(1, self.geometry.header),
                             QPointF(self.geometry.width - 1, self.geometry.header))

        def setOperatorIcon(self, icon) -> None:
            self._operatorIcon = icon
            self.update(QRectF(16, 10, 20, 20))

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
            return QColor("#ffffff")

        def _getBorderColor(self, variant: str, runtimeState: str) -> QColor:
            if runtimeState == "RUNNING":
                return QColor("#0284c7")
            if runtimeState == "SKIPPED":
                return QColor("#94a3b8")
            if runtimeState == "FAILED":
                return QColor("#dc2626")
            if runtimeState == "COMPLETED":
                return QColor("#16a34a")
            return QColor("#bdc5d1")

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
                if self.model.kind in {"workflow_input", "workflow_output"}:
                    # Leave the native item event before a modal can replace it.
                    # Its release may go to the modal, so end the canvas grab.
                    nodeId = self.model.nodeId
                    def openInterface() -> None:
                        if not isValid(self) or self.scene() is not self._sceneRef:
                            return
                        if self._sceneRef.mouseGrabberItem() is self:
                            self.ungrabMouse()
                        handleNodeDoubleClick(nodeId)
                    QTimer.singleShot(0, openInterface)
                else:
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
            self.setPen(QPen(QColor("#2563eb"), 1.2))
            fillColor = (
                QColor("#2563eb") if direction == "output" else QColor("#ffffff")
            )
            self.setBrush(QBrush(fillColor))
            self.setAcceptHoverEvents(True)
            self._defaultFillColor = fillColor
            self._hoverFillColor = (
                QColor("#60a5fa") if direction == "output" else QColor("#dbeafe")
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
            self.setPen(QPen(QColor("#2563eb"), 1.2))

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
            self.route = Route(())
            self.setZValue(-1)

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
            pen = QPen(self._selectedPen if selected else self._normalPen)
            if self.route.blocked:
                pen.setColor(QColor("#dc6b17"))
                pen.setStyle(Qt.DashLine)
            self.setPen(pen)

        def refreshPath(self) -> None:
            scene = self.scene()
            if isinstance(scene, FlowScene):
                scene._routeEdges([self])

        def applyRoute(self, route: Route) -> None:
            self.route = route
            self.setToolTip(route.reason)
            path = QPainterPath()
            if route.points:
                path.moveTo(QPointF(*route.points[0]))
                for index in range(1, len(route.points) - 1):
                    before, corner, after = route.points[index - 1:index + 2]
                    incoming = abs(corner[0] - before[0]) + abs(corner[1] - before[1])
                    outgoing = abs(after[0] - corner[0]) + abs(after[1] - corner[1])
                    radius = min(8.0, incoming / 2, outgoing / 2)
                    entry = QPointF(corner[0] + (before[0] - corner[0]) * radius / incoming,
                                    corner[1] + (before[1] - corner[1]) * radius / incoming)
                    exitPoint = QPointF(corner[0] + (after[0] - corner[0]) * radius / outgoing,
                                        corner[1] + (after[1] - corner[1]) * radius / outgoing)
                    path.lineTo(entry)
                    path.quadTo(QPointF(*corner), exitPoint)
                path.lineTo(QPointF(*route.points[-1]))
            self.setPath(path)
            self._applySelectionStyle(self.isSelected())

    class FlowScene(QGraphicsScene):
        def __init__(self) -> None:
            super().__init__()
            self._routeBatch = False
            self._routeTimer = QTimer(self)
            self._routeTimer.setSingleShot(True)
            self._routeTimer.timeout.connect(self.refreshAllRoutes)
            self._nodeItems: dict[str, _NodeItem] = {}
            self.presentationProvider: Callable[[FlowNodeViewModel], FlowNodeViewModel] | None = None
            self.workflowOpenHandler: Callable[[str], None] | None = None
            self._bindingItems: list[QGraphicsPathItem] = []
            self._bindingTarget = None
            self._bindingSources = ()
            self._iconProvider = None
            self._iconContext: Callable[[], tuple] = lambda: ()
            self._portItems: dict[tuple[str, str, str], _PortItem] = {}
            self._edgeItems: dict[tuple[str, str, str, str], _EdgeItem] = {}
            self._inputEdgeIndex: dict[tuple[str, str], tuple[str, str, str, str]] = {}
            self._connectionHandler: ConnectionHandler | None = None
            self._nodeDoubleClickHandler: NodeDoubleClickHandler | None = None
            self._nodeContextMenuHandler: NodeContextMenuHandler | None = None
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

        def setIconProvider(self, provider, contextSupplier: Callable[[], tuple]) -> None:
            if self._iconProvider is not None:
                for item in self._nodeItems.values():
                    self._iconProvider.unbind(item)
            self._iconProvider = provider
            self._iconContext = contextSupplier
            self.rebindOperatorIcons()

        def rebindOperatorIcons(self) -> None:
            for item in self._nodeItems.values():
                self._bindOperatorIcon(item)

        def _bindOperatorIcon(self, item: _NodeItem) -> None:
            if self._iconProvider is not None and getattr(item.model, "kind", "operator") == "operator":
                self._iconProvider.bind(item, getattr(item.model, "operatorId", ""),
                                        context=(*self._iconContext(), item.model.nodeId))

        def setConnectionHandler(self, handler: ConnectionHandler | None) -> None:
            self._connectionHandler = handler

        def setNodeDoubleClickHandler(
            self, handler: NodeDoubleClickHandler | None
        ) -> None:
            self._nodeDoubleClickHandler = handler

        def setCanvasClickHandler(self, handler: CanvasClickHandler | None) -> None:
            self._canvasClickHandler = handler

        def setNodeContextMenuHandler(self, handler: NodeContextMenuHandler | None) -> None:
            self._nodeContextMenuHandler = handler

        def setOperatorDropHandler(self, handler: OperatorDropHandler | None) -> None:
            self._operatorDropHandler = handler

        def setConnectionErrorHandler(
            self, handler: ConnectionErrorHandler | None
        ) -> None:
            self._connectionErrorHandler = handler

        def clear(self) -> None:
            self.clearGraph()

        def clearGraph(self) -> None:
            hadSelection = bool(self.selectedItems())
            self._routeTimer.stop()
            self._dragHintItem = None
            if self._iconProvider is not None:
                for item in self._nodeItems.values():
                    self._iconProvider.unbind(item)
            # Native selectionChanged is synchronous during clear(). Consumers
            # must never inspect wrappers for items Qt has already destroyed.
            previous = self.blockSignals(True)
            try:
                super().clear()
            finally:
                self.blockSignals(previous)
            self._nodeItems = {}
            self._bindingItems = []
            self._bindingTarget = None
            self._bindingSources = ()
            self._portItems = {}
            self._edgeItems = {}
            self._inputEdgeIndex = {}
            self._dragSourcePort = None
            self._dragPathItem = None
            self._hoverPort = None
            self._snapTargetPort = None
            self._dragInvalidReason = ""
            self._setDragHint("", None)
            if hadSelection and not previous:
                self.selectionChanged.emit()

        def itemAtPoint(self, x: float, y: float):
            return self.itemAt(QPointF(float(x), float(y)), QTransform())

        def addFlowNode(self, model: FlowNodeViewModel) -> None:
            if not isinstance(model, FlowNodeViewModel):
                model = FlowNodeViewModel(
                    model.nodeId, model.title, model.x, model.y, model.inputPorts, model.outputPorts,
                    getattr(model, "operatorId", ""), getattr(model, "kind", "operator"),
                )
            if self.presentationProvider is not None:
                model = self.presentationProvider(model)
            nodeItem = _NodeItem(model, self)
            self.addItem(nodeItem)
            self._nodeItems[model.nodeId] = nodeItem
            self._bindOperatorIcon(nodeItem)

            geometry = nodeItem.geometry
            metrics = QFontMetricsF(nodeItem.bodyFont)
            for direction, ports in (("input", model.inputPorts), ("output", model.outputPorts)):
                for index, (portName, portType) in enumerate(ports.items()):
                    centerY = geometry.header + (index + 0.5) * geometry.rowHeight
                    x = 6.0 if direction == "input" else geometry.width - 18.0
                    portItem = _PortItem(model.nodeId, portName, portType, direction, self, x, centerY - 6)
                    portItem.setParentItem(nodeItem)
                    portItem.setToolTip(f"{portName}: {portType}")
                    available = geometry.inputWidth if direction == "input" else geometry.outputWidth
                    labels = model.outputPortLabels if direction == "output" else model.inputPortLabels
                    displayName = labels.get(portName, portName)
                    label = QGraphicsSimpleTextItem(metrics.elidedText(displayName, Qt.ElideRight, available), nodeItem)
                    label.setFont(nodeItem.bodyFont)
                    label.setBrush(QColor("#475569"))
                    label.setToolTip(f"{displayName}\n{portName}: {portType}")
                    if direction == "output" and model.operatorId == "vision.analysis.blob" and portName == "overlay":
                        hint = label.toolTip() + "\n可选输出：仅启用 drawOverlay（绘制叠加图）时产生；nullable 不代表允许缺失。"
                        label.setToolTip(hint)
                        portItem.setToolTip(hint)
                    if direction == "input" and portName in model.inputPortLabels:
                        tip = label.toolTip() + "\n初始值来自此连线；后续每轮判断循环体回传的同名布尔值"
                        label.setToolTip(tip)
                        portItem.setToolTip(tip)
                    rect = label.boundingRect()
                    labelX = 28.0 if direction == "input" else geometry.width - 28.0 - rect.width()
                    label.setPos(labelX, centerY - rect.height() / 2)
                    self._portItems[(model.nodeId, direction, portName)] = portItem
            self._scheduleRoutes()

        def refreshPresentations(self) -> None:
            if self.presentationProvider is None:
                return
            previous = self.blockSignals(True)
            try:
                for nodeId, old in list(self._nodeItems.items()):
                    model = self.presentationProvider(old.model)
                    if model == old.model:
                        continue
                    model = replace(model, x=old.pos().x(), y=old.pos().y())
                    selected, state = old.isSelected(), old._runtimeState
                    if self._iconProvider is not None:
                        self._iconProvider.unbind(old)
                    self.removeItem(old)
                    self.addFlowNode(model)
                    item = self._nodeItems[nodeId]
                    item.setSelected(selected)
                    item.setRuntimeState(state)
                    item._bindingSource = old._bindingSource
                    for edge in self._edgeItems.values():
                        if edge.edge.fromNodeId == nodeId:
                            edge.sourcePort = self._portItems[(nodeId, "output", edge.edge.fromPort)]
                        if edge.edge.toNodeId == nodeId:
                            edge.targetPort = self._portItems[(nodeId, "input", edge.edge.toPort)]
            finally:
                self.blockSignals(previous)
            self._scheduleRoutes()

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
            self._scheduleRoutes()

        def _scheduleRoutes(self) -> None:
            if not self._routeBatch and self.mouseGrabberItem() is None:
                self._routeTimer.start(0)

        def _routeEdges(self, items: list[_EdgeItem]) -> None:
            if not items:
                return
            nodes = {}
            for key, item in self._nodeItems.items():
                rect = item.mapRectToScene(item.rect())
                nodes[key] = Rect(rect.left(), rect.top(), rect.right(), rect.bottom())
            requests = []
            for item in items:
                start = item.sourcePort.sceneBoundingRect().center()
                end = item.targetPort.sceneBoundingRect().center()
                edge = item.edge
                requests.append(RouteRequest((edge.fromNodeId, edge.fromPort, edge.toNodeId, edge.toPort),
                                             (start.x(), start.y()), (end.x(), end.y())))
            routes = OrthogonalRouter(nodes, requests).routeAll()
            for item, request in zip(items, requests):
                item.applyRoute(routes[request.key])

        def refreshAllRoutes(self) -> None:
            self._routeTimer.stop()
            self._routeEdges(list(self._edgeItems.values()))
            self._refreshBindingPaths()

        def refreshEdgesForNode(self, nodeId: str) -> None:
            self._refreshBindingPaths()
            if self._routeBatch:
                return
            if self.mouseGrabberItem() is not None:
                self._routeEdges([item for item in self._edgeItems.values()
                                  if nodeId in (item.edge.fromNodeId, item.edge.toNodeId)])
            else:
                self._scheduleRoutes()

        def setSqliteHints(self, nodeId, summary, error=''):
            item = self._nodeItems.get(nodeId)
            if item is None or item._sqliteSummary is None:
                return
            full = summary + (' · 配置错误：' + error if error else '')
            metrics = QFontMetricsF(item.bodyFont)
            item._sqliteSummary.setText(metrics.elidedText(full, Qt.ElideRight, item.geometry.width - 32))
            item._sqliteSummary.setBrush(QColor('#c0392b' if error else '#475569'))
            item.setToolTip(item.model.title + '\n' + full)

        def setBindingDependencies(self, target, sources):
            sources = tuple(dict.fromkeys(s for s in sources if s in self._nodeItems))
            if self._bindingTarget == target and self._bindingSources == sources:
                self._refreshBindingPaths()
                return
            for item in self._bindingItems:
                self.removeItem(item)
            self._bindingItems = []
            self._bindingTarget = target
            self._bindingSources = sources
            for key, item in self._nodeItems.items():
                item._bindingSource = key in self._bindingSources
                item.update()
            if target in self._nodeItems:
                for _source in self._bindingSources:
                    item = QGraphicsPathItem()
                    item.setPen(QPen(QColor('#8b5cf6'), 1.5, Qt.DashLine))
                    item.setAcceptedMouseButtons(Qt.NoButton)
                    item.setZValue(-.5)
                    item.setToolTip('字段映射依赖 · 临时提示，不是画布连线')
                    self.addItem(item)
                    self._bindingItems.append(item)
            self._refreshBindingPaths()

        def _refreshBindingPaths(self):
            target = self._nodeItems.get(self._bindingTarget)
            if target is None:
                return
            end = target.sceneBoundingRect().center()
            for source, item in zip(self._bindingSources, self._bindingItems):
                node = self._nodeItems.get(source)
                if node is None:
                    item.setPath(QPainterPath())
                    continue
                start = node.sceneBoundingRect().center()
                path = QPainterPath(start)
                middle = (start.x() + end.x()) / 2
                path.cubicTo(QPointF(middle, start.y()), QPointF(middle, end.y()), end)
                item.setPath(path)

        def removeFlowNode(self, nodeId: str) -> None:
            nodeItem = self._nodeItems.get(nodeId)
            if nodeItem is not None and nodeItem.model.kind in {
                "workflow_input",
                "workflow_output",
            }:
                return
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

            if nodeItem is not None:
                if self._iconProvider is not None:
                    self._iconProvider.unbind(nodeItem)
                self.removeItem(nodeItem)
                del self._nodeItems[nodeId]
                self._scheduleRoutes()

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
            self._scheduleRoutes()

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
            self._applyPositions(gridPositions({key: item.geometry for key, item in self._nodeItems.items()}, columns))

        def _applyPositions(self, positions: dict[str, tuple[float, float]]) -> None:
            self._routeTimer.stop()
            self._routeBatch = True
            try:
                for nodeId, (x, y) in positions.items():
                    self._nodeItems[nodeId].setPos(x, y)
            finally:
                self._routeBatch = False
                self.refreshAllRoutes()

        def layoutNodesFlow(self) -> None:
            positions = flowPositions(
                {key: LayoutNode(item.geometry.width, item.geometry.height, item.model.kind)
                 for key, item in self._nodeItems.items()},
                [(edge[0], edge[2]) for edge in self._edgeItems])
            self._applyPositions(positions)

        def nextNodePosition(self) -> tuple[float, float]:
            bottom = max((item.sceneBoundingRect().bottom() for item in self._nodeItems.values()), default=-28)
            return (20.0, float(bottom + 48))

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

        def handleNodeContextMenu(self, nodeId: str, screenX: int, screenY: int) -> bool:
            if self._nodeContextMenuHandler is None or not self.hasNode(nodeId):
                return False
            self._nodeContextMenuHandler(nodeId, screenX, screenY)
            return True

        def contextMenuEvent(self, event: QGraphicsSceneContextMenuEvent) -> None:
            nodeId = self.getSelectedNodeId() if event.reason() == QGraphicsSceneContextMenuEvent.Keyboard else None
            if nodeId is None and event.reason() != QGraphicsSceneContextMenuEvent.Keyboard:
                for item in self.items(event.scenePos()):
                    current = item
                    while current is not None and not isinstance(current, _NodeItem):
                        current = current.parentItem()
                    if isinstance(current, _NodeItem):
                        nodeId = current.model.nodeId
                        break
                    if isinstance(item, _EdgeItem):
                        break
            point = event.screenPos()
            if nodeId is not None and self.handleNodeContextMenu(nodeId, point.x(), point.y()):
                event.accept()
                return
            super().contextMenuEvent(event)

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
            self.refreshAllRoutes()
            callback = getattr(self, "editCompleted", None)
            if callback is not None:
                callback()

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

        def resetRuntimeStates(self) -> None:
            for nodeItem in self._nodeItems.values():
                nodeItem.setRuntimeState("IDLE")

        def setAllNodeRuntimeStates(self, state: str) -> None:
            for nodeItem in self._nodeItems.values():
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
            painter.fillRect(rect, QColor("#f5f6f8"))

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
            if not arePortTypesCompatible(sourcePort.portType, targetPort.portType):
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
                    arePortTypesCompatible(sourcePort.portType, portItem.portType)
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
            self._nodeContextMenuHandler: NodeContextMenuHandler | None = None
            self._canvasClickHandler: CanvasClickHandler | None = None
            self._operatorDropHandler: OperatorDropHandler | None = None
            self._connectionErrorHandler: ConnectionErrorHandler | None = None
            self._dragHintText: str = ""
            self._dragSourceNodeId: str | None = None
            self._dragSourcePortName: str | None = None
            self._sceneRect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
            self._nodeRuntimeStates: dict[str, str] = {}
            self._selectedEdges: set[tuple[str, str, str, str]] = set()

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

        def setNodeContextMenuHandler(self, handler: NodeContextMenuHandler | None) -> None:
            self._nodeContextMenuHandler = handler

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
            self._nodeRuntimeStates = {}
            self._dragHintText = ""
            self._dragSourceNodeId = None
            self._dragSourcePortName = None

        def itemAtPoint(self, x: float, y: float):
            for node in self._nodes.values():
                geometry = measureNode(node)
                if (
                    float(node.x) <= float(x) <= float(node.x) + geometry.width
                    and float(node.y) <= float(y) <= float(node.y) + geometry.height
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
            node = self._nodes.get(nodeId)
            if node is not None and node.kind in {"workflow_input", "workflow_output"}:
                return
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

        def layoutNodesFlow(self) -> None:
            positions = flowPositions(
                {key: LayoutNode(measureNode(node).width, measureNode(node).height, node.kind)
                 for key, node in self._nodes.items()},
                [(edge.fromNodeId, edge.toNodeId) for edge in self._edges])
            for nodeId, (x, y) in positions.items():
                self._nodes[nodeId] = replace(self._nodes[nodeId], x=x, y=y)

        def layoutNodesGrid(self, columns: int = 4) -> None:
            positions = gridPositions({key: measureNode(node) for key, node in self._nodes.items()}, columns)
            for nodeId, (x, y) in positions.items():
                current = self._nodes[nodeId]
                self._nodes[nodeId] = FlowNodeViewModel(
                    nodeId=current.nodeId,
                    title=current.title,
                    x=x,
                    y=y,
                    inputPorts=current.inputPorts,
                    outputPorts=current.outputPorts,
                    operatorId=current.operatorId,
                    kind=current.kind,
                )

        def nextNodePosition(self) -> tuple[float, float]:
            bottom = max((node.y + measureNode(node).height for node in self._nodes.values()), default=-28)
            return (20.0, float(bottom + 48))

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
            geometry = measureNode(node)
            return (float(node.x) + geometry.width / 2, float(node.y) + geometry.height / 2)

        def getNodeVisualStyle(self, nodeId: str) -> dict[str, object]:
            node = self._nodes.get(nodeId)
            if node is None:
                return {}
            runtimeState = self._nodeRuntimeStates.get(nodeId, "IDLE")
            if node.operatorId == "vision.flow.if":
                return {"variant": "if", "runtimeState": runtimeState}
            if node.operatorId == "vision.flow.switch":
                return {"variant": "switch", "runtimeState": runtimeState}
            if node.operatorId != "":
                return {"variant": "default", "runtimeState": runtimeState}
            if set(node.outputPorts.keys()) == {"true", "false"}:
                return {"variant": "if", "runtimeState": runtimeState}
            if set(node.outputPorts.keys()) == {"case0", "case1", "case2", "case3", "default"}:
                return {"variant": "switch", "runtimeState": runtimeState}
            return {"variant": "default", "runtimeState": runtimeState}

        def setNodeRuntimeState(self, nodeId: str, state: str) -> None:
            if nodeId in self._nodes:
                self._nodeRuntimeStates[nodeId] = state

        def resetRuntimeStates(self) -> None:
            self._nodeRuntimeStates = {}

        def setAllNodeRuntimeStates(self, state: str) -> None:
            self._nodeRuntimeStates = {
                nodeId: state for nodeId in self._nodes
            }

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
                geometry = measureNode(node)
                right = left + geometry.width
                bottom = top + geometry.height
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

        def handleNodeContextMenu(self, nodeId: str, screenX: int, screenY: int) -> bool:
            if self._nodeContextMenuHandler is None or not self.hasNode(nodeId):
                return False
            self._nodeContextMenuHandler(nodeId, screenX, screenY)
            return True

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
            if not arePortTypesCompatible(sourceType, targetType):
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
            if selected:
                self._selectedEdges.add(edgeKey)
            else:
                self._selectedEdges.discard(edgeKey)

        def getEdgeStyle(self, edgeKey: tuple[str, str, str, str]) -> dict[str, object]:
            return {
                "width": 3.2 if edgeKey in self._selectedEdges else 2.0,
                "color": "#0652dd" if edgeKey in self._selectedEdges else "#0984e3",
            }
