"""A reusable QWidget shell. No Runtime, Designer or algorithm imports."""
from collections import deque
from dataclasses import replace
import json
import math
import sys
import time
from types import MappingProxyType

from PySide2.QtCore import Qt, QRect, QSize, Signal, QEvent
from PySide2.QtGui import QPainter, QColor, QFont, QImage, QIcon, QPixmap
from PySide2.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QStackedWidget, QFrame, QSizePolicy, QApplication, QMenu, QToolButton,
)

from emo_master.core.presentation.models import ImageView as ImageConfig
from emo_master.core.presentation.models import PageAppearance, PageShell, Presentation, ResponsiveLayout, walkComponents
from emo_master.core.presentation.messages import budgetMessage
from emo_master.core.presentation.values import readValue
from emo_master.core.project.snapshots import captureDefinition, revisionOf
from emo_master.core.presentation.validation import pageScopes
from emo_master.ui.presentation.images import assertGuiThread, ownedImage
from emo_master.ui.presentation.table import CollectionView
from emo_master.ui.presentation.page_navigation import PageNavigation
from emo_master.ui.presentation.page_scroll import PageScrollArea
from emo_master.ui.presentation.appearance import THEME, ValueView, plainLabel, roleStyle, surfaceStyle


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
    return roleStyle(props)


class ImageView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.message = "尚无结果"
        self.key = ""
        self.painted = None
        self.setViewConfig(None)

    def setViewConfig(self, config, height=0):
        self.config = config or ImageConfig()
        self.fixedHeight = height or 180
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(self.config.sizing == 'aspect')
        self.setSizePolicy(policy)
        self.updateGeometry()

    def aspectRatio(self):
        if self.config.aspectRatio is not None:
            return self.config.aspectRatio
        return self.image.width() / self.image.height() if not self.image.isNull() else 4 / 3

    def heightForWidth(self, width):
        return round(min(8192, width / self.aspectRatio())) if self.config.sizing == 'aspect' else self.fixedHeight

    def sizeHint(self):
        return QSize(240, self.heightForWidth(240))

    def minimumSizeHint(self):
        return QSize(0, 0 if self.config.sizing == 'aspect' else self.fixedHeight)

    def setImage(self, image, key, message=""):
        assertGuiThread()
        if self.image.cacheKey() == image.cacheKey() and self.key == key and self.message == message:
            return
        ratio = self.aspectRatio()
        self.image, self.key, self.message = image, key, message
        if self.config.sizing == 'aspect' and self.aspectRatio() != ratio:
            self.updateGeometry()
        self.update()

    def contextMenuEvent(self, event):
        details = self.toolTip()
        if not details:
            event.ignore()
            return
        menu = QMenu(self)
        # Capture the displayed diagnostic before live updates can replace it.
        copyAction = menu.addAction('复制提示详情')
        copyAction.triggered.connect(lambda _checked=False: QApplication.clipboard().setText(details))
        try:
            menu.exec_(event.globalPos())
        finally:
            menu.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.config.background != 'transparent':
            painter.fillRect(self.rect(), QColor(THEME[self.config.background]))
        if not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            offsets = {'start': 0, 'center': 0.5, 'end': 1}
            x = round((self.width() - size.width()) * offsets[self.config.horizontalAlign])
            y = round((self.height() - size.height()) * offsets[self.config.verticalAlign])
            painter.drawImage(QRect(x, y, size.width(), size.height()), self.image)
            if self.painted:
                self.painted(self.key, time.perf_counter_ns())
        else:
            painter.setPen(QColor(THEME['surface' if self.config.background == 'dark' else 'muted']))
            painter.drawText(self.rect().adjusted(14, 14, -14, -14), Qt.AlignCenter | Qt.TextWordWrap, self.message)
        painter.end()


class RuntimePages(QWidget):
    """Stable page IDs, bounded lazy pages, one atomic submission per scope.

    A hub supplies snapshots and shared image conversion; manual submit remains
    useful for clearly labelled simulation and deterministic widget tests.
    """
    designExamplesChanged = Signal(bool)

    def __init__(self, presentation, *, hub=None, label="只读运行页面", parent=None, assets=None, projectRoot=None,
                 target='desktop', historyClient=None, editorHost=False):
        super().__init__(parent)
        assertGuiThread()
        self.assets = dict(assets or {})
        self.projectRoot = projectRoot
        self.historyClient = historyClient
        self.target = target
        self.config = Presentation.model_validate(presentation.model_dump()) if presentation is not None else Presentation()
        try:
            self.expectedCapture = revisionOf(captureDefinition(self.config))
        except KeyError:
            self.expectedCapture = None
        if self.target is not None:
            from emo_master.core.presentation.targets import presentationForTarget
            self.config = presentationForTarget(self.config, self.target)
        self.hub = hub
        self.captureCoverage = None
        self.simulationState = None
        self.detached = False
        self.editing = False
        self.editorHost = editorHost
        self.designExamples = False
        self._designImage = QImage()
        self.currentPageId = None
        self.pages = {}
        self.widgets = {}
        self._historyReview = {}
        self.cards = {}
        self.layoutHosts = {}
        self.displayed = {}
        self.lastView = None
        self._hostConnected = False
        self._hostJobState = '尚未开始'
        self.frozen = None
        self.frozenGeneration = None
        self.records = deque(maxlen=256)
        self._recordedPaints = deque(maxlen=128)
        self._collapseStates = {}
        self._screenWindow = None
        self._screenSource = None
        self._label = label
        self.setWindowTitle(label)
        self.resize(1060, 720)
        self.setMinimumSize(460, 360)
        self.fitToAvailableScreen(resize=False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        if sys.platform == 'win32':
            self.setFont(QFont('Microsoft YaHei UI'))
        self.setStyleSheet(
            f"QWidget {{font-size:13px;color:{THEME['text']};}}"
            f"QWidget#runtimePages {{background:{THEME['page']};}}"
            f"QPushButton {{padding:5px 12px;background:{THEME['surface']};border:1px solid {THEME['border']};border-radius:6px;}}"
            f"QPushButton:checked,QPushButton[buttonStyle=primary] {{background:{THEME['primary']};color:white;}}"
            f"QPushButton[buttonStyle=link] {{background:transparent;color:{THEME['primary']};border:1px solid transparent;}}"
            f"QPushButton:focus {{border:1px solid {THEME['primary']};}}"
            f"QPushButton:disabled {{color:{THEME['muted']};background:{THEME['subtle']};}}"
            f"QTabBar::tab {{padding:7px 15px;color:{THEME['muted']};border-bottom:2px solid transparent;}}"
            f"QTabBar::tab:selected {{color:{THEME['primary']};border-color:{THEME['primary']};font-weight:bold;}}")
        self.setObjectName("runtimePages")
        layout = QVBoxLayout(self)
        self.banner = QLabel(label)
        self.banner.setTextFormat(Qt.PlainText)
        self.banner.setWordWrap(True)
        self.header = QWidget()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(12, 8, 12, 0)
        self.logo = QLabel()
        head.addWidget(self.logo)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.shellTitle = plainLabel('', f"font-size:20px;font-weight:bold;color:{THEME['text']};")
        self.shellSubtitle = plainLabel('', f"color:{THEME['muted']};")
        titles.addWidget(self.shellTitle)
        titles.addWidget(self.shellSubtitle)
        head.addLayout(titles, 1)
        self.headerStatus = plainLabel('')
        head.addWidget(self.headerStatus)
        self.headerJob = plainLabel('')
        head.addWidget(self.headerJob)
        self.hostControls = QHBoxLayout()
        head.addLayout(self.hostControls)
        self.hostMessage = plainLabel('', f"color:{THEME['foreground']['red']};")
        self.hostMessage.hide()
        layout.addWidget(self.header)
        layout.addWidget(self.hostMessage)
        layout.addWidget(self.banner)
        self.status = QLabel("未连接 · 等待明确选择任务")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.jobStatus = QLabel('任务执行状态不可用 · 尚无已核验的任务身份')
        self.jobStatus.setTextFormat(Qt.PlainText)
        self.jobStatus.setWordWrap(True)
        controls = QHBoxLayout()
        self.pageNavigation = PageNavigation(self.config)
        self.pageNavigation.pageRequested.connect(self.navigate)
        controls.addWidget(self.pageNavigation, 1)
        controls.addStretch()
        self.modeLabel = plainLabel('实时')
        controls.addWidget(self.modeLabel)
        self.resumeButton = QPushButton("恢复实时")
        self.resumeButton.clicked.connect(self.resumeLive)
        controls.addWidget(self.resumeButton)
        self.fitButton = QPushButton('适配当前屏幕')
        self.fitButton.clicked.connect(lambda: self.fitToAvailableScreen())
        layout.addLayout(controls)
        self.diagnosticToggle = QToolButton()
        self.diagnosticToggle.setText('技术诊断')
        self.diagnosticToggle.setCheckable(True)
        self.diagnosticToggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        controls.addWidget(self.diagnosticToggle)
        self.diagnostics = QWidget()
        diagnosticBody = QVBoxLayout(self.diagnostics)
        diagnosticBody.setContentsMargins(12, 0, 12, 0)
        diagnosticBody.addWidget(self.status)
        diagnosticBody.addWidget(self.jobStatus)
        diagnosticBody.addWidget(self.fitButton, 0, Qt.AlignLeft)
        layout.addWidget(self.diagnostics)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.identity = QLabel("尚无已显示结果")
        self.identity.setTextFormat(Qt.PlainText)
        self.identity.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.identity.setWordWrap(True)
        diagnosticBody.addWidget(self.identity)
        self.diagnosticToggle.toggled.connect(self._showDiagnostics)
        self._setShell()
        if self.config.defaultPageId:
            self.navigate(self.config.defaultPageId)
        else:
            self.stack.addWidget(QLabel("此项目尚无运行页面"))
            self.syncNavigation()
        if hub:
            hub.attach(self)

    def _showDiagnostics(self, visible):
        self.diagnostics.setVisible(visible)
        self.diagnosticToggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)

    def _setShell(self):
        shell = self.config.shell or PageShell()
        self.shellTitle.setText(shell.title or self._label)
        self.shellSubtitle.setText(shell.subtitle)
        self.shellSubtitle.setVisible(bool(shell.subtitle))
        self.headerStatus.setVisible(shell.showConnection)
        self.headerJob.setVisible(shell.showJobState)
        self.headerStatus.setText('尚未连接结果服务')
        self.headerJob.setText('尚未开始')
        self.logo.hide()
        if self.editorHost:
            self.diagnosticToggle.hide()
        else:
            self.diagnosticToggle.setChecked(shell.diagnosticsInitiallyExpanded)
        self._showDiagnostics(shell.diagnosticsInitiallyExpanded and not self.editorHost)

    def setHostMessage(self, message, *, problem=False):
        self.hostMessage.setStyleSheet(f"color:{THEME['foreground']['red'] if problem else THEME['muted']};")
        self.hostMessage.setText(message)
        self.hostMessage.setVisible(bool(message))

    def setBasicStatus(self, state, *, connected, connectionDetail="", operation=""):
        """Publish host status while keeping custom-page diagnostics session-owned."""
        assertGuiThread()
        self._hostConnected = connected
        jobId, status = state.get("jobId", ""), state.get("jobStatus", "")
        names = {"ACCEPTED": "已接受，等待启动", "STARTING": "启动中", "RUNNING": "运行中",
                 "STOPPING": "停止中", "COMPLETED": "已完成", "FAILED": "执行失败", "ABORTED": "已中止"}
        self._hostJobState = names.get(status, '状态待确认') if jobId else '尚未开始'
        self._showHostStatus()
        if self.config.pageOrder:
            return
        self.status.setText("Runtime：已连接（本机）" if connected else "Runtime：连接状态不可用")
        self.status.setToolTip(connectionDetail)
        terminal = status in {"COMPLETED", "FAILED", "ABORTED"}
        if jobId:
            text = ("最近任务：" if terminal else "当前任务：") + self._hostJobState
            if status:
                text += f" ({status})"
            text += "\nJob: " + jobId
            if terminal:
                text += "\n任务资源已释放" if state.get("resourcesReleased") else "\n正在等待任务资源释放"
        else:
            text = "任务：尚未开始" if state.get("state") != "FAULT" else "任务：启动结果待确认"
        if operation == "starting":
            text += "\n正在请求启动"
        elif operation == "stopping":
            text += "\n正在请求停止，等待任务退出及资源释放"
        if state.get("message"):
            text += "\n" + state["message"]
        if not connected:
            text += "\n以上为最近一次已确认状态，当前状态暂不可用"
            if connectionDetail:
                text += "\n" + connectionDetail
        self.jobStatus.setText(text)
        self.stack.currentWidget().setText("尚未配置运行页面\n当前仅显示基础任务状态；任务完成不等于产品 OK/NG。")
        for widget in (self.resumeButton, self.fitButton, self.modeLabel, self.identity):
            widget.hide()

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
        self.syncNavigation()
        self._syncLocalPreviews()
        self.setMinimumSize(0, 0)
        self.fitButton.setText('适配编辑区')
        if self.parentWidget():
            self.parentWidget().installEventFilter(self)

    def eventFilter(self, obj, event):
        if self.editorHost and obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.fitToAvailableScreen(resize=False)
        return super().eventFilter(obj, event)

    def setDesignExamples(self, enabled):
        assertGuiThread()
        if enabled and (not (self.editorHost or getattr(self, 'allowsDesignExamples', False)) or self.hub or self.captureCoverage is not None):
            raise ValueError('设计示例仅供未连接任务的编辑器使用')
        from . import design_examples
        if not enabled and self.designExamples:
            design_examples.clear(self)
        changed = self.designExamples != bool(enabled)
        self.designExamples = bool(enabled)
        self._syncLocalPreviews()
        if enabled:
            self.simulationState = None
            self.lastView = None
            design_examples.populate(self)
        if changed:
            self.designExamplesChanged.emit(self.designExamples)

    def editorResourceUsage(self):
        return {'design_image_bytes': self._designImage.sizeInBytes(),
                'static_image_bytes': sum(w.image.sizeInBytes() for rows in self.widgets.values()
                    for c, w in rows.values() if isinstance(w, ImageView) and c.props.imageResourceId),
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
        self._syncLocalPreviews()
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

    def setProjectRoot(self, projectRoot):
        """Rebase local file readers without changing portable page configuration."""
        if self.projectRoot == projectRoot:
            return
        self.projectRoot = projectRoot
        self._historyReview.clear()
        for rows in self.widgets.values():
            for component, widget in rows.values():
                if component.props.imagePath is not None or component.type in {'sqlite_table', 'sqlite_value'}:
                    widget.setProjectRoot(projectRoot)

    def reload(self, presentation):
        """Replace only widgets/configuration; retain the borrowed hub/session."""
        assertGuiThread()
        config = Presentation.model_validate(presentation.model_dump())
        captureConfig = config
        if self.target is not None:
            from emo_master.core.presentation.targets import presentationForTarget
            config = presentationForTarget(config, self.target)
        pageId = self.currentPageId
        self.resumeLive(submit=False)
        self.displayed.clear()
        self.lastView = None
        for rows in self.widgets.values():
            for _component, widget in rows.values():
                if isinstance(widget, ImageView):
                    if _component.props.imagePath is not None:
                        widget.dispose()
                    widget.painted = None
                    widget.setImage(QImage(), '')
                elif _component.props.iconResourceId:
                    widget.setIcon(QIcon())
                elif isinstance(widget, CollectionView):
                    widget.clear('配置更新')
        while self.stack.count():
            widget = self.stack.widget(0)
            widget.hide()
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self.pages.clear()
        self.widgets.clear()
        self._historyReview.clear()
        self.cards.clear()
        self.layoutHosts.clear()
        self.config = config
        self._setShell()
        self.pageNavigation.setPages(config)
        try:
            self.expectedCapture = revisionOf(captureDefinition(captureConfig))
        except KeyError:
            self.expectedCapture = None
        self.currentPageId = None
        target = pageId if pageId in config.pages else config.defaultPageId
        if target:
            self.navigate(target)
        else:
            self.stack.addWidget(QLabel('此项目尚无运行页面'))
            self.syncNavigation()
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
            self.cards.pop(old, None)
            self.layoutHosts.pop(old, None)
            for component, child in self.widgets.pop(old).values():
                if component.props.imagePath is not None:
                    child.dispose()
                elif component.type == 'sqlite_table':
                    self._historyReview[(old, component.componentId)] = child.saveReviewState()
            self.stack.removeWidget(widget)
            widget.deleteLater()
        page = self.config.pages[pageId]
        scroll = PageScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        body = QWidget()
        body.setProperty('pageGrid', pageId)
        appearance = self.config.appearance or PageAppearance()
        if appearance.contentMaxWidth:
            body.setMaximumWidth(appearance.contentMaxWidth)
        self.widgets[pageId] = {}
        self.cards[pageId] = {}
        self.layoutHosts[pageId] = {}
        self._children(body, page.components, page.layout, pageId, canvasTop=page.canvasTop,
                       padding=(appearance.pagePadding,) * 4)
        self._connectLocalImages(pageId)
        scroll.setWidget(body)
        scroll.setCanvasExtension(0 if self.editorHost else -page.canvasTop)
        self.stack.addWidget(scroll)
        self.pages[pageId] = scroll

    def _children(self, parent, components, grid, pageId, parentId=None, *, canvasTop=0, padding=(0, 0, 0, 0)):
        from .editor_grid import EditorGridLayout
        responsive = isinstance(grid, ResponsiveLayout)
        if responsive:
            from .responsive_layout import ResponsiveWidgetLayout
            layout = ResponsiveWidgetLayout(parent, grid, canvasTop=canvasTop)
        else:
            layout = (EditorGridLayout if self.editorHost else QGridLayout)(parent)
        self.layoutHosts[pageId][parentId] = parent
        layout.setContentsMargins(*padding)
        layout.setSpacing(grid.spacing)
        fixedRows = {row for c in components if c.type not in {'image', 'table', 'sqlite_table', 'container'}
                     for row in range(c.layout.row, c.layout.row + c.layout.rowSpan)}
        if self.editorHost and not responsive:
            # A real stretch row keeps Qt's cellRect valid during native DnD.
            # AlignTop on QGridLayout can leave zero-height cell geometry until
            # a later activation when transient child widgets are inserted.
            end = max((c.layout.row + c.layout.rowSpan for c in components), default=0)
            layout.setRowStretch(end, 1)
            # Full-content canvases have no spare viewport height to stretch.
            # Keep the append target usable even when existing rows overflow.
            layout.setRowMinimumHeight(end, 64)
            for row in range(end):
                layout.setRowMinimumHeight(row, 64)
        for column in range(grid.columns if not responsive else 0):
            layout.setColumnStretch(column, 1)
        for component in components:
            from .spacer import SpacerCard
            card = SpacerCard(editing=self.editorHost) if component.type == "spacer" else QFrame()
            card.setObjectName("pageSpacer" if component.type == "spacer" else "card")
            if self.editorHost:
                card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            card.setProperty('componentId', component.componentId)
            self.cards[pageId][component.componentId] = card
            margins = surfaceStyle(card, component) if component.type != 'spacer' else (0, 0, 0, 0)
            if component.type == "spacer":
                self.widgets[pageId][component.componentId] = (component, card)
            elif component.type == "container":
                content = card
                if component.props.title or component.props.collapse:
                    from .container_title import ContainerTitle
                    card.containerTitle = ContainerTitle(card, component.props.title,
                        roleStyle(component.props, 'title'), margins=margins,
                        collapse=component.props.collapse)
                    content = card.containerTitle.body
                    if component.props.collapse:
                        key = (pageId, component.componentId)
                        toggle = card.containerTitle.label
                        toggle.setChecked(self._collapseStates.get(key, toggle.isChecked()))
                        toggle.toggled.connect(lambda expanded, key=key: self._collapseStates.__setitem__(key, expanded))
                self._children(content, component.children, component.grid, pageId, component.componentId,
                               padding=margins if content is card else (0, 0, 0, 0))
                if self.editorHost and not component.children:
                    card.setMinimumHeight(90)
                    if isinstance(component.grid, ResponsiveLayout):
                        content.layout().addWidget(QLabel('容器 · 拖入组件'))
                    else:
                        content.layout().addWidget(QLabel('容器 · 拖入组件'), 0, 0)
            else:
                box = QVBoxLayout(card)
                box.setContentsMargins(*margins)
                box.setSpacing(5)
                if component.props.title and component.type not in {
                        'text', 'number', 'indicator', 'runtime_status', 'navigation_button'}:
                    title = QLabel(component.props.title)
                    title.setTextFormat(Qt.PlainText)
                    title.setStyleSheet(roleStyle(component.props, 'title'))
                    title.setWordWrap(True)
                    box.addWidget(title)
                if component.type == "image":
                    if component.props.imagePath is not None:
                        from .path_image import PathImageView
                        source = component.props.imagePath
                        widget = PathImageView(baseDirectory=source.baseDirectory,
                            preview=self._localPreviewEnabled(), availableBytes=self._localImageAvailableBytes,
                            projectRoot=self.projectRoot)
                        from emo_master.core.presentation.targets import effectiveImageLocation
                        if effectiveImageLocation(self.config, pageId, component) == 'runtime':
                            widget.remoteReader = self._readHistoryImage
                        if source.kind == 'fixed':
                            if source.location == 'runtime':
                                from emo_master.clients.runtime.history import HistoryImageRef
                                widget.setPath(HistoryImageRef(componentId=component.componentId))
                            else:
                                widget.setPath(source.path)
                    else:
                        widget = ImageView()
                        widget.painted = self._painted
                    widget.setViewConfig(component.props.imageView, component.responsive.preferredHeight)
                    if component.bindings:
                        widget.message = '已绑定 · 等待明确任务结果'
                elif component.type == 'table' and sum(isinstance(w, CollectionView) for rows in self.widgets.values() for _c, w in rows.values()) < 4:
                    widget = CollectionView(component.props)
                elif component.type == "navigation_button":
                    widget = QPushButton(component.props.text or component.props.title or "导航")
                    widget.clicked.connect(lambda _checked=False, item=component: self.act(item.actions.get("clicked")))
                    widget.setProperty('buttonStyle', component.props.buttonStyle)
                elif component.type == 'page_navigation':
                    widget = PageNavigation(self.config, title=component.props.text)
                    widget.selectPage(pageId)
                    widget.pageRequested.connect(self._componentNavigate)
                elif component.type == 'page_title':
                    widget = QLabel(component.props.text or self.config.pages[pageId].name)
                    widget.setTextFormat(Qt.PlainText)
                    widget.setWordWrap(True)
                elif component.type == 'clock':
                    from .clock import ClockView
                    widget = ClockView()
                elif component.type in {'sqlite_table', 'sqlite_value'}:
                    from .sqlite_widgets import SqliteTableView, SqliteValueView
                    preview = self._localPreviewEnabled()
                    if component.type == 'sqlite_table':
                        pageComponents = list(walkComponents(self.config.pages[pageId].components))
                        fields = {c.props.imagePath.field for c in pageComponents
                                  if c.props.imagePath is not None and c.props.imagePath.kind == 'selected_row'
                                  and c.props.imagePath.componentId == component.componentId}
                        needsCount = any(c.props.recordValue is not None
                            and c.props.recordValue.componentId == component.componentId
                            and c.props.recordValue.kind == 'meta' and c.props.recordValue.meta == 'count'
                            for c in pageComponents)
                        requiresIdentity = bool(fields) or any(
                            (c.props.recordValue is not None and c.props.recordValue.componentId == component.componentId)
                            or any(action.componentId == component.componentId for action in c.actions.values())
                            for c in pageComponents)
                        widget = SqliteTableView(component.props.sqlite, pageSize=component.props.pageSize,
                            preview=preview, selectionFields=sorted(fields), projectRoot=self.projectRoot,
                            props=component.props, needsCount=needsCount, requiresIdentity=requiresIdentity)
                        state = self._historyReview.get((pageId, component.componentId))
                        if state is not None:
                            widget.restoreReviewState(state)
                    else:
                        widget = SqliteValueView(component.props.sqlite, decimals=component.props.decimals,
                                                 unit=component.props.unit, preview=preview, projectRoot=self.projectRoot)
                    widget.historyClient = self.historyClient
                    widget.componentId = component.componentId
                elif component.type in {'text', 'number', 'indicator', 'runtime_status'}:
                    widget = ValueView(component, card)
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
                    if component.props.imageResourceId:
                        note = '静态资源: ' + component.props.imageResourceId
                    elif component.props.imagePath is not None:
                        pathSource = component.props.imagePath
                        note = ('文件路径: ' + (pathSource.path or '待配置') if pathSource.kind == 'fixed'
                                else '选中记录: ' + pathSource.componentId + ' / ' + pathSource.field)
                    elif source and source.kind == 'global_variable':
                        note = '来源: 全局变量 ' + source.variableId
                    elif source:
                        note = '来源: ' + (source.port or source.name or source.kind)[:32]
                    else:
                        note = '来源缺失' if component.bindings else '未选择来源 · 拖入流程结果'
                    card.setToolTip(note)
                if not isinstance(widget, ValueView):
                    widget.setStyleSheet(appearanceStyle(component.props))
                self.widgets[pageId][component.componentId] = (component, widget)
            p = component.layout
            if responsive:
                from PySide2.QtWidgets import QLayout
                card.setMinimumWidth(0)
                card.layout().setSizeConstraint(QLayout.SetNoConstraint)
                layout.addComponent(card, component)
            else:
                layout.addWidget(card, p.row, p.column, p.rowSpan, p.columnSpan)
            if not responsive and not self.editorHost and component.type in {'image', 'table', 'sqlite_table'}:
                # A spanning image must not stretch an adjacent scalar footer.
                for row in range(p.row, p.row + p.rowSpan):
                    if row not in fixedRows:
                        layout.setRowStretch(row, 1)

    def _connectLocalImages(self, pageId):
        """Wire page-local references after all nested components exist."""
        from emo_master.core.presentation.sqlite_reader import PathValueError
        from .sqlite_widgets import SqliteTableView
        rows = self.widgets[pageId]
        for component, widget in rows.values():
            recordValue = component.props.recordValue
            action = component.actions.get('clicked')
            recordTarget = rows.get(recordValue.componentId) if recordValue is not None else None
            actionTarget = rows.get(action.componentId) if action is not None and action.componentId else None
            if recordTarget is not None and isinstance(recordTarget[1], SqliteTableView):
                table = recordTarget[1]
                def updateValue(field=recordValue, source=table, target=widget):
                    target.setValue(source.recordValue(field))
                table.stateChanged.connect(updateValue)
                updateValue()
            elif action is not None and action.type in {'history_first', 'history_previous', 'history_next'}:
                if actionTarget is None or not isinstance(actionTarget[1], SqliteTableView):
                    widget.setEnabled(False)
                    continue
                table = actionTarget[1]
                def updateAction(button=widget, source=table, kind=action.type):
                    button.setEnabled(source.canReview(kind))
                table.stateChanged.connect(updateAction)
                updateAction()
            source = component.props.imagePath
            if source is None or source.kind != 'selected_row':
                continue
            from emo_master.core.presentation.targets import effectiveImageLocation
            widget.selectionField = (component.componentId
                if effectiveImageLocation(self.config, pageId, component) == 'runtime' else source.field)
            target = rows.get(source.componentId)
            if target is None or not isinstance(target[1], SqliteTableView):
                widget.setPath(PathValueError('关联的历史表格不存在，请重新选择图像来源'))
                continue
            table = target[1]
            table.selectedRowChanged.connect(widget.setSelectedRow)
            widget.referenceExpired.connect(table.retryExpiredImage)
            widget.setSelectedRow(table.selectedRow())

    def historyReviewState(self):
        tables = dict(self._historyReview)
        for pageId, rows in self.widgets.items():
            for component, widget in rows.values():
                if component.type == 'sqlite_table':
                    tables[pageId, component.componentId] = widget.saveReviewState()
        return self.currentPageId, tables, dict(self._collapseStates)

    def restoreHistoryReviewState(self, state):
        # The operator host restores before showing a replacement renderer.
        pageId, tables, collapsed = state
        self._historyReview.update(tables)
        self._collapseStates.update(collapsed)
        for mountedPage, rows in self.widgets.items():
            for component, widget in rows.values():
                saved = tables.get((mountedPage, component.componentId))
                if component.type == 'sqlite_table' and saved is not None:
                    widget.restoreReviewState(saved)
        for mountedPage, cards in self.cards.items():
            for componentId, card in cards.items():
                title = getattr(card, 'containerTitle', None)
                if title is not None and isinstance(title.label, QToolButton) and (mountedPage, componentId) in collapsed:
                    title.label.setChecked(collapsed[mountedPage, componentId])
        if pageId in self.config.pages:
            self.navigate(pageId)

    def _readHistoryImage(self, ref):
        if self.historyClient is None:
            raise ValueError('此图片由 Runtime 读取，请连接对应 Runtime')
        return self.historyClient.readImage(ref)

    def setHistoryClient(self, client):
        if client is self.historyClient:
            return
        self.historyClient = client
        self._historyReview.clear()
        for rows in self.widgets.values():
            for component, widget in rows.values():
                if component.type in {'sqlite_table', 'sqlite_value'}:
                    widget.historyClient = client
                    widget._generation += 1
                    widget.loaded = False
                    widget._clear()
                    if widget.isVisible():
                        if widget.busy and component.type == 'sqlite_table':
                            widget._resumeAfterWorker = True
                        else:
                            widget.refresh()
                elif component.props.imagePath is not None and widget.remoteReader:
                    widget.reload()

    def _localImageAvailableBytes(self):
        if self.hub:
            return max(0, self.hub.imageLimit - self.hub.imageBytes())
        images = {widget.image.cacheKey(): widget.image.sizeInBytes()
                  for rows in self.widgets.values() for _component, widget in rows.values()
                  if isinstance(widget, ImageView) and not widget.image.isNull()}
        return max(0, 24 * 1024 * 1024 - sum(images.values()))

    def imageSources(self, pageId=None):
        page = self.config.pages.get(pageId or self.currentPageId)
        if page is None:
            return set()
        return {sourceId for component in walkComponents(page.components) if component.type == 'image'
                for sourceId in component.bindings.values() if sourceId in self.config.dataSources
                and self.config.dataSources[sourceId].expectedType == 'image'
                and (self.captureCoverage is None or not self.captureCoverage.sourceProblem(self.config, sourceId))}

    def setAssets(self, assets):
        assertGuiThread()
        self.assets = dict(assets)
        if not self.detached:
            self._renderAssets()

    def _renderAssets(self):
        from .static_assets import readAsset
        shell = self.config.shell
        logo = self.assets.get(shell.logoResourceId) if shell and shell.logoResourceId else None
        if logo is not None:
            try:
                image = readAsset(logo, SURFACE_LIMIT, icon=True)
                self.logo.setPixmap(QPixmap.fromImage(image))
                self.logo.show()
            except (ValueError, OSError):
                self.logo.hide()
        else:
            self.logo.clear()
            self.logo.hide()
        # Drop previous decoded ownership before allocating replacements.
        for component, widget in self.widgets.get(self.currentPageId, {}).values():
            if component.props.imageResourceId:
                widget.setImage(QImage(), "")
            elif component.props.iconResourceId:
                widget.setIcon(QIcon())
        remaining = SURFACE_LIMIT
        encodedRemaining = SURFACE_LIMIT
        for component, widget in self.widgets.get(self.currentPageId, {}).values():
            key = component.props.imageResourceId or component.props.iconResourceId
            if not key:
                continue
            try:
                if key not in self.assets:
                    raise ValueError("STATIC_ASSET_MISSING")
                size = self.assets[key][2]
                if size > encodedRemaining:
                    raise ValueError("STATIC_ASSET_ENCODED_BUDGET")
                encodedRemaining -= size
                image = readAsset(self.assets[key], remaining, icon=component.type == "navigation_button")
                remaining -= image.sizeInBytes()
                widget.setToolTip("")
                if isinstance(widget, ImageView):
                    widget.setImage(image, "")
                else:
                    widget.setIcon(QIcon(QPixmap.fromImage(image)))
            except (OSError, ValueError) as error:
                widget.setToolTip(str(error))
                if isinstance(widget, ImageView):
                    widget.setImage(QImage(), "", str(error))
                else:
                    widget.setIcon(QIcon())

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
                elif _component.props.iconResourceId:
                    widget.setIcon(QIcon())
                elif isinstance(widget, CollectionView):
                    widget.clear('隐藏页面')
        if pageId not in self.pages:
            self._build(pageId)
        self.currentPageId = pageId
        self.stack.setCurrentWidget(self.pages[pageId])
        self._showHostStatus()
        self._renderAssets()
        self.syncNavigation()
        if self.designExamples:
            from .design_examples import populate
            populate(self)
        if self.hub:
            self.hub.updateImageDemand()
            if self.lastView is not None:
                self.submit(self.hub.session.readSnapshot())
        elif self.lastView is not None:
            self.submit(self.lastView)

    def syncNavigation(self):
        page = self.config.pages.get(self.currentPageId)
        components = list(walkComponents(page.components)) if page is not None else []
        custom = any(c.type == 'page_navigation' for c in components)
        customResume = any(a.type == 'resume_live' for c in components for a in c.actions.values())
        self.pageNavigation.selectPage(self.currentPageId)
        self.pageNavigation.setVisible(not self.editorHost and len(self.config.pages) > 1 and not custom)
        self.resumeButton.setVisible(not self.editorHost and bool(self.frozen) and not customResume)
        for rows in self.widgets.values():
            for component, widget in rows.values():
                if component.type == 'page_navigation':
                    widget.selectPage(self.currentPageId)

    def _componentNavigate(self, pageId):
        if not self.editing:
            self.navigate(pageId)
        # Editing selects the component; it must not navigate away or leave a
        # selection pointing at a page the editor has not accepted.
        self.syncNavigation()

    def _syncLocalPreviews(self):
        preview = self._localPreviewEnabled()
        for rows in self.widgets.values():
            for component, widget in rows.values():
                if component.type in {'sqlite_table', 'sqlite_value'} or component.props.imagePath is not None:
                    widget.setPreview(preview)

    def _localPreviewEnabled(self):
        return self.editorHost or self.designExamples or bool(self.simulationState) or getattr(self, 'localPreviewOnly', False)

    def act(self, action):
        if self.editing:
            return
        if action is None:
            self.status.setText("此按钮未配置动作")
        elif action.type == "navigate" and action.context == "live":
            self.navigate(action.pageId)
        elif action.type == "resume_live":
            self.resumeLive()
        elif action.type in {'history_first', 'history_previous', 'history_next'}:
            item = self.widgets.get(self.currentPageId, {}).get(action.componentId)
            if item is not None and item[0].type == 'sqlite_table':
                item[1].review(action.type)
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
        self.modeLabel.setText('实时')
        self.syncNavigation()
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
        if source.kind not in ("node_output", "workflow_output", "global_variable", "global_counter"):
            return None, None, "不支持此来源: " + source.kind
        if self.captureCoverage is not None:
            problem = self.captureCoverage.sourceProblem(self.config, sourceId)
            if problem:
                return None, None, problem
        problem = getattr(view, 'scopeErrors', {}).get(source.resultScopeId)
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
            error = value.reasonCode or value.reason
            if value.reasonCode and value.reason and value.reason != value.reasonCode:
                error += ' · ' + value.reason
            return scope, None, error
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
        from emo_master.core.presentation.coverage import CaptureCoverage, DispatchCoverage
        if coverage is not None and not isinstance(coverage, (CaptureCoverage, DispatchCoverage)):
            raise TypeError('validated capture coverage required')
        self.captureCoverage = coverage
        if coverage is not None:
            self.simulationState = None
        if self.hub:
            self.hub.updateImageDemand()
            self.hub.lastToken = None
            self.submit(self.hub.session.readSnapshot())

    def _pendingImageScopes(self, view, previous):
        if (self.frozen or previous is None or view.connection != 'CONNECTED'
                or (view.generation, view.runtimeInstanceId, view.jobId)
                != (previous.generation, previous.runtimeInstanceId, previous.jobId)):
            return set()
        images = {}
        for component, widget in self.widgets.get(self.currentPageId, {}).values():
            if not isinstance(widget, ImageView) or component.props.imageResourceId or component.props.imagePath is not None:
                continue
            sourceId = next(iter(component.bindings.values()), '')
            source = self.config.dataSources.get(sourceId)
            if source is not None:
                images.setdefault(source.resultScopeId, []).append((sourceId, widget))
        held = set()
        for scopeId, rows in images.items():
            old = self.displayed.get(scopeId)
            if (old is None or scopeId in view.expiredScopes or scopeId in view.scopeErrors
                    or any(widget.image.isNull() or widget.key != old.result.identity.resultKey for _, widget in rows)):
                continue
            current = view.scopes.get(scopeId)
            pending = current.result if current else view.loading.get(scopeId)
            if pending is None or pending.status != 'COMPLETE':
                continue
            if current and (current.failures or not any(current.imageStates.get(sourceId) == 'LOADING' for sourceId, _ in rows)):
                continue
            identity, before = pending.identity, old.result.identity
            if (identity.resultOrdinal <= before.resultOrdinal
                    or any(getattr(identity, field) != getattr(before, field) for field in
                           ('runtimeInstanceId', 'jobId', 'executionRevision', 'capturePlanRevision'))):
                continue
            if self.captureCoverage is not None and (not self.captureCoverage.matches(identity)
                    or any(self.captureCoverage.sourceProblem(self.config, sourceId) for sourceId, _ in rows)):
                continue
            held.add(scopeId)
        return held

    def releaseReplacedImages(self, view):
        if self.frozen:
            return
        held = self._pendingImageScopes(view, self.lastView)
        for component, widget in self.widgets.get(self.currentPageId, {}).values():
            if not isinstance(widget, ImageView) or component.props.imageResourceId or component.props.imagePath is not None:
                continue
            source = self.config.dataSources.get(next(iter(component.bindings.values()), ''))
            scope = view.scopes.get(source.resultScopeId) if source else None
            if scope and source.resultScopeId not in held and widget.key != scope.result.identity.resultKey:
                widget.setImage(QImage(), '')

    def submit(self, view):
        assertGuiThread()
        if self.detached:
            return
        held = self._pendingImageScopes(view, self.lastView)
        self.setDesignExamples(False)
        # A real session, including its initial empty/disconnected snapshot,
        # retires legacy offline simulations even without a capture manifest.
        if (self.editorHost or self.hub or self.captureCoverage is not None) and (
                view.runtimeInstanceId != 'offline-simulation' or view.jobId != 'offline-simulation'):
            self.simulationState = None
        self.lastView = view
        connectionMessage = f"{view.connection} · {view.detail or '连接健康，等待触发'}"
        self.status.setToolTip(connectionMessage)
        self.status.setText(self.displayMessage(connectionMessage))
        from emo_master.ui.presentation.job_status import jobStatusText
        # Execution remains live even when business values below use a pin.
        self.jobStatus.setText(jobStatusText(getattr(view, 'job', None)))
        self._hostConnected = view.connection == 'CONNECTED'
        job = getattr(view, 'job', None)
        from emo_master.ui.presentation.job_status import JOB_STATES
        self._hostJobState = JOB_STATES.get(job.status, '状态待确认') if job and job.availability == 'AVAILABLE' else '尚无任务状态'
        self._showHostStatus()
        if self.simulationState:
            self.banner.setText('离线模拟 · ' + self.simulationState + ' · 示例状态，不代表真实检测')
            self.banner.show()
            self.status.setText('模拟 · 不读取或写入 Runtime / 计数')
            self.jobStatus.setText('离线模拟 · 无真实任务执行状态')
        elif view.runtimeInstanceId and view.jobId and not self.editorHost:
            self.banner.hide()
        if self.frozen and view.generation != self.frozenGeneration:
            self.resumeLive(submit=False)
        if self.frozen:
            pin = self.hub.session.pins().read(self.frozen)
            self.modeLabel.setText(f"冻结 · {pin.state} · 不暂停检测，不自动续租")
            if pin.scope is not None:
                scope = pin.scope
                view = replace(view, connection="CONNECTED", scopes=MappingProxyType({scope.result.identity.resultScopeId: scope}),
                               loading=MappingProxyType({}), scopeErrors=MappingProxyType({}))
            else:
                view = replace(view, connection="PIN_EXPIRED", detail=pin.error,
                               scopes=MappingProxyType({}), loading=MappingProxyType({}))
        else:
            self.modeLabel.setText('模拟 · 无可冻结的真实结果' if self.simulationState else "实时 · 下一件处理中" if any(view.started.get(s, 0) > r.result.identity.resultOrdinal for s, r in view.scopes.items())
                                   else "实时 · 等待触发")
        if held:
            self.modeLabel.setText('实时 · 图像更新中，显示上一结果')
        page = self.currentPageId
        if page is None:
            return
        shown = {}
        # QWidget updates coalesce after this GUI-thread submission returns.
        # Toggling the whole window's updates would also repaint unchanged content.
        for component, widget in self.widgets[page].values():
            if component.props.imageResourceId or component.props.imagePath is not None:
                continue
            if component.type in {"navigation_button", "page_navigation", "page_title", "clock", "sqlite_table", "sqlite_value", "spacer"}:
                continue
            if component.props.recordValue or (component.type == "text" and not component.bindings):
                continue
            if component.type == 'runtime_status' and not component.bindings:
                continue
            source = self.config.dataSources.get(next(iter(component.bindings.values()), ''))
            if source is not None and source.resultScopeId in held:
                # Retain the whole committed scope, not an old image paired
                # with new values. The live session snapshot remains untouched.
                shown[source.resultScopeId] = self.displayed[source.resultScopeId]
                continue
            scope, value, error = self._value(component, view)
            if view.connection != "CONNECTED":
                value, error, scope = None, view.connection + ": " + view.detail, None
            widget.setToolTip(error)
            diagnostic = error
            errorCode = error.split(' · ', 1)[0].split(':', 1)[0]
            error = (component.props.emptyText if component.type == 'image'
                     and errorCode in {'OPTIONAL_ABSENT', 'BRANCH_SKIPPED'} and component.props.emptyText != '—'
                     else self.displayMessage(error))
            if scope:
                shown[scope.result.identity.resultScopeId] = scope
            if isinstance(widget, ImageView):
                image = QImage()
                if not error and value is not None:
                    try:
                        image = self.hub.image(scope, next(iter(component.bindings.values())), value) if self.hub else ownedImage(value)
                    except ValueError as problem:
                        error = str(problem)
                        diagnostic = error
                if error:
                    details = [error, '诊断：' + diagnostic]
                    if source is not None:
                        details.append(f'数据源：{next(iter(component.bindings.values()))}')
                        if source.workflowId:
                            details.append(f'流程 / 节点 / 端口：{source.workflowId} / {source.nodeId or "—"} / {source.port or "—"}')
                    if scope is not None:
                        identity = scope.result.identity
                        details.extend([f'Runtime：{identity.runtimeInstanceId}', f'任务：{identity.jobId}',
                                        f'结果范围：{identity.resultScopeId}',
                                        f'结果：{identity.resultKey}（序号 {identity.resultOrdinal}）',
                                        f'执行版本：{identity.executionRevision}',
                                        f'采集版本：{identity.capturePlanRevision}'])
                    widget.setToolTip('\n'.join(details))
                    if errorCode == 'BUDGET_EXCEEDED':
                        error += '\n右键可复制提示详情'
                else:
                    widget.setToolTip('')
                widget.setImage(image, scope.result.identity.resultKey if scope else "", error)
            elif isinstance(widget, ValueView):
                widget.setValue(value, error)
                if diagnostic:
                    widget.setToolTip(diagnostic)
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
        for scopeId, scope in shown.items():
            if scopeId in held:
                continue
            self.records.append({"key": scope.result.identity.resultKey, "ready_ns": scope.readyNs,
                                 "gui_ns": time.perf_counter_ns(), "scope_end_ns": scope.result.timing.scopeEndedNs if scope.result.timing else None})

    def _showHostStatus(self):
        connection = '●  已连接' if self._hostConnected else '未连接结果服务'
        self.headerStatus.setText(connection)
        self.headerStatus.setStyleSheet(f"color:{THEME['foreground']['green'] if self._hostConnected else THEME['muted']};")
        self.headerJob.setText(self._hostJobState)
        for component, widget in self.widgets.get(self.currentPageId, {}).values():
            if component.type == 'runtime_status' and not component.bindings:
                widget.setValue(self._hostJobState if component.props.statusField == 'job_state' else connection)

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
        self._renderAssets()
        if self.designExamples:
            from .design_examples import populate
            populate(self)
        if self.hub and not self.detached:
            self.hub.updateImageDemand()
            self.submit(self.hub.session.readSnapshot())

    def displayMessage(self, message):
        """Explain unavailable results; raw diagnostics remain in tooltips."""
        messages = {
            'NODE_FAILED': '本次处理异常，请联系技术人员',
            'EXECUTION_CANCELLED': '本次处理已停止，未取得结果',
            'SOURCE_MISSING': '本次未取得所需结果，请联系技术人员',
            'OPTIONAL_ABSENT': '本次未提供此项结果',
            'BRANCH_SKIPPED': '本次未执行此项检测',
            'EXPORT_TIMEOUT': '结果读取超时，请检查连接',
            'EXPORT_FAILED': '结果读取失败，请联系技术人员',
            'RESOURCE_EXPIRED': '此结果已过期，请等待新的结果',
            'BUDGET_EXCEEDED': '显示资源受限，本次结果未显示',
            'INVALID_VALUE': '结果数据异常，请联系技术人员',
            'IPC_ERROR': '结果服务通信异常，请联系技术人员',
            'IMAGE_UNAVAILABLE': '本次图像暂不可用',
            'DISCONNECTED': '结果连接已中断，请检查连接',
            'CONNECTING': '正在连接结果服务',
            'OFFLINE': '尚未连接运行任务',
            'PIN_EXPIRED': '固定的结果已过期，请继续更新',
        }
        # Match a complete diagnostic token, not arbitrary text with a similar
        # prefix. Unknown diagnostics stay visible rather than becoming OK.
        token = message.split(' · ', 1)[0].split(':', 1)[0]
        if token == 'BUDGET_EXCEEDED':
            return budgetMessage(message.partition(' · ')[2])
        if token in messages:
            return messages[token]
        if token == 'CONNECTED':
            return message.replace('CONNECTED', '结果服务已连接', 1)
        return message

    def hideEvent(self, event):
        self.displayed.clear()
        self.lastView = None
        for rows in self.widgets.values():
            for _component, widget in rows.values():
                if isinstance(widget, ImageView):
                    widget.setImage(QImage(), "", "隐藏页面")
                elif _component.props.iconResourceId:
                    widget.setIcon(QIcon())
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
                    if _component.props.imagePath is not None:
                        widget.dispose()
                    widget.painted = None
                    widget.setImage(QImage(), "")
                elif _component.props.iconResourceId:
                    widget.setIcon(QIcon())
                elif isinstance(widget, CollectionView):
                    widget.clear('已关闭')
        super().closeEvent(event)
