from __future__ import annotations

from typing import Any

try:
    from PySide2.QtCore import QPoint, QPointF, QRectF, QSize, QTimer, Qt, Signal
    from PySide2.QtGui import QPainter
    from PySide2.QtWidgets import QGraphicsItem, QGraphicsView

    class DesignerGraphicsView(QGraphicsView):
        zoomChanged: Any = Signal(float)

        def __init__(self, scene) -> None:
            super().__init__(scene)
            self._pendingFit: tuple[tuple[float, float, float, float], float] | None = None
            self._fitViewportSize: QSize | None = None
            self._fitTimer = QTimer(self)
            self._fitTimer.setSingleShot(True)
            self._fitTimer.timeout.connect(self._applyPendingFit)
            self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
            self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
            self._minZoom = 0.02
            self._maxZoom = 3.0
            self._zoomStep = 1.15
            self._isPanning = False
            self._rubberBandSelecting = False
            self._previousDragMode = self.dragMode()
            self._lastPanPoint: QPoint | None = None
            self._scrollBarVisibility = {"horizontal": "off", "vertical": "off"}
            setHorizontalPolicy = getattr(self, "setHorizontalScrollBarPolicy", None)
            setVerticalPolicy = getattr(self, "setVerticalScrollBarPolicy", None)
            if callable(setHorizontalPolicy):
                setHorizontalPolicy(Qt.ScrollBarAlwaysOff)
            if callable(setVerticalPolicy):
                setVerticalPolicy(Qt.ScrollBarAlwaysOff)
            self._navigationTimer = QTimer(self)
            self._navigationTimer.setSingleShot(True)
            self._navigationTimer.timeout.connect(self._ensureNavigationBounds)
            scene.changed.connect(self._queueNavigationBounds)
            self._ensureNavigationBounds()

        def _viewportCenter(self) -> QPointF:
            # Use the geometric center, not the rounded integer pixel center:
            # rounding here would accumulate drift each time bounds expand.
            return self.mapToScene(self.viewport().rect()).boundingRect().center()

        def _queueNavigationBounds(self, _regions) -> None:
            if not self._navigationTimer.isActive():
                self._navigationTimer.start(0)

        def _ensureNavigationBounds(self, center=None, zoom=None, *, reset=False) -> None:
            """Keep real scroll range even when the graph is smaller than the viewport.

            Bounds belong to this view, not the graph or saved project. Grow
            before scrolling/zooming so Qt never clamps the requested movement.
            The spare viewport on each side amortizes expansion during a drag.
            """
            currentCenter = self._viewportCenter()
            targetCenter = currentCenter if center is None else center
            scale = self.getZoomFactor() if zoom is None else zoom
            width = max(1, self.viewport().width()) / scale
            height = max(1, self.viewport().height()) / scale
            visible = QRectF(targetCenter.x() - width / 2, targetCenter.y() - height / 2, width, height)
            guard = 64 / scale
            scene = self.scene()
            content = scene.itemsBoundingRect() if scene is not None else QRectF()
            required = visible.adjusted(-guard, -guard, guard, guard)
            if not content.isEmpty():
                required = required.united(content.adjusted(-guard, -guard, guard, guard))
            if not reset and self.sceneRect().contains(required):
                return
            bounds = required.united(visible.adjusted(-width, -height, width, height))
            if not reset:
                bounds = bounds.united(self.sceneRect())
            anchor = self.transformationAnchor()
            self.setTransformationAnchor(QGraphicsView.NoAnchor)
            self.setSceneRect(bounds)
            # Changing the scrollbar range must not move the camera or nodes.
            super().centerOn(currentCenter)
            self.setTransformationAnchor(anchor)

        def centerOn(self, *args) -> None:
            if len(args) == 1:
                target = args[0]
                point = target.sceneBoundingRect().center() if isinstance(target, QGraphicsItem) else QPointF(target)
            else:
                point = QPointF(*args)
            self._ensureNavigationBounds(center=point)
            super().centerOn(point)

        def getZoomFactor(self) -> float:
            return float(self.transform().m11())

        def setZoomFactor(self, factor: float, underMouse: bool = False) -> None:
            self._pendingFit = None
            self._fitTimer.stop()
            target = max(self._minZoom, min(self._maxZoom, float(factor)))
            self._ensureNavigationBounds(zoom=target)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse if underMouse else QGraphicsView.AnchorViewCenter)
            self.scale(target / self.getZoomFactor(), target / self.getZoomFactor())
            self._ensureNavigationBounds()
            self.zoomChanged.emit(self.getZoomFactor())

        def fitContent(self, bounds, minimumZoom: float = 0.02) -> None:
            self._pendingFit = (bounds, minimumZoom)
            self._fitViewportSize = None
            self._fitTimer.start(0)

        def _applyPendingFit(self) -> None:
            if self._pendingFit is None or not self.isVisible():
                return
            size = self.viewport().size()
            if self._fitViewportSize != size:
                # Top-level screen constraints can resize us after the first show.
                self._fitViewportSize = size
                self._fitTimer.start(0)
                return
            (x, y, width, height), minimum = self._pendingFit
            self._pendingFit = None
            target = min((self.viewport().width() - 24) / max(1, width),
                         (self.viewport().height() - 24) / max(1, height), 1.0)
            self.resetTransform()
            self.scale(max(minimum, target), max(minimum, target))
            # Fit is an explicit reframe: discard distant navigation history
            # from other workflows instead of growing bounds forever.
            self._ensureNavigationBounds(center=QPointF(x + width / 2, y + height / 2), reset=True)
            self.centerOn(x + width / 2, y + height / 2)
            self.zoomChanged.emit(self.getZoomFactor())

        def showEvent(self, event):
            super().showEvent(event)
            self._ensureNavigationBounds()
            if self._pendingFit is not None:
                self._fitTimer.start(0)

        def hideEvent(self, event):
            scene = self.scene()
            cancel = getattr(scene, 'cancelAlignmentDrag', None)
            if callable(cancel):
                cancel()
            self.endPan()
            if self._rubberBandSelecting:
                self._rubberBandSelecting = False
                self.setDragMode(self._previousDragMode)
            super().hideEvent(event)

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if hasattr(self, "_navigationTimer"):
                self._ensureNavigationBounds()
            if self._pendingFit is not None:
                self._fitTimer.start(0)

        def isPanning(self) -> bool:
            return self._isPanning

        def getScrollBarVisibility(self) -> dict[str, str]:
            return dict(self._scrollBarVisibility)

        def zoomByDelta(self, delta: int) -> None:
            factor = self._zoomStep if delta > 0 else 1.0 / self._zoomStep
            self.setZoomFactor(self.getZoomFactor() * factor, underMouse=True)

        def beginPanAt(self, x: float, y: float) -> bool:
            sceneMethod = getattr(self, "scene", None)
            sceneObj = sceneMethod() if callable(sceneMethod) else None
            sceneItemAt = getattr(sceneObj, "itemAtPoint", None)
            if callable(sceneItemAt):
                point = self.mapToScene(int(x), int(y))
                if sceneItemAt(point.x(), point.y()) is not None:
                    return False
            itemAt = getattr(self, "itemAt", None)
            if callable(itemAt):
                item = itemAt(int(x), int(y))
                if item is not None:
                    return False
            clearFocus = getattr(sceneObj, "clearEdgeFocus", None)
            if callable(clearFocus):
                clearFocus()
            self._isPanning = True
            self._pendingFit = None
            self._fitTimer.stop()
            self._lastPanPoint = QPoint(int(x), int(y))
            return True

        def updatePanTo(self, x: float, y: float) -> None:
            if not self._isPanning or self._lastPanPoint is None:
                return
            horizontalBar = getattr(self, "horizontalScrollBar", None)
            verticalBar = getattr(self, "verticalScrollBar", None)
            if not callable(horizontalBar) or not callable(verticalBar):
                self._lastPanPoint = QPoint(int(x), int(y))
                return
            hBar = horizontalBar()
            vBar = verticalBar()
            currentPoint = QPoint(int(x), int(y))
            delta = currentPoint - self._lastPanPoint
            self._ensureNavigationBounds(center=self._viewportCenter() - QPointF(delta) / self.getZoomFactor())
            setHValue = getattr(hBar, "setValue", None)
            setVValue = getattr(vBar, "setValue", None)
            hValue = getattr(hBar, "value", None)
            vValue = getattr(vBar, "value", None)
            if callable(setHValue) and callable(hValue):
                setHValue(hValue() - delta.x())
            if callable(setVValue) and callable(vValue):
                setVValue(vValue() - delta.y())
            self._lastPanPoint = currentPoint

        def endPan(self) -> None:
            self._isPanning = False
            self._lastPanPoint = None

        def wheelEvent(self, event) -> None:  # type: ignore[override]
            angleDelta = getattr(event, "angleDelta", None)
            if callable(angleDelta):
                deltaPoint = angleDelta()
                yMethod = getattr(deltaPoint, "y", None)
                if callable(yMethod):
                    self.zoomByDelta(int(yMethod()))
                    event.accept()
                    return
            super().wheelEvent(event)

        def mousePressEvent(self, event) -> None:  # type: ignore[override]
            buttonMethod = getattr(event, "button", None)
            posMethod = getattr(event, "pos", None)
            if callable(buttonMethod) and callable(posMethod):
                if buttonMethod() == Qt.LeftButton:
                    pos = posMethod()
                    if event.modifiers() & Qt.ShiftModifier and self.itemAt(pos) is None:
                        self._rubberBandSelecting = True
                        self._previousDragMode = self.dragMode()
                        self.setDragMode(QGraphicsView.RubberBandDrag)
                        super().mousePressEvent(event)
                        return
                    xMethod = getattr(pos, "x", None)
                    yMethod = getattr(pos, "y", None)
                    if callable(xMethod) and callable(yMethod):
                        if self.beginPanAt(float(xMethod()), float(yMethod())):
                            event.accept()
                            return
            super().mousePressEvent(event)

        def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
            if self._isPanning:
                posMethod = getattr(event, "pos", None)
                if callable(posMethod):
                    pos = posMethod()
                    xMethod = getattr(pos, "x", None)
                    yMethod = getattr(pos, "y", None)
                    if callable(xMethod) and callable(yMethod):
                        self.updatePanTo(float(xMethod()), float(yMethod()))
                        event.accept()
                        return
            super().mouseMoveEvent(event)

        def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
            buttonMethod = getattr(event, "button", None)
            if callable(buttonMethod) and buttonMethod() == Qt.LeftButton:
                if self._isPanning:
                    self.endPan()
                    event.accept()
                    return
            super().mouseReleaseEvent(event)
            if self._rubberBandSelecting:
                self._rubberBandSelecting = False
                self.setDragMode(self._previousDragMode)

except Exception:  # pragma: no cover

    class DesignerGraphicsView:  # type: ignore[no-redef]
        def __init__(self, scene) -> None:
            self._scene = scene
            self._zoomFactor = 1.0
            self._isPanning = False
            self._scrollBarVisibility = {"horizontal": "off", "vertical": "off"}

        def setAcceptDrops(self, value: bool) -> None:
            _ = value

        def getZoomFactor(self) -> float:
            return float(self._zoomFactor)

        def setZoomFactor(self, factor: float, underMouse: bool = False) -> None:
            self._zoomFactor = max(0.35, min(3.0, float(factor)))

        def fitContent(self, bounds, minimumZoom: float = 0.02) -> None:
            self._zoomFactor = max(minimumZoom, 1.0)

        def zoomByDelta(self, delta: int) -> None:
            factor = 1.15 if delta > 0 else 1.0 / 1.15
            target = self._zoomFactor * factor
            if 0.35 <= target <= 3.0:
                self._zoomFactor = target

        def beginPanAt(self, x: float, y: float) -> bool:
            itemAt = getattr(self._scene, "itemAtPoint", None)
            if callable(itemAt) and itemAt(float(x), float(y)) is not None:
                return False
            self._isPanning = True
            return True

        def isPanning(self) -> bool:
            return self._isPanning

        def endPan(self) -> None:
            self._isPanning = False

        def getScrollBarVisibility(self) -> dict[str, str]:
            return dict(self._scrollBarVisibility)

        def centerOn(self, x: float, y: float) -> None:
            _ = x
            _ = y

        def fitInView(
            self, x: float, y: float, width: float, height: float, mode
        ) -> None:
            _ = x
            _ = y
            _ = width
            _ = height
            _ = mode

        def resetTransform(self) -> None:
            return

        def scale(self, sx: float, sy: float) -> None:
            _ = sx
            _ = sy
