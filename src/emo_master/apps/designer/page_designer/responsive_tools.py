"""Selection and ordered drag/drop over the shared native responsive page."""
import json

from PySide2.QtCore import QObject, QEvent, QPoint, QRect, QRectF, QSize, Qt
from PySide2.QtGui import QColor, QDrag, QPainter, QPen
from PySide2.QtWidgets import QApplication, QWidget
from shiboken2 import isValid

from emo_master.apps.designer.state.presentation_store import _component
from emo_master.apps.designer.ui.canvas_alignment import Box, snapOffset
from .responsive_editing import ResponsiveCommands, childrenFor, layoutFor, parentOf, selectionRoots, placeComponents


class DropOutline(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setProperty('editorDecoration', True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.box = QRect()
        self.lines = ()
        self.message = ''
        self.placements = {}
        self.moving = set()

    def paintEvent(self, event):
        painter = QPainter(self)
        for key, rect in self.placements.items():
            moving = key in self.moving
            painter.setPen(QPen(QColor('#2672d9' if moving else '#61758d'), 2,
                                Qt.SolidLine if moving else Qt.DashLine))
            painter.setBrush(QColor(38, 114, 217, 35 if moving else 12))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor('#2672d9'), 2))
        painter.setBrush(QColor(38, 114, 217, 35))
        if not self.box.isEmpty():
            painter.drawRect(self.box.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor('#ca48b8'), 1, Qt.DashLine))
        for line in self.lines:
            painter.drawLine(*[round(v) for v in line])
        if self.message:
            painter.setPen(QColor('#1d4f93'))
            painter.fillRect(QRect(0, 0, min(self.width(), 460), 28), QColor('#eef4ff'))
            painter.drawText(QRect(8, 0, self.width() - 16, 28), Qt.AlignVCenter, self.message)


class ResponsiveHandle(QWidget):
    def __init__(self, owner, card, key):
        super().__init__(card)
        self.owner, self.key = owner, key
        self.setProperty('editorDecoration', True)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setStyleSheet('background:#2672d9;border:1px solid white;')
        self.setToolTip('拖动调整期望宽度和高度；Esc 取消')
        card.installEventFilter(self)
        self.position()

    def position(self):
        card = self.parentWidget()
        self.setGeometry(card.width() - 14, card.height() - 14, 14, 14)
        self.show()
        self.raise_()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.position()
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            try:
                self.owner.beginResize(self.key, self, event.globalPos())
                self.grabMouse()
                self.grabKeyboard()
            except (ValueError, KeyError) as error:
                self.owner.w.message.setText(str(error))
            event.accept()

    def mouseMoveEvent(self, event):
        self.owner.moveResize(event.globalPos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.releaseMouse()
            self.releaseKeyboard()
            self.owner.finishResize(event.globalPos())
            event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.owner.cancel()
            event.accept()
        else:
            super().keyPressEvent(event)


class ResponsiveTools(QObject):
    def __init__(self, tools):
        super().__init__(tools)
        self.tools, self.w = tools, tools.w
        self.selectedIds = []
        self.decorations = []
        self.handles = []
        self.preview = None
        self.pointer = None
        self.resize = None
        self.guidesEnabled = True
        self.snapEnabled = True
        self.lastTarget = None
        self.candidateRects = {}
        self.candidateSize = QSize()
        self.candidateIds = []
        self.prototype = None
        self.prototypeKind = None
        self.prototypeScene = None

    def enabled(self):
        page = self.w.renderer.config.pages.get(self.w.pageId)
        return page is not None and page.layout.type == 'responsive'

    def commands(self):
        return ResponsiveCommands(self.w.session)

    def cards(self):
        return self.w.renderer.cards.get(self.w.pageId, {})

    def point(self, globalPoint):
        return self.w.canvas.rendererPoint(globalPoint)

    def select(self, keys):
        keys = [key for key in dict.fromkeys(keys) if key in self.cards()]
        primary = keys[-1] if keys else None
        primaryChanged = primary != self.tools.selected
        if primaryChanged:
            self.tools.commitPending()
        self.selectedIds = keys
        # Updating other selected cards does not leave the active property form.
        # Reloading that same form would overwrite its uncommitted input.
        if primaryChanged:
            self.tools.select(primary)
        self.updateSelection()
        actions = getattr(self.w.coordinator.window, 'designerActions', None)
        if actions:
            actions.scheduleRefresh()

    def selectRect(self, box):
        if not self.enabled():
            return
        keys = []
        for key, card in self.cards().items():
            rect = QRectF(card.mapTo(self.w.renderer, QPoint()), card.size())
            if box.contains(rect):
                keys.append(key)
        # A selected group already contains its descendants.
        roots = selectionRoots(self.w.renderer.config, self.w.pageId, keys)
        self.select([item.componentId for item in roots])

    def clearDecorations(self):
        for mark in [*self.decorations, *self.handles]:
            if isValid(mark):
                if isinstance(mark, ResponsiveHandle) and isValid(mark.parentWidget()):
                    mark.parentWidget().removeEventFilter(mark)
                mark.hide()
                mark.deleteLater()
        self.decorations.clear()
        self.handles.clear()

    def updateSelection(self):
        if self.resize:
            return
        self.clearDecorations()
        if not self.w.renderer.editing:
            return
        if self.tools.selected and self.tools.selected not in self.selectedIds:
            self.selectedIds = [self.tools.selected]
        self.selectedIds = [key for key in self.selectedIds if key in self.cards()]
        from .canvas_tools import Outline
        from .palette import TITLES
        for key in self.selectedIds:
            card = self.cards()[key]
            component = _component(self.w.renderer.config, self.w.pageId, key)
            mark = Outline(card, TITLES[component.type])
            mark.setGeometry(card.rect())
            mark.show()
            mark.raise_()
            self.decorations.append(mark)
            if len(self.selectedIds) == 1:
                self.handles.append(ResponsiveHandle(self, card, key))

    def clearPreview(self):
        if self.preview is not None and isValid(self.preview):
            self.preview.hide()
            self.preview.deleteLater()
        self.preview = None
        self.lastTarget = None

    def showPreview(self, host, box, message, lines=(), *, placements=None, moving=()):
        if self.preview is None or not isValid(self.preview) or self.preview.parentWidget() is not host:
            self.clearPreview()
            self.preview = DropOutline(host)
        self.preview.setGeometry(host.rect())
        self.preview.box, self.preview.lines, self.preview.message = box, lines, message
        self.preview.placements = placements or {}
        self.preview.moving = set(moving)
        self.preview.show()
        self.preview.raise_()
        self.preview.update()
        self.w.message.setText(message)

    def target(self, payload, widget, point):
        p = self.w.renderer.config
        key, card = self.tools.componentAt(widget)
        moving = payload.get('moves', [payload['move']] if 'move' in payload else [])
        selected = selectionRoots(p, self.w.pageId, moving)
        excluded = {c.componentId for item in selected for c in self._walk(item)}
        position = widget.mapTo(self.w.renderer, point)
        hosts = self.w.renderer.layoutHosts.get(self.w.pageId, {})
        parentId = None
        if key and key not in excluded:
            item = _component(p, self.w.pageId, key)
            if item.type == 'container':
                parentId = key
            else:
                parentId = parentOf(p, self.w.pageId, key)[0]
        elif key:
            parentId = parentOf(p, self.w.pageId, key)[0]
        host = hosts.get(parentId)
        if host is None:
            raise ValueError('请拖入页面或容器')
        local = host.mapFrom(self.w.renderer, position)
        rules = layoutFor(p, self.w.pageId, parentId)
        if rules.type != 'responsive':
            raise ValueError('目标容器请先选择响应式排列')
        siblings = childrenFor(p, self.w.pageId, parentId)
        guideLines = ()
        if selected and (self.guidesEnabled or self.snapEnabled):
            sourceCard = self.cards()[payload.get('move', selected[0].componentId)]
            hotspot = QPoint(*payload.get('hotspot', [0, 0]))
            references = [Box(self.cards()[item.componentId].x(), self.cards()[item.componentId].y(),
                              self.cards()[item.componentId].width(), self.cards()[item.componentId].height())
                          for item in siblings if item.componentId not in excluded]
            zoom = max(.02, self.w.canvas.getZoomFactor())
            dx, dy, guideLines = snapOffset(Box(local.x() - hotspot.x(), local.y() - hotspot.y(),
                sourceCard.width(), sourceCard.height()), references, 6 / zoom, 400 / zoom)
            if self.snapEnabled and not QApplication.keyboardModifiers() & Qt.AltModifier:
                local += QPoint(round(dx), round(dy))
        self.candidateGuides = guideLines if self.guidesEnabled else ()
        horizontal = rules.direction in ('horizontal', 'wrap')
        available = [item for item in siblings if item.componentId not in excluded]
        args = dict(parentId=parentId, index=len(siblings))
        box = QRect(9, max(9, host.height() - 34), max(24, host.width() - 18), 24)
        message = '放到容器中' if parentId else '放到页面中'
        # The card under the pointer identifies a stable ordered insertion.
        hit = next((item for item in available if self.cards()[item.componentId].geometry().contains(local)), None)
        if hit is not None:
            rect = self.cards()[hit.componentId].geometry()
            index = next(i for i, item in enumerate(siblings) if item.componentId == hit.componentId)
            x, y = local.x() - rect.x(), local.y() - rect.y()
            if not horizontal and (x < rect.width() * .22 or x > rect.width() * .78):
                before = x < rect.width() / 2
                args = dict(parentId=parentId, besideId=hit.componentId, direction='horizontal', before=before)
                box = QRect(rect.left() if before else rect.center().x(), rect.top(), max(24, rect.width() // 2), rect.height())
                message = '并排放置 · 自动形成排列组'
            elif horizontal and (y < rect.height() * .22 or y > rect.height() * .78):
                before = y < rect.height() / 2
                args = dict(parentId=parentId, besideId=hit.componentId, direction='vertical', before=before)
                box = QRect(rect.left(), rect.top() if before else rect.center().y(), rect.width(), max(24, rect.height() // 2))
                message = '上下放置 · 自动形成排列组'
            else:
                before = local.x() < rect.center().x() if horizontal else local.y() < rect.center().y()
                args['index'] = index + (0 if before else 1)
                box = (QRect(rect.left() - 3 if before else rect.right() - 3, rect.top(), 6, rect.height()) if horizontal
                       else QRect(rect.left(), rect.top() - 3 if before else rect.bottom() - 3, rect.width(), 6))
                message = '插入此位置 · 松开应用'
        elif available:
            # Spaces between cards, including wrapped rows, remain valid targets.
            nearest = min(available, key=lambda item: (self.cards()[item.componentId].geometry().center() - local).manhattanLength())
            rect = self.cards()[nearest.componentId].geometry()
            index = next(i for i, item in enumerate(siblings) if item.componentId == nearest.componentId)
            before = local.y() < rect.top() or (local.y() <= rect.bottom() and local.x() < rect.center().x())
            args['index'] = index + (0 if before else 1)
            box = QRect(rect.left(), rect.top() - 3 if before else rect.bottom() - 3, rect.width(), 6)
            message = '插入此位置 · 松开应用'
        if parentId in excluded:
            raise ValueError('容器不能移入自身或子组件')
        return host, box, args, message

    @staticmethod
    def _walk(item):
        from emo_master.core.presentation.models import walkComponents
        return walkComponents([item])

    def drop(self, payload, widget, point, preview=False):
        host, box, args, message = self.target(payload, widget, point)
        keys = payload.get('moves', [payload['move']] if 'move' in payload else [])
        if preview:
            from .responsive_preview import candidateGeometry
            renderer = self.w.renderer
            pageId = self.w.pageId
            root = renderer.layoutHosts[pageId][None]
            scroll = renderer.pages[pageId]
            signature = (args, tuple(keys), payload.get('kind'), id(renderer.config),
                         root.size(), scroll.viewport().size())
            if signature != self.lastTarget:
                # Copy only this page when the ordered insertion/group changes;
                # pointer movement within the same candidate just paints guides.
                page = renderer.config.pages[pageId].model_copy(deep=True)
                draft = renderer.config.model_copy(update={'pages': {**renderer.config.pages, pageId: page}})
                self.candidateIds = placeComponents(draft, pageId, keys=keys, kind=payload.get('kind'), **args)
                prototypes = self.prototypeItems(page, self.candidateIds, payload.get('kind'))
                self.candidateRects, self.candidateSize = candidateGeometry(renderer, pageId, page, prototypes)
            viewport = scroll.viewport()
            offset = QPoint(-min(scroll.horizontalScrollBar().value(), max(0, self.candidateSize.width() - viewport.width())),
                            -min(scroll.verticalScrollBar().value(), max(0, self.candidateSize.height() - viewport.height())))
            moving = set(self.candidateIds) | (set(self.candidateRects) - set(self.cards()))
            placements = {}
            for key, rect in self.candidateRects.items():
                card = self.cards().get(key)
                displayed = rect.translated(offset)
                if key in moving or card is None or displayed != QRect(card.mapTo(viewport, QPoint()), card.size()):
                    placements[key] = displayed
            hostOffset = host.mapTo(viewport, QPoint())
            guides = [(x1 + hostOffset.x(), y1 + hostOffset.y(), x2 + hostOffset.x(), y2 + hostOffset.y())
                      for x1, y1, x2, y2 in self.candidateGuides]
            marker = box.translated(hostOffset)
            if 'besideId' in args:
                marker = QRect()  # The candidate outlines show the actual group and card sizes.
            self.showPreview(viewport, marker, message + ' · 蓝框为放置位置，虚框为其他组件的新位置',
                             guides, placements=placements, moving=moving)
            self.lastTarget = signature
            return args
        selected = self.commands().place(self.w.pageId, keys=keys, kind=payload.get('kind'), **args)
        self.selectedIds = selected
        self.tools.selected = selected[-1] if selected else None
        return args

    def prototypeItems(self, page, keys, kind):
        """Measure a palette component once, using the real renderer in sample mode."""
        if kind is None or kind == 'container':
            return {}
        if self.prototype is None or self.prototypeKind != kind:
            self.disposePrototype()
            from emo_master.core.presentation.models import Page, Presentation, walkComponents
            from emo_master.ui.presentation.renderer import RuntimePages
            from PySide2.QtWidgets import QGraphicsScene
            component = next(item for item in walkComponents(page.components) if item.componentId in keys)
            self.prototype = RuntimePages(Presentation())
            # Graphics proxies propagate scene fonts differently from a plain
            # QWidget parent. Use the same native host without adding preview
            # items to the real canvas or changing its navigation bounds.
            self.prototypeScene = QGraphicsScene(self)
            self.prototypeScene.setFont(self.w.canvas.scene().font())
            self.prototypeScene.setPalette(self.w.canvas.scene().palette())
            self.prototypeScene.addWidget(self.prototype)
            self.prototype.setEditorHost()
            self.prototype.setStyleSheet(self.w.renderer.styleSheet())
            self.prototype.resize(self.w.renderer.size())
            self.prototype.reload(Presentation(defaultPageId='prototype', pageOrder=['prototype'],
                pages={'prototype': Page(name='Prototype', components=[component.model_copy(deep=True)])}))
            self.prototype.setDesignExamples(True)
            self.prototype.ensurePolished()
            self.prototypeKind = kind
        item = self.prototype.layoutHosts['prototype'][None].layout().itemAt(0)
        return {keys[0]: item}

    def disposePrototype(self):
        if self.prototype is not None:
            self.prototype.close()
        if self.prototypeScene is not None:
            self.prototypeScene.deleteLater()  # Owns its proxy and embedded renderer.
        self.prototype = None
        self.prototypeKind = None
        self.prototypeScene = None

    def prepareDrag(self):
        """Accept pending properties before the native drag owns any old widgets."""
        try:
            self.tools.commitPending()
        except ValueError as error:
            self.w.message.setText(str(error))
            return False
        if self.w.renderer.config != self.w.store.snapshot():
            self.w.refresh()
        return True

    def dragImage(self, key, hotspot):
        pixmap = self.cards()[key].grab()
        zoom = self.w.canvas.getZoomFactor()
        size = QSize(max(1, round(pixmap.width() * zoom)), max(1, round(pixmap.height() * zoom)))
        pixmap = pixmap.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return pixmap, QPoint(round(hotspot.x() * zoom), round(hotspot.y() * zoom))

    def eventFilter(self, obj, event):
        if not self.enabled() or not self.w.renderer.editing:
            return False
        kind = event.type()
        if kind == QEvent.Resize:
            for decoration in self.decorations:
                if isValid(decoration) and decoration.parentWidget() is obj:
                    decoration.setGeometry(obj.rect())
        from .tools import MIME, mime
        if kind in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop) and event.mimeData().hasFormat(MIME):
            payload = json.loads(bytes(event.mimeData().data(MIME)))
            if 'choice' in payload:
                return False  # Existing binding validation and confirmation.
            try:
                if kind == QEvent.Drop:
                    self.tools.commitPending()
                    self.drop(payload, obj, event.pos())
                    self.clearPreview()
                    self.tools.laterRefresh()
                else:
                    self.drop(payload, obj, event.pos(), preview=True)
                event.acceptProposedAction()
            except (ValueError, KeyError) as error:
                self.w.message.setText(str(error))
                self.clearPreview()
                if kind == QEvent.Drop:
                    event.ignore()
                else:
                    event.acceptProposedAction()
            return True
        if kind == QEvent.DragLeave:
            self.clearPreview()
        if kind == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            if self.cancel():
                return True
        key, card = self.tools.componentAt(obj)
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and key:
            try:
                if event.modifiers() & Qt.ControlModifier:
                    self.select([k for k in self.selectedIds if k != key] if key in self.selectedIds else [*self.selectedIds, key])
                    self.pointer = None
                else:
                    if key not in self.selectedIds:
                        self.select([key])
                    location = card.mapFrom(self.w.renderer, self.point(event.globalPos()))
                    self.pointer = (key, QPoint(event.globalPos()), location)
                    self.tools.dragStart = (key, QPoint(event.globalPos()))
            except ValueError as error:
                self.w.message.setText(str(error))
            return True
        if kind == QEvent.MouseMove and self.pointer and event.buttons() & Qt.LeftButton:
            key, start, hotspot = self.pointer
            if (event.globalPos() - start).manhattanLength() >= QApplication.startDragDistance():
                self.pointer = None
                self.tools.dragStart = None
                if not self.prepareDrag():
                    return True
                drag = QDrag(self.w.canvas)
                drag.setMimeData(mime({'move': key, 'moves': list(self.selectedIds), 'hotspot': [hotspot.x(), hotspot.y()]}))
                pixmap, dragHotspot = self.dragImage(key, hotspot)
                drag.setPixmap(pixmap)
                drag.setHotSpot(dragHotspot)
                try:
                    drag.exec_(Qt.MoveAction)
                finally:
                    drag.deleteLater()
                    self.cancel()
            return True
        if kind == QEvent.MouseButtonRelease and key:
            self.pointer = None
            self.tools.dragStart = None
            return True
        return False

    def beginResize(self, key, handle, point):
        self.tools.commitPending()
        if self.w.renderer.config != self.w.store.snapshot():
            # Accept pending content before measuring the gesture. Keep the
            # pressed native receiver alive across retirement of the old page.
            oldCard = handle.parentWidget()
            oldCard.removeEventFilter(handle)
            self.handles.remove(handle)
            handle.setParent(self.w.canvasHost)
            self.w.refresh()
            for replacement in self.handles:
                if isValid(replacement):
                    replacement.parentWidget().removeEventFilter(replacement)
                    replacement.hide()
                    replacement.deleteLater()
            card = self.cards()[key]
            handle.setParent(card)
            card.installEventFilter(handle)
            handle.position()
            self.handles = [handle]
        item = _component(self.w.store.snapshot(), self.w.pageId, key)
        card = self.cards()[key]
        self.resize = dict(key=key, handle=handle, start=self.point(point), original=item.responsive.model_copy(),
                           size=card.size(), candidate=item.responsive.model_copy(), card=card)

    def moveResize(self, point):
        if not self.resize:
            return
        state = self.resize
        delta = self.point(point) - state['start']
        if delta.isNull():
            state['candidate'] = state['original'].model_copy()
            self.clearPreview()
            return
        item = state['original'].model_copy()
        item.width = 'content'
        item.alignSelf = 'start'
        item.preferredWidth = max(item.minWidth, min(8192, state['size'].width() + delta.x()))
        item.preferredHeight = max(item.minHeight, min(8192, state['size'].height() + delta.y()))
        state['candidate'] = item
        card = state['card']
        host = card.parentWidget()
        rect = QRect(card.pos(), state['size'])
        rect.setWidth(item.preferredWidth)
        rect.setHeight(item.preferredHeight)
        lines = ()
        if self.guidesEnabled or self.snapEnabled:
            references = [Box(other.x(), other.y(), other.width(), other.height()) for other in
                          self.cards().values() if other.parentWidget() is host and other is not card]
            dx, dy, guideLines = snapOffset(Box(rect.right(), rect.bottom(), 0, 0), references,
                6 / max(.02, self.w.canvas.getZoomFactor()), 400 / max(.02, self.w.canvas.getZoomFactor()))
            if self.snapEnabled and not QApplication.keyboardModifiers() & Qt.AltModifier:
                item.preferredWidth = max(item.minWidth, min(8192, item.preferredWidth + round(dx)))
                item.preferredHeight = max(item.minHeight, min(8192, item.preferredHeight + round(dy)))
                rect.setWidth(item.preferredWidth)
                rect.setHeight(item.preferredHeight)
            lines = guideLines if self.guidesEnabled else ()
        layout = host.layout()
        if hasattr(layout, '_measurements'):
            from dataclasses import replace
            from emo_master.core.presentation.responsive import solveResponsive
            measures = [replace(measure, responsive=item) if measure.key == state['key'] else measure
                        for measure in layout._measurements()]
            margins = layout.contentsMargins()
            boxes, _height = solveResponsive(layout.rules, measures,
                max(0, host.width() - margins.left() - margins.right()),
                max(0, host.height() - margins.top() - margins.bottom()))
            index = next(i for i, measure in enumerate(measures) if measure.key == state['key'])
            box = boxes[index]
            rect = QRect(round(box.x + margins.left()), round(box.y + margins.top()),
                         round(box.width), round(box.height))
        self.showPreview(host, rect, f'期望大小 {item.preferredWidth} × {item.preferredHeight}；松开应用，Esc 取消', lines)

    def finishResize(self, point):
        if not self.resize:
            return
        self.moveResize(point)
        state, self.resize = self.resize, None
        self.clearPreview()
        if state['candidate'] != state['original']:
            self.w.session.editPresentation(lambda p: setattr(_component(p, self.w.pageId, state['key']),
                                                              'responsive', state['candidate']))
            self.tools.laterRefresh()

    def cancel(self):
        active = bool(self.resize or self.pointer or self.preview)
        if self.resize:
            handle = self.resize['handle']
            if isValid(handle):
                handle.releaseMouse()
                handle.releaseKeyboard()
        self.resize = None
        self.pointer = None
        self.clearPreview()
        self.disposePrototype()
        return active

    def group(self, direction):
        self.tools.commitPending()
        key = self.commands().group(self.w.pageId, self.selectedIds, direction)
        self.selectedIds = [key]
        self.tools.selected = key
        self.w.refresh()

    def ungroup(self):
        self.tools.commitPending()
        keys = self.commands().ungroup(self.w.pageId, self.tools.selected)
        self.selectedIds = keys
        self.tools.selected = keys[-1] if keys else None
        self.w.refresh()

    def arrange(self, *, alignment=None, distribute=None):
        self.tools.commitPending()
        self.commands().arrange(self.w.pageId, self.selectedIds, alignment=alignment, distribute=distribute)
        self.w.refresh()

    def blockedReason(self, key):
        if not self.enabled():
            return '请在页面布局中选择响应式排列'
        required = 3 if key.startswith('distribute_') else 2
        if len(self.selectedIds) < required:
            return f'请按住 Ctrl 选择至少 {required} 个组件'
        p = self.w.renderer.config
        parents = {parentOf(p, self.w.pageId, selected)[0] for selected in self.selectedIds}
        if len(parents) != 1:
            return '请先将跨容器的组件编组'
        parentId = parents.pop()
        rules = layoutFor(p, self.w.pageId, parentId)
        if rules.type != 'responsive':
            return '请先设置父容器的响应式排列'
        horizontal = rules.direction in ('horizontal', 'wrap')
        full = len(self.selectedIds) == len(childrenFor(p, self.w.pageId, parentId))
        if key.startswith('distribute_'):
            if (key.endswith('horizontal')) != horizontal or not full:
                return '请将所选组件编为对应方向的完整排列组'
        else:
            xaxis = key in ('align_left', 'align_hcenter', 'align_right')
            if xaxis == horizontal and not full:
                return '此方向作用于整组；请先将所选组件编组'
        return ''
