"""Graphics navigation around the same native page used by preview and runtime."""
import json

from PySide2.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide2.QtGui import QColor
from PySide2.QtWidgets import QGraphicsItem, QGraphicsScene, QLayout, QRubberBand

from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView
from emo_master.ui.presentation.page_scroll import pageContentSize


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
        self.componentDragActive = False
        self._dropCenter = None
        self._expansionPayload = None
        self._expansionPoint = QPoint()
        scene = QGraphicsScene(workspace)
        super().__init__(scene)
        self.setObjectName('pageCanvas')
        self.setBackgroundBrush(QColor('#e6ebf2'))
        self.proxy = scene.addWidget(renderer)
        self.proxy.setCacheMode(QGraphicsItem.NoCache)
        renderer.layout().setSizeConstraint(QLayout.SetNoConstraint)
        renderer.setMinimumSize(0, 0)
        self.rubber = QRubberBand(QRubberBand.Rectangle, self.viewport())
        self.expansionTimer = QTimer(self)
        self.expansionTimer.setInterval(16)
        self.expansionTimer.timeout.connect(self.extendAtEdge)
        self.sizeTimer = QTimer(self)
        self.sizeTimer.setSingleShot(True)
        self.sizeTimer.timeout.connect(self.updatePageSize)
        self.updatePageSize()

    def gestureActive(self):
        tools = getattr(self.workspace, 'tools', None)
        responsive = getattr(tools, 'responsive', None)
        selection = getattr(tools, 'selection', None)
        return bool(self._rubberStart is not None or self._isPanning or self.componentDragActive
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
        pageId = getattr(self.workspace, 'pageId', None)
        page = renderer.config.pages.get(pageId)
        canvasTop = page.canvasTop if page is not None else 0
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
                    scroll.setCanvasExtension(-canvasTop if self.previewWidth else 0)
                    extra = renderer.size() - scroll.viewport().size()
                else:
                    margins = renderer.layout().contentsMargins()
                    extra = QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
                if self.previewWidth:
                    target = QSize(self.previewWidth, self.previewHeight or 720) + extra
                else:
                    target = QSize(max(1, self.viewport().width() - 16),
                                   max(1, self.viewport().height() - 16 - canvasTop))
                    if scroll is not None:
                        target = pageContentSize(scroll.widget().layout(), self.contentMinimumSize(canvasTop)) + extra
                renderer.setMinimumSize(0, 0)
                renderer.setMaximumSize(target)
                renderer.resize(target)
            renderer.layout().activate()
            renderer.stack.layout().activate()
            self.proxy.setPos(8, 8 + canvasTop)
            self._ensureNavigationBounds()
        finally:
            self._sizing = False
        if self.autoFit and (changedPage or viewportChanged):
            self._requestFit()

    def contentMinimumSize(self, canvasTop):
        """Stable layout basis for both live content and disposable drag candidates."""
        if self.previewWidth:
            return QSize(self.previewWidth, (self.previewHeight or 720) - canvasTop)
        renderer = self.workspace.renderer
        scroll = renderer.pages[self.workspace.pageId]
        extra = renderer.size() - scroll.viewport().size()
        return QSize(max(1, self.viewport().width() - 16 - extra.width()),
                     max(1, self.viewport().height() - 16 - extra.height() - canvasTop))

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

    def pageViewportRect(self):
        scroll = self.workspace.renderer.pages[self.workspace.pageId]
        origin = scroll.viewport().mapTo(self.workspace.renderer, QPoint())
        first = self.mapFromScene(self.proxy.mapToScene(QPointF(origin)))
        last = self.mapFromScene(self.proxy.mapToScene(QPointF(origin + scroll.viewport().rect().bottomRight())))
        return QRect(first, last)

    def rendererRectOnViewport(self, rect):
        first = self.mapFromScene(self.proxy.mapToScene(QPointF(rect.topLeft())))
        last = self.mapFromScene(self.proxy.mapToScene(QPointF(rect.bottomRight())))
        return QRect(first, last)

    def expansionOffset(self, payload, point):
        renderer = self.workspace.renderer
        page = renderer.config.pages[self.workspace.pageId]
        root = renderer.layoutHosts[self.workspace.pageId][None]
        local = root.mapFrom(renderer, self.proxy.mapFromScene(self.mapToScene(point)).toPoint())
        hotspot = payload.get('hotspot', [0, 0])
        offset = local.y() - hotspot[1] + page.canvasTop - root.layout().contentsMargins().top()
        return max(-32768, min(0, offset))

    def beginExpansion(self, payload, point):
        if self._dropCenter is None:
            self._dropCenter = self._viewportCenter()
        self._expansionPayload, self._expansionPoint = payload, QPoint(point)
        if point.y() < 32:
            self.expansionTimer.start()
        else:
            self.expansionTimer.stop()

    def extendAtEdge(self):
        if not self.componentDragActive or self._expansionPayload is None:
            self.expansionTimer.stop()
            return
        if self.expansionOffset(self._expansionPayload, self._expansionPoint) <= -32768:
            self.expansionTimer.stop()
            return
        # Reuse the flow canvas's growing navigation bounds, without changing
        # logical widget sizes while the native drag owns their receivers.
        self.centerOn(self._viewportCenter() - QPointF(0, 6 / self.getZoomFactor()))
        self.workspace.tools.responsive.drop(self._expansionPayload, self.workspace.renderer, QPoint(),
            preview=True, canvasOffset=self.expansionOffset(self._expansionPayload, self._expansionPoint))

    def commitExpansion(self):
        self._dropCenter = None
        self.autoFit = False
        self._pendingFit = None
        self._fitTimer.stop()

    def finishDragAssist(self):
        self.componentDragActive = False
        self.expansionTimer.stop()
        self._expansionPayload = None
        if self._dropCenter is not None:
            center, self._dropCenter = self._dropCenter, None
            self.centerOn(center)

    def routeComponentDrag(self, event):
        from .tools import MIME
        tools = getattr(self.workspace, 'tools', None)
        if (tools is None or not self.workspace.renderer.editing or not tools.responsive.enabled()
                or not event.mimeData().hasFormat(MIME)):
            return False
        payload = json.loads(bytes(event.mimeData().data(MIME)))
        if 'choice' in payload:
            return False
        self.componentDragActive = True
        rect, point = self.pageViewportRect(), event.pos()
        scroll = self.workspace.renderer.pages[self.workspace.pageId]
        offset = self.expansionOffset(payload, point)
        expanding = (scroll.verticalScrollBar().value() == 0
                     and rect.left() <= point.x() <= rect.right() and offset < 0)
        if expanding:
            self.beginExpansion(payload, point)
            widget, local = self.workspace.renderer, QPoint()
        else:
            self.expansionTimer.stop()
            self._expansionPayload = None
            widget, local = self.rendererAt(point)
            if widget is None:
                tools.responsive.clearPreview()
                # Accept entry into gray canvas so subsequent moves can reach
                # the page or its expanded upper canvas without leaving this QWidget.
                if event.type() == QEvent.DragEnter:
                    event.acceptProposedAction()
                else:
                    event.ignore()
                if event.type() == QEvent.Drop:
                    tools.responsive.cancel()
                return True
            local = widget.mapFrom(self.workspace.renderer, local)
        tools.responsive.handleDropEvent(event, payload, widget, local, canvasOffset=offset if expanding else None)
        return True

    def dragEnterEvent(self, event):
        if not self.routeComponentDrag(event):
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if not self.routeComponentDrag(event):
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not self.routeComponentDrag(event):
            super().dropEvent(event)

    def dragLeaveEvent(self, event):
        if self.componentDragActive:
            self.workspace.tools.responsive.cancel()
            event.accept()
        else:
            super().dragLeaveEvent(event)

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
