"""A reusable QWidget shell. No Runtime, Designer or algorithm imports."""
from collections import deque
from dataclasses import replace
import json
import time
from types import MappingProxyType

from PySide2.QtCore import Qt, QRect
from PySide2.QtGui import QPainter, QColor, QImage
from PySide2.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QScrollArea, QFrame, QSizePolicy,
)

from emo_master.core.presentation.models import Presentation
from emo_master.core.presentation.values import readValue
from emo_master.core.project.snapshots import captureDefinition, revisionOf
from emo_master.core.presentation.validation import pageScopes
from emo_master.ui.presentation.images import assertGuiThread, ownedImage
from emo_master.ui.presentation.table import CollectionView


class ImageView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.message = "尚无结果"
        self.key = ""
        self.painted = None
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setImage(self, image, key, message=""):
        assertGuiThread()
        self.image, self.key, self.message = image, key, message
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e9eef4"))
        if not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            x, y = (self.width() - size.width()) // 2, (self.height() - size.height()) // 2
            painter.drawImage(QRect(x, y, size.width(), size.height()), self.image)
            if self.painted:
                self.painted(self.key, time.perf_counter_ns())
        else:
            painter.setPen(QColor("#576477"))
            painter.drawText(self.rect().adjusted(14, 14, -14, -14), Qt.AlignCenter | Qt.TextWordWrap, self.message)
        painter.end()


class RuntimePages(QWidget):
    """Stable page IDs, bounded lazy pages, one atomic submission per scope.

    A hub supplies snapshots and shared image conversion; manual submit remains
    useful for clearly labelled simulation and deterministic widget tests.
    """
    def __init__(self, presentation, *, hub=None, label="只读运行页面", parent=None):
        super().__init__(parent)
        assertGuiThread()
        self.config = Presentation.model_validate(presentation.model_dump()) if presentation is not None else Presentation()
        try:
            self.expectedCapture = revisionOf(captureDefinition(self.config))
        except KeyError:
            self.expectedCapture = None
        self.hub = hub
        self.captureCoverage = None
        self.detached = False
        self.editing = False
        self.currentPageId = None
        self.pages = {}
        self.widgets = {}
        self.displayed = {}
        self.lastView = None
        self.frozen = None
        self.frozenGeneration = None
        self.records = deque(maxlen=256)
        self._recordedPaints = deque(maxlen=128)
        self.setWindowTitle(label)
        self.resize(1060, 720)
        self.setMinimumSize(460, 360)
        self.setMaximumSize(1600, 1000)
        # Bound each Qt backing surface to 16MiB even at high DPI.
        ratio = max(1, self.devicePixelRatioF() / 1.5)
        self.setMaximumSize(int(1600 / ratio), int(1000 / ratio))
        self.setStyleSheet("QWidget {font-family: 'Microsoft YaHei'; font-size: 13px; color:#223247;} "
            "QWidget#runtimePages {background:#f5f7fa;} QFrame#card {background:white; border:1px solid #dce3eb; border-radius:6px;} "
            "QPushButton {padding:9px 14px; background:#e3edf8; border:1px solid #c4d7ec; border-radius:4px;} "
            "QPushButton:checked {background:#2168ae; color:white;} QLabel#number {font-size:36px; font-weight:600;}")
        self.setObjectName("runtimePages")
        layout = QVBoxLayout(self)
        self.banner = QLabel(label)
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.status = QLabel("未连接 · 等待明确选择任务")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.resumeButton = QPushButton("恢复实时")
        self.resumeButton.clicked.connect(self.resumeLive)
        controls.addWidget(self.resumeButton)
        self.modeLabel = QLabel("实时 · 冻结仅影响本窗口")
        controls.addWidget(self.modeLabel, 1)
        layout.addLayout(controls)
        navigation = QHBoxLayout()
        self.navigationLayout = navigation
        self.buttons = {}
        for pageId in self.config.pageOrder:
            button = QPushButton(self.config.pages[pageId].name)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, key=pageId: self.navigate(key))
            navigation.addWidget(button)
            self.buttons[pageId] = button
        layout.addLayout(navigation)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.identity = QLabel("尚无已显示结果")
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        if self.config.defaultPageId:
            self.navigate(self.config.defaultPageId)
        else:
            self.stack.addWidget(QLabel("此项目尚无运行页面"))
        if hub:
            hub.attach(self)

    def reload(self, presentation):
        """Replace only widgets/configuration; retain the borrowed hub/session."""
        assertGuiThread()
        config = Presentation.model_validate(presentation.model_dump())
        pageId = self.currentPageId
        self.resumeLive(submit=False)
        self.displayed.clear()
        self.lastView = None
        for rows in self.widgets.values():
            for _component, widget in rows.values():
                if isinstance(widget, ImageView):
                    widget.painted = None
                    widget.setImage(QImage(), '')
                elif isinstance(widget, CollectionView):
                    widget.clear('配置更新')
        while self.stack.count():
            widget = self.stack.widget(0)
            widget.hide()
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self.pages.clear()
        self.widgets.clear()
        while self.navigationLayout.count():
            widget = self.navigationLayout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.buttons.clear()
        self.config = config
        try:
            self.expectedCapture = revisionOf(captureDefinition(config))
        except KeyError:
            self.expectedCapture = None
        self.currentPageId = None
        for key in config.pageOrder:
            button = QPushButton(config.pages[key].name)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, target=key: self.navigate(target))
            self.navigationLayout.addWidget(button)
            self.buttons[key] = button
        target = pageId if pageId in config.pages else config.defaultPageId
        if target:
            self.navigate(target)
        else:
            self.stack.addWidget(QLabel('此项目尚无运行页面'))
        if self.hub:
            self.hub.lastToken = None
            self.submit(self.hub.session.readSnapshot())

    def _build(self, pageId):
        # Retire a hidden page before allocating its replacement, so its table
        # quota does not reject otherwise valid controls on the new page.
        if len(self.pages) >= 2:
            old = next(key for key in self.pages if key != self.currentPageId)
            widget = self.pages.pop(old)
            self.widgets.pop(old)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        page = self.config.pages[pageId]
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setProperty('pageGrid', pageId)
        body.setMinimumHeight(240)
        self.widgets[pageId] = {}
        self._children(body, page.components, page.layout, pageId)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        self.pages[pageId] = scroll

    def _children(self, parent, components, grid, pageId):
        layout = QGridLayout(parent)
        layout.setSpacing(grid.spacing)
        for column in range(grid.columns):
            layout.setColumnStretch(column, 1)
        for component in components:
            card = QFrame()
            card.setObjectName("card")
            card.setProperty('componentId', component.componentId)
            if component.type == "container":
                self._children(card, component.children, component.grid, pageId)
            else:
                box = QVBoxLayout(card)
                if component.props.title:
                    title = QLabel(component.props.title)
                    title.setWordWrap(True)
                    box.addWidget(title)
                if component.type == "image":
                    widget = ImageView()
                    if component.bindings:
                        widget.message = '已绑定 · 等待明确任务结果'
                    widget.painted = self._painted
                elif component.type == 'table' and sum(isinstance(w, CollectionView) for rows in self.widgets.values() for _c, w in rows.values()) < 4:
                    widget = CollectionView(component.props)
                elif component.type == "navigation_button":
                    widget = QPushButton(component.props.text or component.props.title or "导航")
                    widget.clicked.connect(lambda _checked=False, item=component: self.act(item.actions.get("clicked")))
                else:
                    widget = QLabel(component.props.text if component.type == "text" and not component.bindings
                                    else '已绑定 · 等待明确任务结果' if component.bindings else "未绑定")
                    widget.setWordWrap(True)
                    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    if component.type == "number":
                        widget.setObjectName("number")
                box.addWidget(widget, 1)
                self.widgets[pageId][component.componentId] = (component, widget)
            p = component.layout
            layout.addWidget(card, p.row, p.column, p.rowSpan, p.columnSpan)

    def navigate(self, pageId, keepFrozen=False):
        assertGuiThread()
        if pageId not in self.config.pages:
            self.status.setText("导航目标不存在")
            return
        if not keepFrozen and self.frozen:
            self.resumeLive(submit=False)
        if self.currentPageId != pageId and self.currentPageId in self.widgets:
            for _component, widget in self.widgets[self.currentPageId].values():
                if isinstance(widget, ImageView):
                    widget.setImage(QImage(), "", "隐藏页面")
                elif isinstance(widget, CollectionView):
                    widget.clear('隐藏页面')
        if pageId not in self.pages:
            self._build(pageId)
        self.currentPageId = pageId
        self.stack.setCurrentWidget(self.pages[pageId])
        for key, button in self.buttons.items():
            button.setChecked(key == pageId)
        if self.lastView is not None:
            self.submit(self.lastView)

    def act(self, action):
        if self.editing:
            return
        if action is None:
            self.status.setText("此按钮未配置动作")
        elif action.type == "navigate" and action.context == "live":
            self.navigate(action.pageId)
        elif action.type == "resume_live":
            self.resumeLive()
        else:
            scopeId = action.resultScopeId
            target = action.pageId if action.type == "navigate" else self.currentPageId
            if not self.hub or scopeId not in self.displayed or target not in self.config.pages or scopeId not in pageScopes(self.config, target):
                self.status.setText("所选作用域没有可锁定的已显示结果")
                return
            try:
                # This is the committed GUI value, deliberately not session.latest.
                self.frozen = self.hub.freeze(self, self.displayed[scopeId], self.lastView.generation)
                self.frozenGeneration = self.lastView.generation
                self.navigate(target, keepFrozen=True)
            except ValueError as error:
                self.status.setText(str(error))

    def resumeLive(self, _checked=False, *, submit=True):
        if self.hub:
            self.hub.resume(self)
        self.frozen = self.frozenGeneration = None
        self.modeLabel.setText("实时 · 冻结仅影响本窗口")
        if submit and self.hub:
            self.submit(self.hub.session.readSnapshot())

    def _value(self, component, view):
        if not component.bindings:
            return None, None, "未绑定"
        sourceId = next(iter(component.bindings.values()))
        source = self.config.dataSources.get(sourceId)
        if source is None:
            return None, None, "来源不存在"
        if source.kind not in ("node_output", "workflow_output"):
            return None, None, "不支持此来源: " + source.kind
        if self.captureCoverage is not None:
            problem = self.captureCoverage.sourceProblem(self.config, sourceId)
            if problem:
                return None, None, problem
        scope = view.scopes.get(source.resultScopeId)
        if scope is None:
            return None, None, "当前结果准备中" if source.resultScopeId in view.loading else "等待触发 / 尚无结果"
        if self.captureCoverage is not None:
            if not self.captureCoverage.matches(scope.result.identity):
                return None, None, "CAPTURE_IDENTITY_MISMATCH · 任务代际已改变，请重新选择任务"
        elif self.expectedCapture is None or scope.result.identity.capturePlanRevision != self.expectedCapture:
            return None, None, "CAPTURE_REVISION_MISMATCH · 绑定变更需明确启动新任务"
        value = next((item for item in scope.result.sources if item.sourceId == sourceId), None)
        if value is None:
            return scope, None, "SOURCE_MISSING"
        if value.state != "AVAILABLE":
            return scope, None, value.reasonCode or value.reason
        if sourceId in scope.failures:
            return scope, None, scope.failures[sourceId]
        if component.type == "image":
            return scope, scope.images.get(sourceId), "" if sourceId in scope.images else "IMAGE_UNAVAILABLE"
        if value.valueJson is None:
            return scope, None, "绑定类型不支持"
        return scope, readValue(value.valueJson), ""

    def setCaptureCoverage(self, coverage):
        """Only validated frozen metadata permits partial source compatibility."""
        from emo_master.core.presentation.coverage import CaptureCoverage
        if coverage is not None and not isinstance(coverage, CaptureCoverage):
            raise TypeError('validated capture coverage required')
        self.captureCoverage = coverage
        if self.hub:
            self.hub.lastToken = None
            self.submit(self.hub.session.readSnapshot())

    def submit(self, view):
        assertGuiThread()
        if self.detached:
            return
        self.lastView = view
        self.status.setText(f"{view.connection} · {view.detail or '连接健康，等待触发'}")
        if self.frozen and view.generation != self.frozenGeneration:
            self.resumeLive(submit=False)
        if self.frozen:
            pin = self.hub.session.pins().read(self.frozen)
            self.modeLabel.setText(f"冻结 · {pin.state} · 不暂停检测，不自动续租")
            if pin.scope is not None:
                scope = pin.scope
                view = replace(view, connection="CONNECTED", scopes=MappingProxyType({scope.result.identity.resultScopeId: scope}), loading=MappingProxyType({}))
            else:
                view = replace(view, connection="PIN_EXPIRED", detail=pin.error,
                               scopes=MappingProxyType({}), loading=MappingProxyType({}))
        else:
            self.modeLabel.setText("实时 · 下一件处理中" if any(view.started.get(s, 0) > r.result.identity.resultOrdinal for s, r in view.scopes.items())
                                   else "实时 · 等待触发")
        page = self.currentPageId
        if page is None:
            return
        shown = {}
        self.setUpdatesEnabled(False)
        try:
            for component, widget in self.widgets[page].values():
                if component.type == "navigation_button":
                    continue
                if component.type == "text" and not component.bindings:
                    continue
                if component.type == 'runtime_status' and not component.bindings:
                    widget.setText('客户端连接：' + self.lastView.connection + ' · ' + self.lastView.detail)
                    continue
                scope, value, error = self._value(component, view)
                if view.connection != "CONNECTED":
                    value, error, scope = None, view.connection + ": " + view.detail, None
                if scope:
                    shown[scope.result.identity.resultScopeId] = scope
                if isinstance(widget, ImageView):
                    image = QImage()
                    if not error and value is not None:
                        try:
                            image = self.hub.image(scope, next(iter(component.bindings.values())), value) if self.hub else ownedImage(value)
                        except ValueError as problem:
                            error = str(problem)
                    widget.setImage(image, scope.result.identity.resultKey if scope else "", error)
                elif component.type == "number":
                    widget.setText(error or ("null" if value is None else
                        (f"{value:.{component.props.decimals}f}" if type(value) is float else str(value)) + component.props.unit))
                elif component.type == "text":
                    text = error or json.dumps(value, ensure_ascii=False)
                    widget.setText(text if len(text) <= 4096 else text[:4096] + '…（显示截断）')
                elif component.type == 'indicator':
                    state = component.props.indicatorStates.get(json.dumps(value, ensure_ascii=False)) if not error else None
                    colors = {'neutral': '#576477', 'green': '#176f42', 'red': '#af2734', 'amber': '#925900'}
                    widget.setStyleSheet('color:' + colors[state.color if state else 'neutral'])
                    widget.setText(error or (state.text if state else '未映射判定值: ' + json.dumps(value, ensure_ascii=False)))
                elif component.type == 'runtime_status':
                    widget.setText(error or str(value))
                elif isinstance(widget, CollectionView):
                    widget.submit(value, scope.result.identity.resultKey if scope else '', error)
                elif component.type == 'table':
                    widget.setText('UI_TABLE_BUDGET · 每窗口最多4个已创建表格')
                else:
                    widget.setText("组件尚未支持: " + component.type)
            # Record only after all widgets have committed this scope together.
            self.displayed = shown
            self.identity.setText(" | ".join(f"{scope}: #{item.result.identity.resultOrdinal} · {item.result.status} · {item.result.identity.resultKey}"
                                             for scope, item in shown.items()) or "尚无当前有效结果")
            for scope in shown.values():
                self.records.append({"key": scope.result.identity.resultKey, "ready_ns": scope.readyNs,
                                     "gui_ns": time.perf_counter_ns(), "scope_end_ns": scope.result.timing.scopeEndedNs if scope.result.timing else None})
        finally:
            self.setUpdatesEnabled(True)

    def _painted(self, key, stamp):
        if key and key not in self._recordedPaints:
            self._recordedPaints.append(key)
            for record in reversed(self.records):
                if record["key"] == key:
                    record["paint_ns"] = stamp
                    break

    def showEvent(self, event):
        super().showEvent(event)
        if self.hub and not self.detached:
            self.submit(self.hub.session.readSnapshot())

    def hideEvent(self, event):
        self.displayed.clear()
        self.lastView = None
        for rows in self.widgets.values():
            for _component, widget in rows.values():
                if isinstance(widget, ImageView):
                    widget.setImage(QImage(), "", "隐藏页面")
                elif isinstance(widget, CollectionView):
                    widget.clear('隐藏页面')
        super().hideEvent(event)

    def closeEvent(self, event):
        self.detached = True
        if self.hub:
            self.hub.detach(self)
        self.displayed.clear()
        self.lastView = self.frozen = None
        for rows in self.widgets.values():
            for _component, widget in rows.values():
                if isinstance(widget, ImageView):
                    widget.painted = None
                    widget.setImage(QImage(), "")
                elif isinstance(widget, CollectionView):
                    widget.clear('已关闭')
        super().closeEvent(event)
