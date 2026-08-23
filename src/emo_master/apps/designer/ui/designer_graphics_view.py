from __future__ import annotations


try:
    from PySide2.QtCore import QPoint, Qt
    from PySide2.QtWidgets import QGraphicsView

    class DesignerGraphicsView(QGraphicsView):
        def __init__(self, scene) -> None:
            super().__init__(scene)
            self._zoomFactor = 1.0
            self._minZoom = 0.35
            self._maxZoom = 3.0
            self._zoomStep = 1.15
            self._isPanning = False
            self._lastPanPoint: QPoint | None = None
            self._scrollBarVisibility = {"horizontal": "off", "vertical": "off"}
            setHorizontalPolicy = getattr(self, "setHorizontalScrollBarPolicy", None)
            setVerticalPolicy = getattr(self, "setVerticalScrollBarPolicy", None)
            if callable(setHorizontalPolicy):
                setHorizontalPolicy(Qt.ScrollBarAlwaysOff)
            if callable(setVerticalPolicy):
                setVerticalPolicy(Qt.ScrollBarAlwaysOff)

        def getZoomFactor(self) -> float:
            return float(self._zoomFactor)

        def isPanning(self) -> bool:
            return self._isPanning

        def getScrollBarVisibility(self) -> dict[str, str]:
            return dict(self._scrollBarVisibility)

        def zoomByDelta(self, delta: int) -> None:
            factor = self._zoomStep if delta > 0 else 1.0 / self._zoomStep
            target = self._zoomFactor * factor
            if target < self._minZoom or target > self._maxZoom:
                return
            setAnchor = getattr(self, "setTransformationAnchor", None)
            anchor = getattr(QGraphicsView, "AnchorUnderMouse", None)
            if callable(setAnchor) and anchor is not None:
                setAnchor(anchor)
            scaleMethod = getattr(self, "scale", None)
            if callable(scaleMethod):
                scaleMethod(factor, factor)
            self._zoomFactor = target

        def beginPanAt(self, x: float, y: float) -> bool:
            sceneMethod = getattr(self, "scene", None)
            sceneObj = sceneMethod() if callable(sceneMethod) else None
            sceneItemAt = getattr(sceneObj, "itemAtPoint", None)
            if callable(sceneItemAt):
                if sceneItemAt(float(x), float(y)) is not None:
                    return False
            itemAt = getattr(self, "itemAt", None)
            if callable(itemAt):
                item = itemAt(int(x), int(y))
                if item is not None:
                    return False
            self._isPanning = True
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
