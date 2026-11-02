"""Graphics navigation around the same native page used by preview and runtime."""
from PySide2.QtCore import QPoint, QPointF, QSize, Qt, QTimer
from PySide2.QtGui import QColor
from PySide2.QtWidgets import QGraphicsItem, QGraphicsScene, QLayout, QRubberBand

from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView


class PageCanvas(DesignerGraphicsView):
    def __init__(self, workspace, renderer):
        self.workspace = workspace
        self.previewWidth = 0
        self.previewHeight = 0
        self.autoFit = True
        self._sizing = False
        self._deferredSize = False
        self._lastPageId = None
        self._lastViewportSize = QSize()
        self._rubberStart = None
        scene = QGraphicsScene(workspace)
        super().__init__(scene)
        self.setObjectName('pageCanvas')
        self.setBackgroundBrush(QColor('#e6ebf2'))
        self.proxy = scene.addWidget(renderer)
        self.proxy.setCacheMode(QGraphicsItem.NoCache)
        renderer.layout().setSizeConstraint(QLayout.SetNoConstraint)
        renderer.setMinimumSize(0, 0)
        self.rubber = QRubberBand(QRubberBand.Rectangle, self.viewport())
        self.sizeTimer = QTimer(self)
        self.sizeTimer.setSingleShot(True)
        self.sizeTimer.timeout.connect(self.updatePageSize)
        self.updatePageSize()

    def gestureActive(self):
        tools = getattr(self.workspace, 'tools', None)
        responsive = getattr(tools, 'responsive', None)
        selection = getattr(tools, 'selection', None)
        return bool(self._rubberStart is not None or self._isPanning
            or (tools and tools.dragStart)
            or (responsive and (responsive.resize or responsive.pointer or responsive.preview))
            or (selection and selection.active))

    def updatePageSize(self):
        if self._sizing:
            return
        if self.gestureActive():
            self._deferredSize = True
            self.sizeTimer.start(16)
            return
        self._deferredSize = False
        self._sizing = True
        renderer = self.workspace.renderer
        previous = QSize(renderer.size())
        pageId = getattr(self.workspace, 'pageId', None)
        changedPage = pageId != self._lastPageId
        viewportChanged = self.viewport().size() != self._lastViewportSize
        self._lastViewportSize = QSize(self.viewport().size())
        if changedPage:
            self._lastPageId = pageId
            self.autoFit = True
        try:
            # Terminal sizes describe the page viewport, excluding editor chrome.
            for _ in range(3):
                renderer.layout().activate()
                renderer.stack.layout().activate()
                scroll = renderer.pages.get(pageId)
                if scroll is not None:
                    scroll.setMinimumSize(0, 0)
                    extra = renderer.size() - scroll.viewport().size()
                else:
                    margins = renderer.layout().contentsMargins()
                    extra = QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
                if self.previewWidth:
                    target = QSize(self.previewWidth, self.previewHeight or 720) + extra
                else:
                    target = QSize(max(1, self.viewport().width() - 16),
                                   max(1, self.viewport().height() - 16))
                renderer.setMinimumSize(0, 0)
                renderer.setMaximumSize(target)
                renderer.resize(target)
            renderer.layout().activate()
            renderer.stack.layout().activate()
            self.proxy.setPos(8, 8)
            self._ensureNavigationBounds()
        finally:
            self._sizing = False
        if self.autoFit and (changedPage or viewportChanged or renderer.size() != previous):
            self._requestFit()

    def setPreviewSize(self, width=0, height=0):
        self.previewWidth = int(width)
        self.previewHeight = int(height) if width else 0
        self.autoFit = True
        self.updatePageSize()
        self._requestFit()

    def setPreviewWidth(self, width):
        # Kept for width-preview callers; terminal UI presets set both axes.
        self.setPreviewSize(width, self.previewHeight or 720)

    def fitPage(self):
        self.autoFit = True
        self.updatePageSize()
        self._requestFit()

    def _requestFit(self):
        if self.gestureActive():
            self._deferredSize = True
            self.sizeTimer.start(16)
            return
        box = self.proxy.sceneBoundingRect()
        self.fitContent((box.x(), box.y(), box.width(), box.height()))

    def _applyPendingFit(self):
        if self.gestureActive():
            self._fitTimer.start(16)
            return
        if self._pendingFit is None or not self.isVisible():
            return
        size = self.viewport().size()
        if self._fitViewportSize != size:
            self._fitViewportSize = size
            self._fitTimer.start(0)
            return
        (x, y, width, height), minimum = self._pendingFit
        self._pendingFit = None
        target = min(max(1, size.width() - 16) / max(1, width),
                     max(1, size.height() - 16) / max(1, height), 1.0)
        self.resetTransform()
        self.scale(max(minimum, target), max(minimum, target))
        center = QPointF(x + width / 2, y + height / 2)
        self._ensureNavigationBounds(center=center, reset=True)
        self.centerOn(center)
        self.zoomChanged.emit(self.getZoomFactor())

    def setZoomFactor(self, factor, underMouse=False):
        self.autoFit = False
        super().setZoomFactor(factor, underMouse=underMouse)

    def rendererPoint(self, globalPoint):
        point = self.mapToScene(self.viewport().mapFromGlobal(globalPoint))
        return self.proxy.mapFromScene(point).toPoint()

    def widgetScreenPoint(self, widget, point):
        local = widget.mapTo(self.workspace.renderer, point)
        scene = self.proxy.mapToScene(QPointF(local))
        return self.viewport().mapToGlobal(self.mapFromScene(scene))

    def rendererAt(self, point):
        renderer = self.workspace.renderer
        local = self.proxy.mapFromScene(self.mapToScene(point)).toPoint()
        if not renderer.rect().contains(local):
            return None, local
        return renderer.childAt(local) or renderer, local

    def beginPanAt(self, x, y):
        widget, _point = self.rendererAt(QPoint(int(x), int(y)))
        tools = getattr(self.workspace, 'tools', None)
        if widget is not None:
            if tools is None or not self.workspace.renderer.editing or tools.componentAt(widget)[0]:
                return False
            current = widget
            while current is not None and not current.property('pageGrid'):
                current = current.parentWidget()
            if current is None:
                return False
        if tools:
            try:
                tools.select(None)
            except ValueError as error:
                self.workspace.message.setText(str(error))
                return False
        self.autoFit = False
        self._isPanning = True
        self._pendingFit = None
        self._fitTimer.stop()
        self._lastPanPoint = QPoint(int(x), int(y))
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier:
            widget, _point = self.rendererAt(event.pos())
            tools = getattr(self.workspace, 'tools', None)
            if tools and self.workspace.renderer.editing and (widget is None or not tools.componentAt(widget)[0]):
                self._rubberStart = QPoint(event.pos())
                self.rubber.setGeometry(event.pos().x(), event.pos().y(), 0, 0)
                self.rubber.show()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubberStart is not None:
            from PySide2.QtCore import QRect
            self.rubber.setGeometry(QRect(self._rubberStart, event.pos()).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rubberStart is not None and event.button() == Qt.LeftButton:
            box = self.mapToScene(self.rubber.geometry()).boundingRect()
            local = self.proxy.mapFromScene(box).boundingRect()
            self.rubber.hide()
            self._rubberStart = None
            self.workspace.tools.responsive.selectRect(local)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        # Both embedded-card selection and gray-canvas deselection can commit
        # the property form. Rebuild only after the native receiver has returned.
        tools = getattr(self.workspace, 'tools', None)
        if tools and self.workspace.renderer.config != self.workspace.store.snapshot():
            tools.laterRefresh()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Home:
            self.fitPage()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            if self._rubberStart is not None:
                self.rubber.hide()
                self._rubberStart = None
                event.accept()
                return
            tools = getattr(self.workspace, 'tools', None)
            if tools and tools.responsive.cancel():
                event.accept()
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'sizeTimer'):
            self.sizeTimer.start(0)

    def hideEvent(self, event):
        self.rubber.hide()
        self._rubberStart = None
        tools = getattr(self.workspace, 'tools', None)
        if tools:
            tools.responsive.cancel()
        self.sizeTimer.stop()
        self._fitTimer.stop()
        self._pendingFit = None
        super().hideEvent(event)
