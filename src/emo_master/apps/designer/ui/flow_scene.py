from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from time import perf_counter
from collections.abc import Iterator
from typing import Callable, cast

from emo_master.apps.designer.ui.operator_bubble import OPERATOR_MIME_TYPE
from emo_master.core.contracts.port_compatibility import arePortTypesCompatible
from emo_master.apps.designer.ui.node_geometry import gridPositions, measureNode
from emo_master.apps.designer.ui.flow_layout import LayoutEdge, LayoutNode, flowPositions
from emo_master.apps.designer.ui.flow_routing import EdgeKey, OrthogonalRouter, Rect, Route, RouteRequest, simplify
from emo_master.apps.designer.ui.flow_edge_presentation import (
    SELECTED_COLOR, FOCUS_COLOR, WireGeometry, WirePart, edgeStyle, focusEdges, wireGeometry,
)


_SELECTED_EDGE_COLOR = SELECTED_COLOR


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
    from PySide2.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
    from shiboken2 import isValid
    from PySide2.QtGui import QBrush, QColor, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF, QTransform
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
        QMenu,
    )

    def _partPath(part: WirePart) -> QPainterPath:
        path = QPainterPath(QPointF(*part.points[0]))
        if len(part.points) == 3:
            path.quadTo(QPointF(*part.points[1]), QPointF(*part.points[2]))
        else:
            path.lineTo(QPointF(*part.points[1]))
        return path

    def _stroke(path: QPainterPath) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(8.0)
        return stroker.createStroke(path)

    class _WireLayer(QGraphicsPathItem):
        def __init__(self) -> None:
            super().__init__()
            self.setAcceptedMouseButtons(Qt.NoButton)

        def shape(self):
            return QPainterPath()

        def paint(self, painter, option, widget=None):
            painter.setPen(self.pen())
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.path())

    class _BranchMenu(QMenu):
        def event(self, event):
            if event.type() == QEvent.ShortcutOverride and event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                event.accept()
                return True
            return super().event(event)

        def keyPressEvent(self, event):
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                event.accept()
                return
            super().keyPressEvent(event)

    class _SharedWireItem(QGraphicsPathItem):
        def __init__(self, scene, path, owners) -> None:
            super().__init__(path)
            self.sceneRef = scene
            self.owners = frozenset(owners)
            self._hitPath = _stroke(path)
            self.setAcceptHoverEvents(True)
            self.setZValue(-.2)
            self.setCursor(Qt.PointingHandCursor)
            source = next(iter(self.owners))
            self.setToolTip(f"{scene._edgeLabel(source, sourceOnly=True)}\n{len(self.owners)} 条支路")

        def shape(self):
            return self._hitPath

        def boundingRect(self):
            return self._hitPath.boundingRect()

        def paint(self, painter, option, widget=None):
            pass

        def hoverEnterEvent(self, event):
            self.sceneRef._setWireHover(self.owners)
            super().hoverEnterEvent(event)

        def hoverLeaveEvent(self, event):
            self.sceneRef._setWireHover(None)
            super().hoverLeaveEvent(event)

        def mousePressEvent(self, event):
            event.accept()
            if event.button() == Qt.LeftButton:
                self.sceneRef._showEdgeMenu(self.owners, event.screenPos(), event.modifiers())

        def mouseReleaseEvent(self, event):
            event.accept()

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

        def boundingRect(self):
            # Reserve the selection ring without enlarging the port's hit shape.
            return super().boundingRect().adjusted(-3, -3, 3, 3)

        def paint(self, painter, option, widget=None):
            super().paint(painter, option, widget)
            scene = self.scene()
            color = scene._portEmphasis.get((self.nodeId, self.direction, self.portName)) if isinstance(scene, FlowScene) else None
            if color is not None:
                painter.setPen(QPen(QColor(color), 1.5))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(self.rect().adjusted(-2.5, -2.5, 2.5, 2.5))

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
            self.setPen(QPen(QColor(edgeStyle()[0]), edgeStyle()[1]))
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setData(
                int(Qt.UserRole),
                (edge.fromNodeId, edge.fromPort, edge.toNodeId, edge.toPort),
            )
            self.setAcceptHoverEvents(True)
            self.route = Route(())
            self._displayPath = QPainterPath()
            self._hitPath = QPainterPath()
            self._focused = False
            self.setZValue(-1)

        def paint(self, painter, option, widget=None):
            # QGraphicsPathItem also paints a dashed bounding rectangle when
            # selected. A routed edge can span most of the graph: highlight the
            # actual line instead, not a misleading box around unrelated nodes.
            painter.setPen(self.pen())
            painter.setBrush(self.brush())
            painter.drawPath(self._displayPath)
            if (self.isSelected() or self._focused) and len(self.route.points) >= 2:
                a, b = self.route.points[-2:]
                length = abs(b[0] - a[0]) + abs(b[1] - a[1])
                if length >= 12:
                    dx, dy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
                    tip = QPointF(b[0] - dx * 7, b[1] - dy * 7)
                    base = tip - QPointF(dx * 6, dy * 6)
                    painter.setBrush(self.pen().color())
                    painter.drawPolygon(QPolygonF([tip, base + QPointF(-dy * 3, dx * 3),
                                                    base + QPointF(dy * 3, -dx * 3)]))

        def shape(self):
            # The default shape includes the implicitly closed path interior,
            # even with NoBrush. That steals clicks from large blank regions
            # and prevents canvas panning inside a long bent connection.
            return self._hitPath

        def boundingRect(self):
            return self.path().boundingRect().adjusted(-5, -5, 5, 5)

        def itemChange(
            self, change: QGraphicsItem.GraphicsItemChange, value: object
        ) -> object:
            result = super().itemChange(change, value)
            if change == QGraphicsItem.ItemSelectedHasChanged and not isinstance(self.scene(), FlowScene):
                self._applySelectionStyle(bool(value))
            return result

        def setSelectedStyle(self, selected: bool) -> None:
            self.setSelected(selected)

        def hoverEnterEvent(self, event) -> None:  # type: ignore[override]
            scene = self.scene()
            if isinstance(scene, FlowScene):
                scene._setWireHover((self.data(int(Qt.UserRole)),))
            super().hoverEnterEvent(event)

        def hoverLeaveEvent(self, event) -> None:  # type: ignore[override]
            scene = self.scene()
            if isinstance(scene, FlowScene):
                scene._setWireHover(None)
            super().hoverLeaveEvent(event)

        def getStyle(self) -> dict[str, object]:
            pen = self.pen()
            return {"width": float(pen.widthF()), "color": str(pen.color().name())}

        def _applySelectionStyle(self, selected: bool) -> None:
            scene = self.scene()
            if isinstance(scene, FlowScene):
                scene._refreshEdgePresentation()
            else:
                color, width, _ = edgeStyle(selected=selected, blocked=self.route.blocked)
                self.setPen(QPen(QColor(color), width, Qt.DashLine if self.route.blocked else Qt.SolidLine))

        def _setDisplayPath(self, path) -> None:
            self.prepareGeometryChange()
            self._displayPath = path
            self._hitPath = _stroke(path)
            self.update()

        def refreshPath(self) -> None:
            scene = self.scene()
            if isinstance(scene, FlowScene):
                scene._routeEdges([self])

        def applyRoute(self, route: Route) -> None:
            self.route = route
            scene = self.scene()
            key = self.data(int(Qt.UserRole))
            label = scene._edgeLabel(key) if isinstance(scene, FlowScene) else " -> ".join((".".join(key[:2]), ".".join(key[2:])))
            self.setToolTip(label + ("\n" + route.reason if route.reason else ""))
            path = QPainterPath()
            points = simplify(route.points)
            if points:
                path.moveTo(QPointF(*points[0]))
                for index in range(1, len(points) - 1):
                    before, corner, after = points[index - 1:index + 2]
                    incoming = abs(corner[0] - before[0]) + abs(corner[1] - before[1])
                    outgoing = abs(after[0] - corner[0]) + abs(after[1] - corner[1])
                    radius = min(8.0, incoming / 2, outgoing / 2)
                    entry = QPointF(corner[0] + (before[0] - corner[0]) * radius / incoming,
                                    corner[1] + (before[1] - corner[1]) * radius / incoming)
                    exitPoint = QPointF(corner[0] + (after[0] - corner[0]) * radius / outgoing,
                                        corner[1] + (after[1] - corner[1]) * radius / outgoing)
                    path.lineTo(entry)
                    path.quadTo(QPointF(*corner), exitPoint)
                path.lineTo(QPointF(*points[-1]))
            self.setPath(path)
            self._setDisplayPath(path)
            if isinstance(scene, FlowScene):
                scene._wireGeometryChanged()
            else:
                self._applySelectionStyle(self.isSelected())

    class FlowScene(QGraphicsScene):
        def __init__(self) -> None:
            super().__init__()
            self._routeBatch = False
            self._routesDirty = False
            self._mouseEditChanged = False
            self._routeTimer = QTimer(self)
            self._routeTimer.setSingleShot(True)
            self._routeTimer.timeout.connect(self.refreshAllRoutes)
            self._routeRefineTimer = QTimer(self)
            self._routeRefineTimer.setSingleShot(True)
            self._routeRefineTimer.timeout.connect(self._continueRouteRefinement)
            self._routeRefinement: Iterator[tuple[EdgeKey, Route] | None] | None = None
            self._refinedRoutes: dict[EdgeKey, Route] = {}
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
            self._wireGeometry = WireGeometry((), ())
            self._wirePaths: list[tuple[WirePart, QPainterPath]] = []
            self._wireLayers: dict[tuple, _WireLayer] = {}
            self._sharedWireItems: list[_SharedWireItem] = []
            self._wireBatch = False
            self._wireEpoch = 0
            self._hoverEdgeKeys: frozenset[EdgeKey] | None = None
            self._menuPreview: frozenset[EdgeKey] | None = None
            self._portEmphasis: dict[tuple[str, str, str], str] = {}
            self._focusedEdges: frozenset[EdgeKey] = frozenset()
            self._branchMenu: QMenu | None = None
            self._presentationSignature: tuple[frozenset[EdgeKey], frozenset[EdgeKey], bool] | None = None
            self.selectionChanged.connect(self._refreshEdgePresentation)

        def _edgeLabel(self, key, sourceOnly=False) -> str:
            def endpoint(nodeId, direction, portName):
                node = self._nodeItems.get(nodeId)
                title = node.model.title if node is not None else nodeId
                labels = (node.model.outputPortLabels if direction == "output" else node.model.inputPortLabels) if node else {}
                label = labels.get(portName, portName)
                return f"{title} [{nodeId}].{label}" + (f" ({portName})" if label != portName else "")
            source = endpoint(key[0], "output", key[1])
            return source if sourceOnly else source + " -> " + endpoint(key[2], "input", key[3])

        def _setWireHover(self, keys) -> None:
            if self._dragSourcePort is not None or self._branchMenu is not None:
                return
            self._hoverEdgeKeys = frozenset(keys) if keys is not None else None
            self._refreshEdgePresentation()

        def _refreshEdgePresentation(self) -> None:
            selected = frozenset(self.getSelectedEdgeKeys())
            port = None
            if self._hoverPort is not None and self._hoverPort.scene() is self:
                port = (self._hoverPort.nodeId, self._hoverPort.direction, self._hoverPort.portName)
            preview = self._menuPreview if self._menuPreview is not None else self._hoverEdgeKeys
            self._focusedEdges = focusEdges(self._edgeItems, self.getSelectedNodeIds(), selected,
                                           port if self._dragSourcePort is None else None,
                                           preview if self._dragSourcePort is None else None)
            active = bool(self._focusedEdges or port is not None or preview is not None or self.getSelectedNodeIds())
            signature = (selected, self._focusedEdges, active)
            if signature == self._presentationSignature:
                return
            self._presentationSignature = signature
            styles = {}
            oldPorts, self._portEmphasis = self._portEmphasis, {}
            for key, edge in self._edgeItems.items():
                color, width, rank = edgeStyle(selected=key in selected, focused=key in self._focusedEdges,
                                               dimmed=active, blocked=edge.route.blocked)
                pen = QPen(QColor(color), width, Qt.DashLine if edge.route.blocked else Qt.SolidLine)
                pen.setCapStyle(Qt.RoundCap)
                styles[key] = (rank, color, width, edge.route.blocked)
                edge._focused = key in self._focusedEdges
                if edge.pen() != pen:
                    edge.setPen(pen)
                edge.setZValue(-.25 if key in selected else -.5 if edge._focused else -1)
                if edge._focused:
                    for portKey in ((key[0], "output", key[1]), (key[2], "input", key[3])):
                        if self._portEmphasis.get(portKey) != SELECTED_COLOR:
                            self._portEmphasis[portKey] = SELECTED_COLOR if key in selected else FOCUS_COLOR
            for portKey in oldPorts.keys() | self._portEmphasis.keys():
                if portKey in self._portItems:
                    self._portItems[portKey].update()
            buckets: dict[tuple, QPainterPath] = {}
            for part, path in self._wirePaths:
                if len(part.owners) > 1:
                    style = max(styles[key] for key in part.owners if key in styles)
                    buckets.setdefault(style, QPainterPath()).addPath(path)
            for point, owners in self._wireGeometry.junctions:
                style = max(styles[key] for key in owners if key in styles)
                buckets.setdefault(style, QPainterPath()).addEllipse(QPointF(*point), 2.3, 2.3)
            for style in self._wireLayers.keys() - buckets.keys():
                self._wireLayers[style].setPath(QPainterPath())
            for style, path in buckets.items():
                if style not in self._wireLayers:
                    layer = _WireLayer()
                    self.addItem(layer)
                    self._wireLayers[style] = layer
                layer = self._wireLayers[style]
                rank, color, width, blocked = style
                pen = QPen(QColor(color), width, Qt.DashLine if blocked else Qt.SolidLine)
                pen.setCapStyle(Qt.RoundCap)
                layer.setPen(pen)
                layer.setPath(path)
                layer.setZValue(-.95 + rank * .1775)

        def _wireGeometryChanged(self) -> None:
            if self._wireBatch:
                return
            self._wireEpoch += 1
            self._presentationSignature = None
            self._closeEdgeMenu()
            self._hoverEdgeKeys = None
            for item in self._sharedWireItems:
                self.removeItem(item)
            self._sharedWireItems = []
            self._wireGeometry = wireGeometry({key: edge.route for key, edge in self._edgeItems.items()})
            self._wirePaths = [(part, _partPath(part)) for part in self._wireGeometry.parts]
            unique = {key: QPainterPath() for key in self._edgeItems}
            shared: dict[frozenset[EdgeKey], QPainterPath] = {}
            for part, path in self._wirePaths:
                if len(part.owners) == 1:
                    unique[next(iter(part.owners))].addPath(path)
                else:
                    shared.setdefault(part.owners, QPainterPath()).addPath(path)
            for key, path in unique.items():
                self._edgeItems[key]._setDisplayPath(path)
            for owners, path in shared.items():
                item = _SharedWireItem(self, path, owners)
                self.addItem(item)
                self._sharedWireItems.append(item)
            self._refreshEdgePresentation()

        def _closeEdgeMenu(self) -> None:
            if self._branchMenu is not None:
                menu, self._branchMenu = self._branchMenu, None
                menu.close()
                menu.deleteLater()
            self._menuPreview = None

        def _showEdgeMenu(self, keys, screenPos, modifiers=Qt.NoModifier) -> None:
            keys = sorted(set(keys).intersection(self._edgeItems))
            if not keys:
                return
            self._closeEdgeMenu()
            epoch = self._wireEpoch
            parent = self.views()[0] if self.views() else None
            menu = _BranchMenu(parent)
            menu.setObjectName("flowEdgeBranchMenu")
            menu.setToolTipsVisible(True)
            self._branchMenu = menu
            self.destroyed.connect(menu.close)
            self._hoverEdgeKeys = None
            for key in keys:
                label = self._edgeLabel(key)
                screen = parent.screen() if parent is not None else None
                width = min(1000, screen.availableGeometry().width() - 80) if screen else 900
                action = menu.addAction(QFontMetricsF(menu.font()).elidedText(label, Qt.ElideMiddle, width))
                action.setToolTip(label)
                action.setData(key)
                action.hovered.connect(lambda key=key: self._previewMenuEdge(key))
                action.triggered.connect(lambda checked=False, key=key: self._chooseMenuEdge(key, epoch, modifiers))
            def hidden():
                if isValid(self) and self._branchMenu is menu:
                    self._branchMenu = None
                    self._menuPreview = None
                    self._refreshEdgePresentation()
                menu.deleteLater()
            menu.aboutToHide.connect(hidden)
            self._refreshEdgePresentation()
            menu.popup(screenPos)

        def _previewMenuEdge(self, key) -> None:
            if not isValid(self) or key not in self._edgeItems:
                return
            self._menuPreview = frozenset((key,))
            self._refreshEdgePresentation()

        def _chooseMenuEdge(self, key, epoch, modifiers) -> None:
            if not isValid(self) or epoch != self._wireEpoch or key not in self._edgeItems:
                return
            edge = self._edgeItems[key]
            if not modifiers & Qt.ControlModifier:
                self.clearSelection()
            edge.setSelected(not edge.isSelected() if modifiers & Qt.ControlModifier else True)
            self._menuPreview = None
            self._refreshEdgePresentation()

        def clearEdgeFocus(self) -> None:
            self._closeEdgeMenu()
            self._hoverPort = None
            self._hoverEdgeKeys = None
            self._clearInputHints()
            self.clearSelection()
            self._refreshEdgePresentation()

        def isEdgeMenuOpen(self) -> bool:
            return self._branchMenu is not None

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
            self._closeEdgeMenu()
            self._routeTimer.stop()
            self._cancelRouteRefinement()
            self._routesDirty = False
            self._mouseEditChanged = False
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
            self._wireGeometry = WireGeometry((), ())
            self._wirePaths = []
            self._wireLayers = {}
            self._sharedWireItems = []
            self._wireEpoch += 1
            self._hoverEdgeKeys = None
            self._menuPreview = None
            self._portEmphasis = {}
            self._focusedEdges = frozenset()
            self._presentationSignature = None
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
                    if direction == "input" and model.kind == "loop" and portName in model.inputPortLabels:
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
                    for key in list(self._portItems):
                        if key[0] == nodeId:
                            del self._portItems[key]
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
            self._hoverPort = None
            self._refreshEdgePresentation()
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
            if existingEdgeKey is not None:
                self.refreshPresentations()
            self._wireGeometryChanged()
            self._scheduleRoutes()

        def _scheduleRoutes(self) -> None:
            self._cancelRouteRefinement()
            self._routesDirty = True
            if not self._routeBatch and self.mouseGrabberItem() is None:
                self._routeTimer.start(0)

        def _routingInputs(self, items: list[_EdgeItem]) -> tuple[dict[str, Rect], list[RouteRequest]]:
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
            return nodes, requests

        def _routeEdges(self, items: list[_EdgeItem]) -> None:
            if not items:
                return
            nodes, requests = self._routingInputs(items)
            routes = OrthogonalRouter(nodes, requests).routeAll()
            self._wireBatch = True
            try:
                for item, request in zip(items, requests):
                    item.applyRoute(routes[request.key])
            finally:
                self._wireBatch = False
            self._wireGeometryChanged()

        def refreshAllRoutes(self) -> None:
            self._routeTimer.stop()
            self._cancelRouteRefinement()
            self._routeEdges(list(self._edgeItems.values()))
            self._refreshBindingPaths()
            self._routesDirty = False

        def _cancelRouteRefinement(self) -> None:
            self._routeRefineTimer.stop()
            self._routeRefinement = None
            self._refinedRoutes.clear()

        def _scheduleRouteRefinement(self) -> None:
            self._routeTimer.stop()
            if self._routeRefinement is None and not self._routeRefineTimer.isActive():
                self._routeRefineTimer.start(0)

        def _continueRouteRefinement(self) -> None:
            # Refine a snapshot cooperatively, rather than blocking a mouse
            # release for the whole graph. A new edit/clear cancels the snapshot.
            if self.mouseGrabberItem() is not None:
                self._routeRefineTimer.start(16)
                return
            deadline = perf_counter() + .008
            if self._routeRefinement is None:
                nodes, requests = self._routingInputs(list(self._edgeItems.values()))
                self._routeRefinement = OrthogonalRouter(nodes, requests).iterRoutes(stepNodes=64)
            while perf_counter() < deadline:
                try:
                    result = next(self._routeRefinement)
                except StopIteration:
                    self._wireBatch = True
                    try:
                        for key, route in self._refinedRoutes.items():
                            self._edgeItems[key].applyRoute(route)
                    finally:
                        self._wireBatch = False
                    self._wireGeometryChanged()
                    self._refreshBindingPaths()
                    self._routesDirty = False
                    self._cancelRouteRefinement()
                    return
                if result is not None:
                    key, route = result
                    self._refinedRoutes[key] = route
            self._routeRefineTimer.start(1)

        def refreshEdgesForNode(self, nodeId: str) -> None:
            self._cancelRouteRefinement()
            self._refreshBindingPaths()
            if self._routeBatch:
                return
            if self.mouseGrabberItem() is not None:
                self._routeTimer.stop()
                self._routesDirty = True
                self._mouseEditChanged = True
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
                self._hoverPort = None
                self._wireGeometryChanged()
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
            self._wireGeometryChanged()
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
            # Measure local port offsets, not saved positions or port-key order.
            # Routing stays derived state; only this explicit layout moves nodes.
            links = []
            for edge in self._edgeItems.values():
                source, target = edge.sourcePort, edge.targetPort
                links.append(LayoutEdge(edge.edge.fromNodeId, edge.edge.toNodeId,
                    source.mapToParent(source.boundingRect().center()).y(),
                    target.mapToParent(target.boundingRect().center()).y()))
            positions = flowPositions(
                {key: LayoutNode(item.geometry.width, item.geometry.height, item.model.kind)
                 for key, item in self._nodeItems.items()},
                links)
            self._applyPositions(positions)

        def nextNodePosition(self) -> tuple[float, float]:
            bottom = max((item.sceneBoundingRect().bottom() for item in self._nodeItems.values()), default=-28)
            return (20.0, float(bottom + 48))

        def startConnectionDrag(self, sourcePort: _PortItem) -> None:
            self._closeEdgeMenu()
            self._hoverPort = None
            self._hoverEdgeKeys = None
            self._dragSourcePort = sourcePort
            self._refreshEdgePresentation()
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
            if self._dragSourcePort is not None or self._branchMenu is not None:
                return
            self._clearInputHints()
            if entered:
                self._hoverPort = portItem
                self._hoverEdgeKeys = None
                if portItem.direction == "output":
                    self._applyConnectableInputHighlight(portItem)
            elif self._hoverPort is portItem:
                self._hoverPort = None
            self._refreshEdgePresentation()

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
            if event.button() == Qt.LeftButton:
                self._mouseEditChanged = False
            if self._dragSourcePort is None and self._canvasClickHandler is not None:
                itemsAtPos = self.items(event.scenePos())
                if len(itemsAtPos) == 0:
                    self._canvasClickHandler()
            if event.button() == Qt.LeftButton and self._dragSourcePort is None:
                items = self.items(event.scenePos())
                candidates = set()
                for item in items:
                    if isinstance(item, (_NodeItem, _PortItem)) or item.parentItem() is not None:
                        break
                    if isinstance(item, _EdgeItem):
                        candidates.add(item.data(int(Qt.UserRole)))
                    elif isinstance(item, _SharedWireItem):
                        candidates.update(item.owners)
                if len(candidates) > 1:
                    self._showEdgeMenu(candidates, event.screenPos(), event.modifiers())
                    event.accept()
                    return
                if not items:
                    self.clearEdgeFocus()
            super().mousePressEvent(event)

        def keyPressEvent(self, event) -> None:
            if event.key() == Qt.Key_Escape:
                if self._dragSourcePort is not None:
                    self._cancelConnectionDrag()
                    event.accept()
                    return
                if self._focusedEdges or self._hoverPort is not None:
                    self.clearEdgeFocus()
                    event.accept()
                    return
            super().keyPressEvent(event)

        def _cancelConnectionDrag(self) -> None:
            if self._dragPathItem is not None:
                self.removeItem(self._dragPathItem)
            self._dragPathItem = None
            self._dragSourcePort = None
            self._snapTargetPort = None
            self._dragInvalidReason = ""
            self._hoverPort = None
            self._clearInputHints()
            self._setDragHint("", None)
            grabber = self.mouseGrabberItem()
            if isinstance(grabber, _PortItem):
                grabber.ungrabMouse()
            self._refreshEdgePresentation()

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
                            self._mouseEditChanged = True
                    else:
                        reason = (
                            str(preview.get("reason", "连线无效"))
                            if isinstance(preview, dict)
                            else "连线无效"
                        )
                        self._emitConnectionError(reason)
                elif self._dragInvalidReason != "":
                    self._emitConnectionError(self._dragInvalidReason)

                self._cancelConnectionDrag()
            super().mouseReleaseEvent(event)
            if self._routesDirty:
                self._scheduleRouteRefinement()
            if self._mouseEditChanged:
                self._mouseEditChanged = False
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
            self._hoverPort = None
            self._hoverEdgeKeys = None
            self._refreshEdgePresentation()
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
            self._cancelConnectionDrag()

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
            color, width, _ = edgeStyle(selected=edgeKey in self._selectedEdges)
            return {
                "width": width,
                "color": color,
            }
