"""ROI editing in image coordinates; navigation is shared with other editors."""
from copy import deepcopy
import math

from PySide2.QtCore import QPointF, Qt, Signal
from PySide2.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from emo_master.apps.designer.ui.image_view import ImageView
from emo_master.plugins.builtins.roi.operator import RoiOperator


GEOMETRY_FIELDS = {
    "bbox": ("x", "y", "width", "height"),
    "rotatedBox": ("centerX", "centerY", "width", "height", "angleDegrees"),
    "polygon": ("points",),
}


def regionPoints(region):
    kind = region.get("roiType", "bbox")
    if kind == "polygon":
        points = region.get("points", [])
        return [(float(p["x"]), float(p["y"])) for p in points
                if isinstance(p, dict) and isinstance(p.get("x"), (int, float))
                and isinstance(p.get("y"), (int, float))] if isinstance(points, list) else []
    width, height = region.get("width", 1), region.get("height", 1)
    if kind == "bbox":
        x, y = region.get("x", 0), region.get("y", 0)
        return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    cx, cy = region.get("centerX", 0), region.get("centerY", 0)
    angle = math.radians(region.get("angleDegrees", 0))
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + x*c - y*s, cy + x*s + y*c) for x, y in
            ((-width/2, -height/2), (width/2, -height/2), (width/2, height/2), (-width/2, height/2))]


class RoiCanvas(ImageView):
    draftChanged = Signal()
    regionCommitted = Signal(dict)
    regionSelected = Signal(int)
    editStarted = Signal()
    regionEdited = Signal(int, dict)
    editFinished = Signal()
    modeChanged = Signal(str)

    def __init__(self):
        super().__init__(minimumZoom=.01, maximumZoom=20)
        self.regions = []
        self.selected = -1
        self.mode = "select"
        self.purpose = "exclude"
        self.angle = 0.
        self.draft = None
        self.opacity = .25
        self._roiType = "bbox"
        self._drag = None
        self._hover = None
        self.emptyText = "请选择图源"
        self.setMinimumSize(240, 180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMode("select")

    def setRegions(self, regions, selected):
        self.regions = deepcopy(regions)
        self.selected = selected
        if 0 <= selected < len(regions):
            self._roiType = regions[selected].get("roiType", "bbox")
        self.viewport().update()

    def setMode(self, mode):
        if self.draft is not None:
            self.cancelDrawing()
        self.mode = mode
        self._drag = None
        self.setDragMode(self.ScrollHandDrag if mode == "pan" else self.NoDrag)
        self.viewport().setCursor(Qt.OpenHandCursor if mode == "pan" else
                                  Qt.CrossCursor if mode in GEOMETRY_FIELDS else Qt.ArrowCursor)
        self.modeChanged.emit(mode)

    def setPurpose(self, purpose):
        self.purpose = purpose
        if self.draft is not None:
            self.draft["purpose"] = purpose
            self.draftChanged.emit()
            self.viewport().update()

    def draftError(self):
        if self.draft is None:
            return "尚未开始绘制"
        error = RoiOperator().validateParams(self.draft)
        return error["message"] if error else ""

    def finishDrawing(self):
        if self.draft is None or self.draftError():
            return
        region = deepcopy(self.draft)
        self.draft = None
        self._hover = None
        self.setMode("select")
        self.regionCommitted.emit(region)
        self.draftChanged.emit()
        self.viewport().update()

    def cancelDrawing(self):
        hadDraft = self.draft is not None
        self.draft = None
        self._hover = None
        self._drag = None
        if hadDraft:
            self.draftChanged.emit()
        self.viewport().update()

    def removeLastPoint(self):
        if self.draft is None or self.draft.get("roiType") != "polygon":
            return
        points = self.draft["points"]
        if points:
            points.pop()
        if not points:
            self.draft = None
        self._hover = None
        self.draftChanged.emit()
        self.viewport().update()

    def _toImage(self, point):
        mapped = self.mapToScene(point)
        return (max(0., min(float(self.pixmap().width()), mapped.x())),
                max(0., min(float(self.pixmap().height()), mapped.y())))

    def _near(self, a, b, radius=8):
        return math.hypot(a[0]-b[0], a[1]-b[1]) * self.zoom <= radius

    def mousePressEvent(self, event):
        if self.pixmap().isNull():
            return
        self.setFocus(Qt.MouseFocusReason)
        if event.button() == Qt.MiddleButton or self.mode == "pan":
            super().mousePressEvent(event)
            return
        if event.button() != Qt.LeftButton:
            return
        point = self._toImage(event.pos())
        if self.mode in GEOMETRY_FIELDS:
            if not self.item.boundingRect().contains(self.mapToScene(event.pos())):
                return
            if self.mode == "polygon":
                if self.draft is None:
                    self.draft = dict(roiType="polygon", points=[], purpose=self.purpose, enabled=True)
                points = self.draft["points"]
                if points and self._near(point, (points[0]["x"], points[0]["y"]), 9):
                    if len(points) >= 3:
                        self.finishDrawing()
                    return
                if points and self._near(point, (points[-1]["x"], points[-1]["y"]), 3):
                    return
                points.append(dict(x=point[0], y=point[1]))
                self._hover = None
                self.draftChanged.emit()
                self.viewport().update()
            else:
                self._drag = dict(kind="new", start=point)
            return
        if 0 <= self.selected < len(self.regions) and self.regions[self.selected].get("enabled", True):
            for index, vertex in enumerate(regionPoints(self.regions[self.selected])):
                if self._near(point, vertex):
                    self._drag = dict(kind="vertex", index=self.selected, vertex=index,
                                      original=deepcopy(self.regions[self.selected]), start=point)
                    self.editStarted.emit()
                    return
        for index in reversed(range(len(self.regions))):
            region = self.regions[index]
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(*p) for p in regionPoints(region)]))
            if region.get("enabled", True) and path.contains(QPointF(*point)):
                self.regionSelected.emit(index)
                self._drag = dict(kind="move", index=index, original=deepcopy(region), start=point)
                self.editStarted.emit()
                return
        self._panStart = event.pos()
        self.viewport().setCursor(Qt.ClosedHandCursor)

    def mouseDoubleClickEvent(self, event):
        # Qt sends a double-click instead of the second press. It is never a
        # vertex or a completion gesture; the first press already placed it.
        event.accept()

    def mouseMoveEvent(self, event):
        if self._panStart is not None or self.mode == "pan":
            super().mouseMoveEvent(event)
            return
        point = self._toImage(event.pos())
        if self._drag is None:
            if self.draft is not None and self.mode == "polygon":
                self._hover = point
                self.viewport().update()
            return
        drag = self._drag
        if drag["kind"] == "new":
            x, y = min(point[0], drag["start"][0]), min(point[1], drag["start"][1])
            w, h = abs(point[0]-drag["start"][0]), abs(point[1]-drag["start"][1])
            self.draft = dict(roiType=self.mode, purpose=self.purpose, enabled=True, width=w, height=h)
            self.draft.update(dict(centerX=x+w/2, centerY=y+h/2, angleDegrees=self.angle)
                              if self.mode == "rotatedBox" else dict(x=x, y=y))
            self.draftChanged.emit()
        else:
            region = deepcopy(drag["original"])
            if drag["kind"] == "move":
                vertices = regionPoints(region)
                dx, dy = point[0]-drag["start"][0], point[1]-drag["start"][1]
                dx = max(-min(p[0] for p in vertices), min(self.pixmap().width()-max(p[0] for p in vertices), dx))
                dy = max(-min(p[1] for p in vertices), min(self.pixmap().height()-max(p[1] for p in vertices), dy))
                if region["roiType"] == "polygon":
                    region["points"] = [dict(x=x+dx, y=y+dy) for x, y in vertices]
                else:
                    xKey, yKey = ("x", "y") if region["roiType"] == "bbox" else ("centerX", "centerY")
                    region[xKey] += dx
                    region[yKey] += dy
            elif region["roiType"] == "polygon":
                region["points"][drag["vertex"]] = dict(x=point[0], y=point[1])
            else:
                opposite = regionPoints(region)[(drag["vertex"]+2) % 4]
                if region["roiType"] == "bbox":
                    region.update(x=min(opposite[0], point[0]), y=min(opposite[1], point[1]),
                                  width=max(.001, abs(opposite[0]-point[0])), height=max(.001, abs(opposite[1]-point[1])))
                else:
                    angle = math.radians(region.get("angleDegrees", 0))
                    dx, dy = point[0]-opposite[0], point[1]-opposite[1]
                    region.update(centerX=(point[0]+opposite[0])/2, centerY=(point[1]+opposite[1])/2,
                                  width=max(.001, abs(dx*math.cos(angle)+dy*math.sin(angle))),
                                  height=max(.001, abs(-dx*math.sin(angle)+dy*math.cos(angle))))
            self.regions[drag["index"]] = region
            self.regionEdited.emit(drag["index"], region)
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag is not None:
            drag, self._drag = self._drag, None
            if drag["kind"] == "new":
                if self.draft is not None and not self.draftError():
                    self.finishDrawing()
                else:
                    self.cancelDrawing()
            else:
                self.editFinished.emit()
        elif event.button() == Qt.LeftButton and self.mode == "select" and self._panStart is not None:
            self._panStart = None
            self.viewport().setCursor(Qt.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.draft is not None:
            self.finishDrawing()
        elif event.key() == Qt.Key_Backspace and self.draft is not None:
            self.removeLastPoint()
        elif event.key() == Qt.Key_Escape and (self.draft is not None or self.mode in GEOMETRY_FIELDS):
            self.setMode("select")
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def drawForeground(self, painter, rect):
        if self.pixmap().isNull():
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        for index, region in enumerate(self.regions):
            if region.get("enabled", True):
                self._paintRegion(painter, region, selected=index == self.selected and self.mode == "select")
        if self.draft is not None:
            self._paintRegion(painter, self.draft, draft=True)

    def _paintRegion(self, painter, region, *, selected=False, draft=False):
        points = regionPoints(region)
        if not points:
            return
        color = QColor("#209779" if region.get("purpose", "keep") == "keep" else "#dc5969")
        stroke = QColor("#c88a25") if draft else color
        pen = QPen(stroke, 2 if selected or draft else 1.5)
        pen.setCosmetic(True)
        painter.setPen(pen)
        fill = QColor(color)
        fill.setAlphaF(self.opacity)
        painter.setBrush(fill if len(points) >= 3 else Qt.NoBrush)
        polygon = QPolygonF([QPointF(*p) for p in points])
        if draft and region["roiType"] == "polygon":
            if len(points) >= 3:
                painter.setPen(Qt.NoPen)
                painter.drawPolygon(polygon)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(polygon)
            if len(points) >= 3:
                pen.setStyle(Qt.DashLine)
                painter.setPen(pen)
                painter.drawLine(QPointF(*points[-1]), QPointF(*points[0]))
                pen.setStyle(Qt.SolidLine)
                painter.setPen(pen)
                painter.drawEllipse(QPointF(*points[0]), 9/self.zoom, 9/self.zoom)
            if self._hover and not self._near(self._hover, points[0]):
                painter.drawLine(QPointF(*points[-1]), QPointF(*self._hover))
        else:
            painter.drawPolygon(polygon)
        if selected or draft:
            painter.setBrush(self.palette().base())
            for x, y in points:
                painter.drawEllipse(QPointF(x, y), 3.5/self.zoom, 3.5/self.zoom)
