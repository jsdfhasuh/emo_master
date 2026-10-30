"""Graphics navigation around the same native page used by preview and runtime."""
from PySide2.QtCore import QPoint, Qt, QTimer
from PySide2.QtGui import QColor
from PySide2.QtWidgets import QGraphicsScene, QRubberBand

from emo_master.apps.designer.ui.designer_graphics_view import DesignerGraphicsView


class PageCanvas(DesignerGraphicsView):
    def __init__(self, workspace, renderer):
        self.workspace = workspace
        self.previewWidth = 0
        self._rubberStart = None
        scene = QGraphicsScene(workspace)
        super().__init__(scene)
        self.setObjectName('pageCanvas')
        self.setBackgroundBrush(QColor('#e6ebf2'))
        self.proxy = scene.addWidget(renderer)
        renderer.setMinimumSize(0, 0)
        self.rubber = QRubberBand(QRubberBand.Rectangle, self.viewport())
        self.sizeTimer = QTimer(self)
        self.sizeTimer.setSingleShot(True)
        self.sizeTimer.timeout.connect(self.updatePageSize)
        self.updatePageSize()

    def updatePageSize(self):
        from emo_master.ui.presentation.renderer import surfaceBounds
        width = self.previewWidth or max(320, self.viewport().width() - 32)
        height = max(360, self.viewport().height() - 32)
        width, height = surfaceBounds(width, height, self.workspace.renderer.devicePixelRatioF())
        self.workspace.renderer.setMaximumSize(width, height)
        self.workspace.renderer.resize(width, height)
        self.proxy.setPos(16, 16)
        self._ensureNavigationBounds()

    def setPreviewWidth(self, width):
        self.previewWidth = int(width)
        self.updatePageSize()
        self.fitPage()

    def fitPage(self):
        box = self.proxy.sceneBoundingRect()
        self.fitContent((box.x(), box.y(), box.width(), box.height()))

    def rendererPoint(self, globalPoint):
        point = self.mapToScene(self.viewport().mapFromGlobal(globalPoint))
        return self.proxy.mapFromScene(point).toPoint()

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
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
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
        super().hideEvent(event)
