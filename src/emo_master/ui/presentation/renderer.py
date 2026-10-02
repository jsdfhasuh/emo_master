"""A reusable QWidget shell. No Runtime, Designer or algorithm imports."""
from collections import deque
from dataclasses import replace
import json
import math
import time
from types import MappingProxyType

from PySide2.QtCore import Qt, QRect, Signal
from PySide2.QtGui import QPainter, QColor, QImage, QFontDatabase
from PySide2.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QScrollArea, QFrame, QSizePolicy, QApplication,
)

from emo_master.core.presentation.models import Presentation, walkComponents
from emo_master.core.presentation.values import readValue
from emo_master.core.project.snapshots import captureDefinition, revisionOf
from emo_master.core.presentation.validation import pageScopes
from emo_master.ui.presentation.images import assertGuiThread, ownedImage
from emo_master.ui.presentation.table import CollectionView


COLORS = {'default': '#223247', 'neutral': '#576477', 'green': '#176f42',
          'red': '#af2734', 'amber': '#925900'}
SURFACE_LIMIT = 16 * 1024 * 1024


def surfaceBounds(width, height, ratio):
    """Retain the 16 MiB reservation, adapting its aspect to an actual target."""
    if width <= 0 or height <= 0 or not math.isfinite(ratio) or ratio <= 0:
        raise ValueError('窗口尺寸与像素比例必须为正数')
    if math.ceil(ratio) ** 2 * 4 > SURFACE_LIMIT:
        raise ValueError('像素比例超出单窗口表面预算')
    scale = min(1., math.sqrt(SURFACE_LIMIT / (math.ceil(width * ratio) * math.ceil(height * ratio) * 4)))
    width, height = max(1, int(width * scale)), max(1, int(height * scale))
    while math.ceil(width * ratio) * math.ceil(height * ratio) * 4 > SURFACE_LIMIT:
        width -= 1
    return width, height


def appearanceStyle(props):
    # Every interpolated token is a validated enum or bounded integer.
    family = {'system': QApplication.font().family(), 'sans': 'sans-serif',
              'serif': 'serif', 'monospace': QFontDatabase.systemFont(QFontDatabase.FixedFont).family()}[props.fontFamily]
    family = family.replace("'", '').replace(';', '')
    size = f'font-size:{props.fontSize}px;' if props.fontSize else ''
    return f"font-family:'{family}';{size}font-weight:{props.fontWeight};color:{COLORS[props.textColor]};"


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
    designExamplesChanged = Signal(bool)

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
        self.simulationState = None
        self.detached = False
        self.editing = False
        self.editorHost = False
        self.designExamples = False
        self._designImage = QImage()
        self.currentPageId = None
        self.pages = {}
        self.widgets = {}
        self.displayed = {}
        self.lastView = None
        self.frozen = None
        self.frozenGeneration = None
        self.records = deque(maxlen=256)
        self._recordedPaints = deque(maxlen=128)
        self._screenWindow = None
        self._screenSource = None
        self.setWindowTitle(label)
        self.resize(1060, 720)
        self.setMinimumSize(460, 360)
        self.fitToAvailableScreen(resize=False)
        self.setStyleSheet("QWidget {font-family: 'Microsoft YaHei'; font-size: 13px; color:#223247;} "
            "QWidget#runtimePages {background:#f5f7fa;} QFrame#card {background:white; border:1px solid #dce3eb; border-radius:6px;} "
            "QPushButton {padding:9px 14px; background:#e3edf8; border:1px solid #c4d7ec; border-radius:4px;} "
            "QPushButton:checked {background:#2168ae; color:white;} QLabel#number {font-size:36px; font-weight:600;}")
        self.setObjectName("runtimePages")
        layout = QVBoxLayout(self)
        self.banner = QLabel(label)
        self.banner.setTextFormat(Qt.PlainText)
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        self.status = QLabel("未连接 · 等待明确选择任务")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.jobStatus = QLabel('任务执行状态不可用 · 尚无已核验的任务身份')
        self.jobStatus.setTextFormat(Qt.PlainText)
        self.jobStatus.setWordWrap(True)
        layout.addWidget(self.jobStatus)
        controls = QHBoxLayout()
        self.resumeButton = QPushButton("恢复实时")
        self.resumeButton.clicked.connect(self.resumeLive)
        controls.addWidget(self.resumeButton)
        self.fitButton = QPushButton('适配当前屏幕')
        self.fitButton.clicked.connect(lambda: self.fitToAvailableScreen())
        controls.addWidget(self.fitButton)
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
        self.identity.setTextFormat(Qt.PlainText)
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.identity.setWordWrap(True)
        layout.addWidget(self.identity)
        if self.config.defaultPageId:
            self.navigate(self.config.defaultPageId)
        else:
            self.stack.addWidget(QLabel("此项目尚无运行页面"))
        if hub:
            hub.attach(self)

    def fitToAvailableScreen(self, *, resize=True, screen=None):
        if self.editorHost:
            self.setMinimumSize(0, 0)
            area = self.parentWidget().size() if self.parentWidget() else self.size()
            width, height = surfaceBounds(area.width(), area.height(), self.devicePixelRatioF())
            self.setMaximumSize(width, height)
            if resize:
                self.updateGeometry()
            return
        screen = screen or self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1060, 720)
        ratio = max(self.devicePixelRatioF(), screen.devicePixelRatio() if screen else 1.)
        width, height = surfaceBounds(available.width(), available.height(), ratio)
        self.setMinimumSize(min(460, width), min(360, height))
        self.setMaximumSize(width, height)
        if resize:
            self.resize(width, height)
        if resize and hasattr(self, 'status'):
            self.status.setText(f'当前屏幕适配 {width}×{height} · 16 MiB 表面预算；现场目标规格尚待确认')

    def _screenChanged(self, screen=None):
        screen = screen or self.screen()
        if screen is not self._screenSource:
            if self._screenSource is not None:
                for name in ('availableGeometryChanged', 'logicalDotsPerInchChanged', 'physicalDotsPerInchChanged'):
                    try:
                        getattr(self._screenSource, name).disconnect(self._screenMetricsChanged)
                    except RuntimeError:
                        pass
            self._screenSource = screen
            if screen is not None:
                for name in ('availableGeometryChanged', 'logicalDotsPerInchChanged', 'physicalDotsPerInchChanged'):
                    getattr(screen, name).connect(self._screenMetricsChanged)
        self.fitToAvailableScreen(resize=False, screen=screen)

    def _screenMetricsChanged(self, _value=None):
        self.fitToAvailableScreen(resize=False)

    def surfaceBytes(self):
        ratio = self.devicePixelRatioF()
        return math.ceil(self.width() * ratio) * math.ceil(self.height() * ratio) * 4

    def setEditorHost(self):
        """Keep the embedded editor native-sized, separate from screen fitting."""
        self.editorHost = True
        self.setMinimumSize(0, 0)
        self.setStyleSheet(self.styleSheet().replace("font-family: 'Microsoft YaHei'; font-size: 13px; ", ''))
        self.fitButton.setText('适配编辑区')

    def setDesignExamples(self, enabled):
        if enabled and (not self.editorHost or self.hub or self.captureCoverage is not None):
            raise ValueError('设计示例仅供未连接任务的编辑器使用')
        from . import design_examples
        if not enabled and self.designExamples:
            design_examples.clear(self)
        changed = self.designExamples != bool(enabled)
        self.designExamples = bool(enabled)
        if enabled:
            self.simulationState = None
            self.lastView = None
            design_examples.populate(self)
        if changed:
            self.designExamplesChanged.emit(self.designExamples)

    def editorResourceUsage(self):
        return {'design_image_bytes': self._designImage.sizeInBytes(),
                'table_bytes': sum(w.model.bytesHeld for rows in self.widgets.values()
                                   for _c, w in rows.values() if isinstance(w, CollectionView)),
                'surface_bytes': self.surfaceBytes()}

    def setSimulationState(self, state):
        self.setDesignExamples(False)
        if state not in (None, 'OK', 'NG', 'WAITING', 'ERROR'):
            raise ValueError('未知离线模拟状态')
        if state is not None and (self.hub or self.captureCoverage is not None):
            raise ValueError('请先断开任务观察，再使用离线模拟；模拟不读取 Runtime')
        self.simulationState = state
        self.displayed.clear()
        self.lastView = None
        from emo_master.clients.runtime.view_state import SessionView
        if state is not None:
            self.submit(SessionView(0, 0, 'offline-simulation', 'offline-simulation', 'CONNECTED',
                '仅检查界面，不读取或写入 Runtime / 计数', {}, {}, {}))
        else:
            self.banner.setText('离线模拟布局预览 · 无业务结果')
            self.submit(SessionView(0, 0, '', '', 'OFFLINE', '无当前结果', {}, {}, {}))

    def _simulationValue(self, component):
        if self.simulationState == 'WAITING':
            return None, None, '模拟 · 等待触发 / 尚无结果'
        if self.simulationState == 'ERROR':
            return None, None, '模拟 · NODE_FAILED · 示例错误'
        if component.type == 'image':
            return None, None, '模拟图像区域 · ' + self.simulationState
        if component.type == 'indicator':
            color = 'green' if self.simulationState == 'OK' else 'red'
            key = next((key for key, style in component.props.indicatorStates.items() if style.color == color), None)
            if key is None:
                return None, None, '模拟 · 未配置 ' + self.simulationState + ' 对应颜色映射'
            return None, json.loads(key), ''
        if component.type == 'table':
            return None, [], ''
        return None, (1 if self.simulationState == 'OK' else 0) if component.type == 'number' else '模拟 · ' + self.simulationState, ''

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
            self.hub.updateImageDemand()
            self.hub.lastToken = None
            self.submit(self.hub.session.readSnapshot())
        elif self.simulationState:
            self.setSimulationState(self.simulationState)

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
        if self.editorHost:
            layout.setAlignment(Qt.AlignTop)
        for column in range(grid.columns):
            layout.setColumnStretch(column, 1)
        for component in components:
            card = QFrame()
            card.setObjectName("card")
            if self.editorHost:
                card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            card.setProperty('componentId', component.componentId)
            backgrounds = {'plain': 'white', 'soft': '#edf3fa', 'outlined': 'transparent'}
            card.setStyleSheet('QFrame#card {background:' + backgrounds[component.props.cardStyle] +
                ';border:1px solid #c4d0de;border-radius:6px;}')
            if component.type == "container":
                self._children(card, component.children, component.grid, pageId)
                if self.editorHost and not component.children:
                    card.setMinimumHeight(90)
                    card.layout().addWidget(QLabel(component.props.title or '容器 · 拖入组件'), 0, 0)
            else:
                box = QVBoxLayout(card)
                if component.props.title:
                    title = QLabel(component.props.title)
                    title.setTextFormat(Qt.PlainText)
                    title.setStyleSheet(appearanceStyle(component.props))
                    title.setWordWrap(True)
                    box.addWidget(title)
                if component.type == "image":
                    widget = ImageView()
                    if self.editorHost:
                        widget.setMinimumSize(100, 96)
                        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
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
                    widget.setTextFormat(Qt.PlainText)
                    widget.setWordWrap(True)
                    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    if component.type == "number":
                        widget.setObjectName("number")
                box.addWidget(widget, 1)
                if self.editorHost and component.type in ('image', 'number', 'text', 'indicator', 'table'):
                    source = self.config.dataSources.get(next(iter(component.bindings.values()), ''))
                    note = ('来源: ' + str(source.port) if source else
                            '来源缺失' if component.bindings else '未绑定 · 拖入流程输出')
                    badge = QLabel(note)
                    badge.setObjectName('bindingHint')
                    badge.setWordWrap(True)
                    badge.setToolTip(str(source) if source else note)
                    box.addWidget(badge)
                widget.setStyleSheet(appearanceStyle(component.props))
                self.widgets[pageId][component.componentId] = (component, widget)
            p = component.layout
            layout.addWidget(card, p.row, p.column, p.rowSpan, p.columnSpan)

    def imageSources(self, pageId=None):
        page = self.config.pages.get(pageId or self.currentPageId)
        if page is None:
            return set()
        return {sourceId for component in walkComponents(page.components) if component.type == 'image'
                for sourceId in component.bindings.values() if sourceId in self.config.dataSources
                and self.config.dataSources[sourceId].expectedType == 'image'
                and (self.captureCoverage is None or not self.captureCoverage.sourceProblem(self.config, sourceId))}

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
        if self.designExamples:
            from .design_examples import populate
            populate(self)
        if self.hub:
            self.hub.updateImageDemand()
            if self.lastView is not None:
                self.submit(self.hub.session.readSnapshot())
        elif self.lastView is not None:
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
                self.frozen = self.hub.freeze(self, self.displayed[scopeId], self.lastView.generation,
                                              sourceIds=self.imageSources(target))
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
        elif submit and self.simulationState:
            self.setSimulationState(self.simulationState)

    def _value(self, component, view):
        if self.simulationState:
            return self._simulationValue(component)
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
            if source.resultScopeId in getattr(view, 'expiredScopes', {}):
                return None, None, 'RESOURCE_EXPIRED · 此作用域结果已因资源限额过期，等待下次结果'
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
            state = scope.imageStates.get(sourceId)
            message = "当前结果图像准备中" if state == "LOADING" else "图像按页面需要读取" if state == "NOT_REQUESTED" else "IMAGE_UNAVAILABLE"
            return scope, scope.images.get(sourceId), "" if sourceId in scope.images else message
        if value.valueJson is None:
            return scope, None, "绑定类型不支持"
        return scope, readValue(value.valueJson), ""

    def setCaptureCoverage(self, coverage):
        """Only validated frozen metadata permits partial source compatibility."""
        if coverage is not None:
            self.setDesignExamples(False)
        from emo_master.core.presentation.coverage import CaptureCoverage
        if coverage is not None and not isinstance(coverage, CaptureCoverage):
            raise TypeError('validated capture coverage required')
        self.captureCoverage = coverage
        if coverage is not None:
            self.simulationState = None
        if self.hub:
            self.hub.updateImageDemand()
            self.hub.lastToken = None
            self.submit(self.hub.session.readSnapshot())

    def submit(self, view):
        self.setDesignExamples(False)
        assertGuiThread()
        if self.detached:
            return
        self.lastView = view
        self.status.setText(f"{view.connection} · {view.detail or '连接健康，等待触发'}")
        from emo_master.ui.presentation.job_status import jobStatusText
        # Execution remains live even when business values below use a pin.
        self.jobStatus.setText(jobStatusText(getattr(view, 'job', None)))
        if self.simulationState:
            self.banner.setText('离线模拟 · ' + self.simulationState + ' · 示例状态，不代表真实检测')
            self.status.setText('模拟 · 不读取或写入 Runtime / 计数')
            self.jobStatus.setText('离线模拟 · 无真实任务执行状态')
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
            self.modeLabel.setText('模拟 · 无可冻结的真实结果' if self.simulationState else "实时 · 下一件处理中" if any(view.started.get(s, 0) > r.result.identity.resultOrdinal for s, r in view.scopes.items())
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
                    widget.setText(error or (component.props.emptyText if value is None else
                        (f"{value:.{component.props.decimals}f}" if type(value) is float else str(value)) + component.props.unit))
                elif component.type == "text":
                    text = error or (component.props.emptyText if value is None else json.dumps(value, ensure_ascii=False))
                    widget.setText(text if len(text) <= 4096 else text[:4096] + '…（显示截断）')
                elif component.type == 'indicator':
                    state = component.props.indicatorStates.get(json.dumps(value, ensure_ascii=False)) if not error else None
                    widget.setStyleSheet(appearanceStyle(component.props) + 'color:' + COLORS[state.color if state else 'neutral'])
                    widget.setText(error or (component.props.emptyText if value is None else state.text if state else '未映射判定值: ' + json.dumps(value, ensure_ascii=False)))
                elif component.type == 'runtime_status':
                    widget.setText(error or str(value))
                elif isinstance(widget, CollectionView):
                    widget.submit(value, scope.result.identity.resultKey if scope else 'simulation-' + self.simulationState if self.simulationState else '', error)
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
        handle = self.window().windowHandle()
        if handle is not None and handle is not self._screenWindow:
            if self._screenWindow is not None:
                try:
                    self._screenWindow.screenChanged.disconnect(self._screenChanged)
                except RuntimeError:
                    pass
            self._screenWindow = handle
            handle.screenChanged.connect(self._screenChanged)
        self._screenChanged()
        if self.designExamples:
            from .design_examples import populate
            populate(self)
        if self.hub and not self.detached:
            self.hub.updateImageDemand()
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
        if self.hub:
            self.hub.updateImageDemand()

    def closeEvent(self, event):
        self.setDesignExamples(False)
        self.detached = True
        if self._screenWindow is not None:
            try:
                self._screenWindow.screenChanged.disconnect(self._screenChanged)
            except RuntimeError:
                pass
            self._screenWindow = None
        if self._screenSource is not None:
            for name in ('availableGeometryChanged', 'logicalDotsPerInchChanged', 'physicalDotsPerInchChanged'):
                try:
                    getattr(self._screenSource, name).disconnect(self._screenMetricsChanged)
                except RuntimeError:
                    pass
            self._screenSource = None
        if self.hub:
            hub, self.hub = self.hub, None
            hub.detach(self)
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
