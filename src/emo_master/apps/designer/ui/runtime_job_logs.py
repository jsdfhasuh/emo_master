"""Runtime task directory and the bounded event view for one Job."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import chain
from types import SimpleNamespace

from PySide2.QtCore import QTimer, Qt, Signal
from PySide2.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QSizePolicy, QSplitter, QStackedWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from emo_master.apps.designer.presenters.node_run_presenter import frameworkLogMessage
from emo_master.apps.designer.services.display_calls import DisplayCallContext
from emo_master.apps.designer.state.job_log import ExecutionTree, JobEventWindow, jobExecutionTime
from emo_master.apps.designer.ui.log_filters import (
    OptionalDateTimeRange, describeDateBounds, deviceChoices as _deviceChoices, includesTimestamp, updateIdentityChoices,
)
from emo_master.apps.designer.ui.log_record_detail import LogRecordDetail
from emo_master.ui.communication_panel import DeviceDiagnosticsView, exportDeviceCache


_TERMINAL = {"COMPLETED", "FAILED", "ABORTED"}
_UNFINISHED = {"ACCEPTED", "STARTING", "RUNNING", "STOPPING"}
_UNFINISHED_PROCESSING = "本次执行已结束，但 Runtime 尚未完成与本次任务有关的处理。当前接口未提供具体原因。"
_STATUS = {
    "ACCEPTED": "已受理", "STARTING": "启动中", "RUNNING": "运行中", "STOPPING": "停止中",
    "COMPLETED": "已完成", "FAILED": "失败", "ABORTED": "已停止",
}
_ROLE = Qt.UserRole
_COMMON_FIELDS = (("workflowId", "工作流 ID"), ("nodeId", "节点 ID"), ("deviceId", "设备 ID"))
_TECHNICAL_FIELDS = (("projectId", "工程 ID"), ("component", "模块"), ("eventType", "事件类型"),
                     ("workflowRunId", "调用 ID"), ("iterationPath", "轮次路径（原始索引，如 0/1）"))


def _label():
    result = QLabel()
    result.setTextFormat(Qt.PlainText)
    result.setWordWrap(True)
    return result


def _button(layout, title, action):
    button = QPushButton(title)
    button.setAutoDefault(False)
    button.clicked.connect(action)
    layout.addWidget(button)
    return button


def _tree(headers):
    tree = QTreeWidget()
    tree.setHeaderLabels(headers)
    tree.setUniformRowHeights(True)
    tree.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    tree.header().setStretchLastSection(False)
    tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
    return tree


def _stamp(value):
    return datetime.fromtimestamp(value / 1000).isoformat(sep=" ", timespec="milliseconds") if value else "未记录"


def _jobKey(job):
    return (job.project_id, job.job_id) if job is not None else None


def _jobName(job):
    return getattr(job, "workflow_name", "") or job.workflow_id or job.job_id



def _jobMessage(job):
    return frameworkLogMessage("job." + job.status.lower(), job.message)


class RuntimeJobLogs(QWidget):
    historyRequested = Signal(object)
    exportRequested = Signal(object)
    exportCancelRequested = Signal()

    def __init__(self, maximumEntries, makeEntry, parent=None, *, onNavigate=None):
        super().__init__(parent)
        self.setObjectName("runtimeJobLogs")
        self.maximumEntries = maximumEntries
        self.makeEntry = makeEntry
        self._onNavigate = onNavigate
        self.client = self.transport = None
        self.instance = ""
        self.watermark = None
        self.capabilities = None
        self.runs = {}
        self.activity = {}
        self.recent = ()
        self.history = []
        self.historyCursor = ""
        self.historyScope = {}
        self.job = self.window = self.structure = None
        self._selection = None
        self._focus = None
        self._before = self._upper = self._earlierUpper = None
        self._staged = None
        self._context = self._reveal = self._previousContext = None
        self._anchors = {}
        self._expanded = set()
        self._items = {}
        self._branches = {}
        self._continuous = False
        self._readerOnline = False
        self._readerGeneration = 0
        self._opening = False
        self._dirty = False
        self._shownMode = 0
        self._historyMode = False
        self._sourceBookmark = None
        self._relatedSource = None
        self._restoreLocation = None
        self.work = ThreadPoolExecutor(max_workers=1, thread_name_prefix="designer-job-logs")
        self.pending = None
        self.calls = {}
        resources, executor = self.calls, self.work

        def closeCalls():
            for context in tuple(resources.values()):
                context.cancel()
            executor.shutdown(wait=False, cancel_futures=True)

        self.destroyed.connect(closeCalls)
        self.pages = QStackedWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.pages)
        self._buildCatalog()
        self._buildDetail()
        self._buildRelated()
        self._buildDevice()
        self.poll = QTimer(self)
        self.poll.setInterval(40)
        self.poll.timeout.connect(self._finish)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick)
        self.redraw = QTimer(self)
        self.redraw.setSingleShot(True)
        self.redraw.setInterval(125)
        self.redraw.timeout.connect(self._render)
        self._ticks = 0

    def _buildCatalog(self):
        self.catalog = QWidget()
        panel = QVBoxLayout(self.catalog)
        row = QHBoxLayout()
        self.catalogTitle = _label()
        row.addWidget(self.catalogTitle, 1)
        self.catalogHistory = _button(row, "更早历史", self._toggleHistory)
        _button(row, "刷新", self._refreshCatalog)
        panel.addLayout(row)
        self.catalogFilters = QWidget()
        catalogLayout = QVBoxLayout(self.catalogFilters)
        catalogLayout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.catalogProject = QLineEdit(placeholderText="工程 ID，留空不限")
        self.catalogStatus = QComboBox()
        self.catalogStatus.addItem("全部终态", "")
        for value in ("COMPLETED", "FAILED", "ABORTED"):
            self.catalogStatus.addItem(_STATUS[value], value)
        filters.addWidget(self.catalogProject, 1)
        filters.addWidget(self.catalogStatus)
        self.catalogNext = _button(filters, "下一页", lambda: self._readCatalogHistory(True))
        catalogLayout.addLayout(filters)
        self.catalogDateRange = OptionalDateTimeRange("任务结束时间范围")
        catalogLayout.addWidget(self.catalogDateRange)
        panel.addWidget(self.catalogFilters)
        self.catalogFilters.hide()
        self.catalogHint = _label()
        panel.addWidget(self.catalogHint)
        self.directory = _tree(["运行任务", "工程", "状态", "实际开始", "运行时间", "最近活动"])
        self.directory.setColumnWidth(3, 185)
        self.directory.setColumnWidth(4, 145)
        self.directory.setColumnWidth(5, 270)
        panel.addWidget(self.directory, 1)
        actions = QHBoxLayout()
        self.openButton = _button(actions, "查看日志", self._openSelectedJob)
        self.stopButton = _button(actions, "停止任务", self._stopSelectedJob)
        self.catalogRelatedButton = _button(actions, "查看关联任务", self._openCatalogRelated)
        actions.addStretch(1)
        panel.addLayout(actions)
        self.directory.itemSelectionChanged.connect(self._catalogActions)
        self.directory.itemDoubleClicked.connect(lambda *_: self._openSelectedJob())
        self.pages.addWidget(self.catalog)
        self._renderCatalog()

    def _buildDetail(self):
        self.jobPage = QWidget()
        panel = QVBoxLayout(self.jobPage)
        header = QHBoxLayout()
        _button(header, "返回运行任务", self.home)
        self.returnSourceButton = _button(header, "返回来源任务", self._returnSource)
        self.returnSourceButton.hide()
        self.jobTitle = _label()
        header.addWidget(self.jobTitle, 1)
        self.relatedButton = _button(header, "查看关联任务", self._openJobRelated)
        self.relatedButton.hide()
        self.infoToggle = _button(header, "任务信息", self._toggleInfo)
        self.exportButton = _button(header, "导出此 Job 完整记录…", self._export)
        self.exportButton.setToolTip("包含仍可查询的整个任务范围，包括未加载至当前窗口的记录")
        panel.addLayout(header)
        self.jobSecondary = _label()
        self.jobSecondary.hide()
        panel.addWidget(self.jobSecondary)
        self.info = _label()
        panel.addWidget(self.info)
        self.info.hide()
        self.exportStatus = _label()
        self.exportStatus.hide()
        exportRow = QHBoxLayout()
        exportRow.addWidget(self.exportStatus, 1)
        self.exportCancel = _button(exportRow, "取消导出", self.exportCancelRequested.emit)
        self.exportCancel.hide()
        panel.addLayout(exportRow)

        row = QHBoxLayout()
        self.search = QLineEdit(placeholderText="搜索已加载事件")
        row.addWidget(self.search, 1)
        self.warning = QCheckBox("告警 / 错误")
        row.addWidget(self.warning)
        self.filterToggle = _button(row, "高级筛选", self._toggleFilters)
        _button(row, "定位当前", self._locate)
        panel.addLayout(row)
        self.filters = QWidget()
        self.filters.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        filterLayout = QVBoxLayout(self.filters)
        filterLayout.setContentsMargins(0, 0, 0, 0)
        self.filterScope = _label()
        self.filterScope.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        filterLayout.addWidget(self.filterScope)
        common = QGridLayout()
        self.level = QComboBox()
        self.level.addItems(("全部等级", "DEBUG", "INFO", "WARN", "ERROR"))
        common.addWidget(QLabel("等级"), 0, 0)
        common.addWidget(self.level, 0, 1)
        self.inputs = {}
        for index, (key, title) in enumerate(_COMMON_FIELDS, start=1):
            edit = QLineEdit(placeholderText="输入精确 ID 或检索已加载名称")
            edit.setAccessibleName(title)
            self.inputs[key] = edit
            row, column = divmod(index, 2)
            common.addWidget(QLabel(title), row, column * 2)
            common.addWidget(edit, row, column * 2 + 1)
            edit.textChanged.connect(self._scheduleRender)
        filterLayout.addLayout(common)
        self._effectiveDateBounds = (None, None)
        self.dateRange = OptionalDateTimeRange()
        self.dateRange.changed.connect(self._acceptDateFilter)
        filterLayout.addWidget(self.dateRange)
        commands = QHBoxLayout()
        self.technicalToggle = _button(commands, "技术条件", self._toggleTechnicalFilters)
        self.technicalToggle.setCheckable(True)
        _button(commands, "查询整个历史范围…", self._advancedHistory)
        commands.addStretch(1)
        filterLayout.addLayout(commands)
        self.technicalFilters = QWidget()
        technical = QGridLayout(self.technicalFilters)
        technical.setContentsMargins(0, 0, 0, 0)
        for index, (key, title) in enumerate(_TECHNICAL_FIELDS):
            edit = QLineEdit()
            edit.setAccessibleName(title)
            self.inputs[key] = edit
            row, column = divmod(index, 2)
            technical.addWidget(QLabel(title), row, column * 2)
            technical.addWidget(edit, row, column * 2 + 1)
            edit.textChanged.connect(self._scheduleRender)
        filterLayout.addWidget(self.technicalFilters)
        self.technicalFilters.hide()
        panel.addWidget(self.filters)
        self.filters.hide()

        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(("执行结构", "时间顺序"))
        row.addWidget(self.mode)
        self.timeRange = QCheckBox("显示起止时间")
        row.addWidget(self.timeRange)
        row.addStretch(1)
        self.earlier = _button(row, "加载更早", self._readEarlier)
        self.olderWindow = _button(row, "切换到更早范围", lambda: self._readEarlier(replace=True))
        self.olderWindow.hide()
        _button(row, "返回最新", self._openLatest)
        self.returnContext = _button(row, "返回原筛选", self._leaveContext)
        self.returnContext.hide()
        panel.addLayout(row)
        self.rangeStatus = _label()
        filterStatus = QHBoxLayout()
        filterStatus.addWidget(self.rangeStatus, 1)
        self.clearFilters = _button(filterStatus, "清除筛选", self._resetFilters)
        self.clearFilters.hide()
        panel.addLayout(filterStatus)

        self.split = QSplitter(Qt.Horizontal)
        self.events = _tree(["事件 / 执行结构", "等级 / 状态", "时间", "运行时间"])
        self.events.setObjectName("runtimeJobExecutionTree")
        self.events.setColumnWidth(1, 82)
        self.events.setColumnWidth(2, 100)
        self.events.setColumnWidth(3, 150)
        self.split.addWidget(self.events)
        self.drawer = QWidget()
        drawer = QVBoxLayout(self.drawer)
        drawer.setContentsMargins(6, 0, 0, 0)
        detailBar = QHBoxLayout()
        self.readContext = _button(detailBar, "查看本轮上下文", self._viewContext)
        self.navigate = _button(detailBar, "定位画布节点", self._navigate)
        detailBar.addStretch(1)
        _button(detailBar, "关闭详情", self._closeDrawer)
        drawer.addLayout(detailBar)
        deviceBar = QHBoxLayout()
        self.deviceChoice = QComboBox()
        deviceBar.addWidget(self.deviceChoice, 1)
        self.deviceButton = _button(deviceBar, "设备诊断", self._openDevice)
        self.deviceActions = QWidget()
        self.deviceActions.setLayout(deviceBar)
        drawer.addWidget(self.deviceActions)
        self.deviceHistoryNote = _label()
        drawer.addWidget(self.deviceHistoryNote)
        self.detail = LogRecordDetail(self.drawer)
        drawer.addWidget(self.detail, 1)
        self.drawer.setMinimumWidth(355)
        self.split.addWidget(self.drawer)
        self.split.setStretchFactor(0, 2)
        self.split.setStretchFactor(1, 1)
        self.drawer.hide()
        panel.addWidget(self.split, 1)
        self.jobHint = _label()
        panel.addWidget(self.jobHint)
        self.pages.addWidget(self.jobPage)
        self.mode.currentIndexChanged.connect(self._modeChanged)
        self.level.currentIndexChanged.connect(self._scheduleRender)
        self.search.textChanged.connect(self._scheduleRender)
        self.warning.toggled.connect(self._scheduleRender)
        self.timeRange.toggled.connect(self._updateTimes)
        self.events.itemSelectionChanged.connect(self._eventSelected)
        self.events.itemClicked.connect(self._eventClicked)
        self.events.itemExpanded.connect(self._expandedItem)
        self.events.itemCollapsed.connect(lambda item: self._expanded.discard(item.data(0, _ROLE)))
        self.events.verticalScrollBar().sliderMoved.connect(self._readAt)
        self.events.viewport().installEventFilter(self)

    def _buildRelated(self):
        self.relatedPage = QWidget()
        panel = QVBoxLayout(self.relatedPage)
        header = QHBoxLayout()
        _button(header, "返回", self._leaveRelated)
        self.relatedTitle = _label()
        header.addWidget(self.relatedTitle, 1)
        panel.addLayout(header)
        self.relatedJobs = _tree(["关联任务", "Job", "状态", "运行时间"])
        self.relatedJobs.setColumnWidth(3, 150)
        panel.addWidget(self.relatedJobs, 1)
        actions = QHBoxLayout()
        self.openRelatedButton = _button(actions, "查看日志", self._openRelatedJob)
        self.openRelatedButton.setEnabled(False)
        actions.addStretch(1)
        panel.addLayout(actions)
        self.relatedJobs.itemSelectionChanged.connect(self._relatedActions)
        self.relatedJobs.itemDoubleClicked.connect(lambda *_: self._openRelatedJob())
        self.pages.addWidget(self.relatedPage)

    def _buildDevice(self):
        self.devicePage = QWidget()
        panel = QVBoxLayout(self.devicePage)
        bar = QHBoxLayout()
        _button(bar, "返回事件", self._deviceBack)
        self.deviceTitle = _label()
        bar.addWidget(self.deviceTitle, 1)
        self.deviceNext = _button(bar, "读取下一页缓存", self._deviceNext)
        panel.addLayout(bar)
        self.deviceInfo = _label()
        panel.addWidget(self.deviceInfo)
        self.devices = DeviceDiagnosticsView()
        self.devices.runtimeLogsButton.hide()
        self.devices.exportRequested.connect(lambda: exportDeviceCache(self.devices))
        panel.addWidget(self.devices, 1)
        self.pages.addWidget(self.devicePage)

    def showEvent(self, event):
        super().showEvent(event)
        self.clock.start()
        if self.pages.currentWidget() is self.catalog:
            self._refreshCatalog()
        elif self.job is not None and self.pages.currentWidget() is self.jobPage:
            if self._opening and self.pending is None:
                self._requestLatest()
            elif self._dirty:
                self._render()
            else:
                self._updateRange()

    def hideEvent(self, event):
        self.clock.stop()
        self.redraw.stop()
        if self.window is not None:
            self.window.pause()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        from PySide2.QtCore import QEvent
        if watched is self.events.viewport() and event.type() == QEvent.Wheel:
            if event.angleDelta().y() > 0 and self.window is not None:
                self.window.pause()
                self._updateRange()
        return super().eventFilter(watched, event)

    def synchronize(self, client, instance):
        transport = getattr(client, "runtimeService", None)
        if transport is self.transport and instance == self.instance:
            return
        self._cancel()
        self.client, self.transport, self.instance = client, transport, instance
        self.watermark = self.capabilities = None
        self.runs.clear()
        self.activity.clear()
        self.recent = ()
        self.history.clear()
        self.historyCursor = ""
        self.job = self.window = self._staged = self.structure = None
        self._sourceBookmark = self._relatedSource = self._restoreLocation = None
        self.returnSourceButton.hide()
        self._upper = self._before = self._earlierUpper = None
        self._opening = False
        self.interrupted()
        self.events.clear()
        self._closeDrawer()
        self.devices.setPage({})
        self.pages.setCurrentWidget(self.catalog)
        self._renderCatalog()
        if self.isVisible():
            self._refreshCatalog()

    def progress(self, instance, cursor):
        if instance != self.instance:
            return
        self.watermark = cursor
        self._readerOnline = True
        if self._opening and self.pending is None and self.isVisible():
            self._requestLatest()

    def interrupted(self):
        self._continuous = False
        self._readerOnline = False
        self._readerGeneration += 1

    def shutdown(self):
        self.clock.stop()
        self.redraw.stop()
        self._cancel()

    def _cancel(self):
        self.poll.stop()
        if self.pending is not None:
            future, context, _, _ = self.pending
            context.cancel()
            future.cancel()
            self.pending = None

    def _read(self, request, accept, hint, *, reportError=None):
        self._cancel()
        context = DisplayCallContext()
        future = self.work.submit(request, context)
        self.calls[future] = context
        self.pending = future, context, accept, reportError or hint.setText
        hint.setText("正在读取 Runtime…")
        self.poll.start()

    def _finish(self):
        for future in tuple(self.calls):
            if future.done():
                self.calls.pop(future, None)
        if self.pending is None or not self.pending[0].done():
            return
        future, _, accept, reportError = self.pending
        self.pending = None
        self.poll.stop()
        if getattr(self.client, "runtimeService", None) is not self.transport:
            return
        try:
            accept(future.result())
        except Exception as exc:
            reportError(str(exc))

    def _tick(self):
        self._ticks += 1
        if self.job is not None:
            self._updateTimes()
        if self._ticks % 3 or self.pending is not None or self._historyMode and self.pages.currentWidget() is self.catalog:
            return
        if self.pages.currentWidget() in (self.jobPage, self.catalog, self.relatedPage):
            self._refreshCatalog()

    def _refreshCatalog(self):
        if self.pages.currentWidget() is self.catalog and self._historyMode:
            self._readCatalogHistory()
            return
        client, instance, capabilities = self.client, self.instance, self.capabilities
        if not instance or not callable(getattr(client, "getRuntimeInfo", None)):
            self.catalogHint.setText("正在等待 Runtime 连接。")
            return

        isCatalog = self.pages.currentWidget() is self.catalog
        selectedJob = self.job if not isCatalog else None

        def request(context):
            features = set(capabilities) if capabilities is not None else set(client.getRuntimeInfo().capabilities)
            runs = client.listRuns(instance, "", includeTerminal="job_history_query_v1" not in features)
            if not runs.ok or getattr(runs, "runtime_instance_id", "") not in ("", instance):
                raise ValueError(runs.message or "Runtime 实例已切换。")
            recent = None
            if "job_history_query_v1" in features and isCatalog:
                recent = client.listJobHistory(instance, limit=20, cancellation=context)
                if not recent.ok or recent.runtime_instance_id != instance:
                    raise ValueError(recent.message or "Runtime 实例已切换。")
            selected = None
            if selectedJob is not None:
                selected = next((row for row in runs.runs if _jobKey(row) == _jobKey(selectedJob)), None)
                if selected is None and getattr(selectedJob, "active", False):
                    state = client.getRunState(instance, selectedJob.project_id, selectedJob.job_id)
                    if state.ok:
                        selected = state.run
                if selected is None and not selectedJob.status:
                    historical = client.getJobStatus(selectedJob.job_id)
                    if historical.ok and historical.project_id == selectedJob.project_id:
                        selected = SimpleNamespace(job_id=selectedJob.job_id, **{key: getattr(historical, key)
                            for key in ("status", "project_id", "workflow_id", "accepted_at_ms", "started_at_ms",
                                        "ended_at_ms", "error_code", "message")})
            return features, runs.runs, recent, selected

        def accept(value):
            self.capabilities, runs, recent, selected = value
            self.runs = {run.job_id: run for run in runs
                         if getattr(run, "runtime_instance_id", "") in ("", instance)}
            self.activity = {key: value for key, value in self.activity.items() if key in self.runs}
            if recent is not None:
                self.recent = tuple(recent.jobs)
            elif "job_history_query_v1" not in self.capabilities:
                self.recent = tuple(run for run in runs if run.status in _TERMINAL)
            if self.job is not None and self.pages.currentWidget() is self.jobPage:
                if selected is not None and _jobKey(selected) == _jobKey(self.job):
                    self.job = selected
                if self._opening:
                    self._requestLatest()
                self._updateTimes()
            self._renderCatalog()
            if self.pages.currentWidget() is self.relatedPage:
                self._renderRelated()

        self._read(request, accept, self.catalogHint)

    def _toggleHistory(self):
        self._historyMode = not self._historyMode
        self.catalogFilters.setVisible(self._historyMode)
        self.catalogHistory.setText("返回任务首页" if self._historyMode else "更早历史")
        self._renderCatalog()
        self._refreshCatalog()

    def _readCatalogHistory(self, nextPage=False):
        if not self.capabilities or "job_history_query_v1" not in self.capabilities:
            self.catalogHint.setText("跨重启任务目录不可用。仍可使用 Runtime 高级历史查询。")
            return
        client, instance = self.client, self.instance
        cursor = self.historyCursor if nextPage else ""
        if nextPage and not cursor:
            return
        if nextPage:
            scope = self.historyScope
        else:
            if not self.catalogDateRange.isValidRange():
                return
            status = self.catalogStatus.currentData()
            scope = dict(projectId=self.catalogProject.text().strip(), statuses=(status,) if status else (),
                         **self.catalogDateRange.options("startEndedAtMs", "endEndedAtMs"))

        def accept(reply):
            if not reply.ok or reply.runtime_instance_id != instance:
                raise ValueError(reply.message)
            self.history = reply.jobs
            self.historyCursor = reply.next_cursor if reply.has_more else ""
            self.historyScope = dict(scope)
            self._renderCatalog()

        self._read(lambda ctx: client.listJobHistory(instance, cursor=cursor, cancellation=ctx, **scope),
                   accept, self.catalogHint)

    def _renderCatalog(self):
        selected = self._selectedJob()
        selectedKey = _jobKey(selected)
        scroll = self.directory.verticalScrollBar().value()
        self.directory.blockSignals(True)
        self.directory.clear()
        self.catalogTitle.setText("历史任务" if self._historyMode else "运行任务")
        current = {_jobKey(row): row for row in self.runs.values()}
        if self._historyMode:
            groups = [("终态任务", [current.get(_jobKey(row), row) for row in self.history])]
        else:
            unfinished = [row for row in current.values() if row.status in _UNFINISHED]
            ended = {_jobKey(row): current.get(_jobKey(row), row) for row in self.recent}
            ended.update((key, row) for key, row in current.items() if row.status in _TERMINAL and row.active)
            groups = [("运行中", sorted(unfinished, key=lambda row: (row.accepted_at_ms, row.job_id))),
                      ("最近已结束", sorted((row for row in ended.values() if row.status in _TERMINAL),
                                           key=lambda row: (row.ended_at_ms, row.job_id), reverse=True))]
        for title, rows in groups:
            group = QTreeWidgetItem([title])
            group.setFlags(Qt.ItemIsEnabled)
            self.directory.addTopLevelItem(group)
            group.setExpanded(True)
            if not rows:
                group.addChild(QTreeWidgetItem(["暂无任务"]))
            for job in rows:
                timing = jobExecutionTime(job)
                hint = self._secondaryStatus(job)
                activity = self.activity.get(job.job_id, _jobMessage(job))
                item = QTreeWidgetItem([_jobName(job), job.project_id, _STATUS.get(job.status, job.status),
                    _stamp(timing.startedAtMs), timing.display(), " · ".join(filter(None, (hint, activity)))])
                item.setData(0, _ROLE, job)
                item.setToolTip(0, job.job_id)
                tip = "\n".join(filter(None, (_UNFINISHED_PROCESSING, activity))) if hint == "结束处理未完成" else item.text(5)
                item.setToolTip(5, tip)
                group.addChild(item)
                if _jobKey(job) == selectedKey:
                    self.directory.setCurrentItem(item)
        self.directory.verticalScrollBar().setValue(scroll)
        self.directory.blockSignals(False)
        if self.capabilities is not None:
            supported = "job_history_query_v1" in self.capabilities
            if self._historyMode and supported:
                dates = describeDateBounds((self.historyScope.get("startEndedAtMs"), self.historyScope.get("endEndedAtMs")))
                self.catalogHint.setText("终态任务 · 结束时间" + (f" · {dates}" if dates else "不限"))
            else:
                self.catalogHint.setText(f"Runtime {self.instance}" if supported else
                    "跨重启任务目录不可用。仍可使用 Runtime 高级历史查询。")
            self.catalogHistory.setEnabled(supported)
        self.catalogNext.setEnabled(bool(self.historyCursor))
        self._catalogActions()

    def _selectedJob(self):
        item = self.directory.currentItem()
        return item.data(0, _ROLE) if item else None

    def _catalogActions(self):
        job = self._selectedJob()
        self.openButton.setEnabled(job is not None)
        self.stopButton.setVisible(bool(job and not self._historyMode and "stop" in getattr(job, "allowed_actions", ())))
        members = self._relatedMembers(job)
        self.catalogRelatedButton.setVisible(bool(members))
        self.stopButton.setText("停止关联任务" if members and job.status in _TERMINAL else "停止任务")
        self.stopButton.setToolTip("可向 Runtime 发送停止请求，结果以实际返回为准")

    def _relatedMembers(self, job):
        if job is None or not job.project_id or not self.instance:
            return ()
        source = self.runs.get(job.job_id)
        if (_jobKey(source) != _jobKey(job) or source.runtime_instance_id != self.instance
                or not source.run_group_id):
            return ()
        return tuple(sorted((row for row in self.runs.values()
                             if row.job_id != source.job_id and row.status in _UNFINISHED
                             and row.runtime_instance_id == self.instance and row.project_id == source.project_id
                             and row.run_group_id == source.run_group_id and row.source_job_id == source.job_id),
                            key=lambda row: (row.accepted_at_ms, row.job_id)))

    def _secondaryStatus(self, job):
        if job.status not in _TERMINAL or not getattr(job, "active", False):
            return ""
        members = self._relatedMembers(job)
        return f"还有 {len(members)} 个关联任务未结束" if members else "结束处理未完成"

    def _openCatalogRelated(self):
        self._openRelated(self._selectedJob(), catalog=True)

    def _openJobRelated(self):
        self._openRelated(self.job, catalog=False)

    def _openRelated(self, job, *, catalog):
        if not self._relatedMembers(job):
            return
        if not catalog and self.window is not None:
            self._rememberAnchor()
            self.window.pause()
        self._relatedSource = job
        self._relatedFromCatalog = catalog
        self.pages.setCurrentWidget(self.relatedPage)
        self._renderRelated()

    def _renderRelated(self):
        source = self._relatedSource
        if source is None:
            return
        members = self._relatedMembers(source)
        selected = self.relatedJobs.currentItem()
        selectedKey = _jobKey(selected.data(0, _ROLE)) if selected is not None else None
        self.relatedTitle.setText(f"{_jobName(source)} · " + (f"还有 {len(members)} 个关联任务未结束" if members
                                  else "当前快照没有可确认的未结束关联任务。"))
        self.relatedJobs.clear()
        for job in members:
            item = QTreeWidgetItem([_jobName(job), job.job_id[:8], _STATUS[job.status], jobExecutionTime(job).display()])
            item.setData(0, _ROLE, job)
            item.setToolTip(1, job.job_id)
            self.relatedJobs.addTopLevelItem(item)
            if _jobKey(job) == selectedKey:
                self.relatedJobs.setCurrentItem(item)
        self._relatedActions()

    def _relatedActions(self):
        self.openRelatedButton.setEnabled(self.relatedJobs.currentItem() is not None)

    def _leaveRelated(self):
        self._relatedSource = None
        self.pages.setCurrentWidget(self.catalog if self._relatedFromCatalog else self.jobPage)
        if self._dirty and not self._relatedFromCatalog:
            self._render()

    def _openRelatedJob(self):
        selected = self.relatedJobs.currentItem()
        source = self._relatedSource
        if selected is None or source is None:
            return
        key = _jobKey(selected.data(0, _ROLE))
        member = next((row for row in self._relatedMembers(source) if _jobKey(row) == key), None)
        if member is None:
            self._renderRelated()
            return
        bookmark = {"job": dict(jobId=source.job_id, projectId=source.project_id, workflowId=source.workflow_id),
                    "catalog": self._relatedFromCatalog, "instance": self.instance}
        if not self._relatedFromCatalog and self.window is not None:
            bookmark.update(filters=self._captureFilters(), mode=self.mode.currentIndex(),
                            selection=self._selection, anchors=dict(self._anchors), expanded=set(self._expanded),
                            context=self._context, reveal=self._reveal, previousContext=self._previousContext,
                            drawer=not self.drawer.isHidden(), info=not self.info.isHidden(),
                            detailTab=self.detail.tabs.currentIndex(), showTimes=self.timeRange.isChecked(),
                            first=self.window.firstEventId, last=self.window.lastEventId,
                            upper=max(self._upper or 0, self.window.lastEventId or 0))
        self._relatedSource = None
        self.openJob(member, sourceBookmark=bookmark)

    def _returnSource(self):
        bookmark = self._sourceBookmark
        if bookmark is None or bookmark["instance"] != self.instance:
            return
        source = bookmark["job"]
        if bookmark["catalog"]:
            self.home()
            for index in range(self.directory.topLevelItemCount()):
                group = self.directory.topLevelItem(index)
                for childIndex in range(group.childCount()):
                    item = group.child(childIndex)
                    if _jobKey(item.data(0, _ROLE)) == (source["projectId"], source["jobId"]):
                        self.directory.setCurrentItem(item)
                        self.directory.scrollToItem(item)
                        return
        else:
            self.openJob(source, restore=bookmark)

    def _stopSelectedJob(self):
        job, client, instance = self._selectedJob(), self.client, self.instance
        if not job or self._historyMode or "stop" not in getattr(job, "allowed_actions", ()):
            return

        def request(_):
            state = client.getRunState(instance, job.project_id, job.job_id)
            if not state.ok or "stop" not in state.run.allowed_actions:
                raise ValueError(state.message or "任务当前不可停止。")
            return client.stopJob(job.job_id, expectedRuntimeInstanceId=instance,
                                  expectedProjectId=job.project_id, expectedRunGroupId=state.run.run_group_id)

        def accept(reply):
            if not reply.ok:
                raise ValueError(reply.message)
            self._refreshCatalog()

        self._read(request, accept, self.catalogHint)

    def home(self):
        self._cancel()
        self.redraw.stop()
        self._opening = False
        self.job = self.window = self.structure = self._staged = None
        self.events.clear()
        self._items.clear()
        self._branches.clear()
        self._sourceBookmark = self._relatedSource = self._restoreLocation = None
        self.returnSourceButton.hide()
        self._upper = self._before = None
        self._closeDrawer()
        self.pages.setCurrentWidget(self.catalog)
        self._refreshCatalog()

    def _openSelectedJob(self):
        job = self._selectedJob()
        if job is not None:
            self.openJob(job)

    def openJob(self, job, *, eventId=None, upper=None, focus=None, sourceBookmark=None, restore=None):
        if isinstance(job, dict):
            jobId, projectId = job["jobId"], job["projectId"]
            current = self.runs.get(jobId)
            known = current if _jobKey(current) == (projectId, jobId) else next((row for row in (*self.recent, *self.history)
                if _jobKey(row) == (projectId, jobId)), None)
            job = known or SimpleNamespace(job_id=jobId, project_id=projectId,
                workflow_id=job.get("workflowId", ""), status="", message="", accepted_at_ms=0,
                started_at_ms=0, ended_at_ms=0, error_code="")
        self._cancel()
        self.redraw.stop()
        self.window = self.structure = None
        self.events.clear()
        self.job = job
        self._sourceBookmark = sourceBookmark
        self.returnSourceButton.setVisible(sourceBookmark is not None)
        self._anchors.clear()
        self._expanded.clear()
        self._selection = ("event", eventId) if eventId else None
        self._focus = focus
        self._context = self._reveal = self._previousContext = None
        self._closeDrawer()
        self.jobHint.clear()
        self.exportFeedback("", False)
        self.filters.hide()
        self.technicalToggle.setChecked(False)
        self._resetFilters()
        self.mode.setCurrentIndex(0)
        if restore is not None:
            self._restoreFilters(restore["filters"])
            self.mode.setCurrentIndex(restore["mode"])
            self.info.setVisible(restore["info"])
            self.timeRange.setChecked(restore["showTimes"])
        self.pages.setCurrentWidget(self.jobPage)
        self._openLatest(eventId=eventId, upper=upper, restore=restore)
        self._updateTimes()

    def _openLatest(self, *, eventId=None, upper=None, locate=False, restore=None):
        if self.job is None:
            return
        self._cancel()
        self._context = self._reveal = self._previousContext = None
        self.returnContext.hide()
        self._closeDrawer()
        self.window = JobEventWindow(self.job.job_id, self.maximumEntries)
        self.structure = None
        self._upper = self._before = self._earlierUpper = None
        self._restoreLocation = restore
        if restore is not None and restore["last"] is not None:
            eventId, upper = restore["last"], restore["upper"]
        self._targetEvent, self._targetUpper = eventId, upper
        self._locateAfterLoad = locate
        self._staged = deque(maxlen=self.maximumEntries)
        self._stagedTruncated = False
        self._opening = True
        self._continuous = False
        self.olderWindow.hide()
        self._render(captureAnchor=False)
        self._requestLatest()

    def _requestLatest(self):
        if not self._opening or self.job is None:
            return
        if self.capabilities is None:
            self._refreshCatalog()
            return
        if "runtime_logs_recent_v1" not in self.capabilities:
            self._opening = False
            self._staged = None
            self.jobHint.setText("此 Runtime 不支持最近页。可使用高级筛选中的历史范围查询。")
            self._updateRange()
            return
        if self.watermark is None:
            self.jobHint.setText("等待 Runtime 日志读取器定位。")
            return
        client, query = self.client, self._queryScope()
        target = self._targetEvent
        restore = self._restoreLocation
        readerGeneration = self._readerGeneration
        if target:
            query.update(beforeEventId=target + 1, upperEventId=self._targetUpper)

        def accept(reply):
            self._requirePage(reply)
            self._upper = reply["upperEventId"]
            self._before = reply["nextEventId"] if reply["hasMore"] else None
            self.window.mergeRecent(self._pageEntries(reply))
            if not target and self.watermark > self._upper:
                self._fillGap(self._upper, self.watermark, readerGeneration)
                return
            self._completeLatest(readerGeneration)

        def failed(message):
            self._opening = False
            self._staged = None
            self._restoreLocation = None
            self.jobHint.setText(message + "。可返回最新重试或查询历史。")
            self._updateRange()

        def request(context):
            if restore is not None and restore["first"] is not None:
                return self._readBookmarkedRange(client, query, restore["first"], context)
            return client.queryRuntimeLogs(**query, latestFirst=True, cancellation=context)

        self._read(request, accept, self.jobHint, reportError=failed)

    def _readBookmarkedRange(self, client, query, first, context):
        # The bookmark holds two range keys. Rebuild only the old bounded
        # window, using the same descending cursor as Load Earlier.
        rows = []
        while True:
            limit = min(1000, self.maximumEntries - len(rows))
            reply = client.queryRuntimeLogs(**query, latestFirst=True, limit=limit, cancellation=context)
            if not reply["ok"] or reply["runtimeInstanceId"] != query["runtimeInstanceId"]:
                return reply
            loaded = reply["events"]
            inside = [row for row in loaded if row["eventId"] >= first]
            rows.extend(inside)
            cursor = reply["nextEventId"]
            more = reply["hasMore"] or len(inside) < len(loaded)
            if not reply["hasMore"] or cursor <= first or len(rows) >= self.maximumEntries:
                if rows:
                    return dict(reply, events=rows, nextEventId=rows[-1]["eventId"], hasMore=more)
                # The saved range is no longer queryable. Leave the filters in
                # place and give the reader the available recent window.
                options = {key: val for key, val in query.items() if key not in {"beforeEventId", "upperEventId"}}
                return client.queryRuntimeLogs(**options, latestFirst=True, cancellation=context)
            if cursor >= query["beforeEventId"]:
                raise ValueError("Runtime 日志游标未推进。")
            query = dict(query, beforeEventId=cursor)

    def _completeLatest(self, readerGeneration, gapEntries=(), *, truncated=False, error=""):
        if not self._targetEvent:
            staged = (entry for entry in self._staged if entry.eventId > self._upper)
            self.window.appendLive(chain(gapEntries, staged), truncated=truncated or self._stagedTruncated)
        if self._targetEvent or not self.isVisible():
            self.window.pause()
        self._staged = None
        self._opening = False
        self._continuous = (not self._targetEvent and not error and self._readerOnline
                            and readerGeneration == self._readerGeneration)
        self.jobHint.setText(error)
        restore, self._restoreLocation = self._restoreLocation, None
        self._render(captureAnchor=False, restore=restore)
        locate, self._locateAfterLoad = self._locateAfterLoad, False
        if locate and not error:
            self._applyLocation()
        elif restore is not None:
            self._restoreBookmarkedPosition(restore)
        elif self._targetEvent and self._selection in self._items:
            self._select(self._selection)

    def _restoreBookmarkedScope(self, bookmark):
        self._selection, self._anchors, self._expanded = bookmark["selection"], bookmark["anchors"], bookmark["expanded"]
        self._context, self._reveal = bookmark["context"], bookmark["reveal"]
        self._previousContext = bookmark["previousContext"]
        if ((self._context is not None and self.structure.contextMatches(self._context) is None)
                or (self._reveal is not None and self.structure.representativeEventId(self._reveal) is None)):
            self._context = self._reveal = self._previousContext = None
            self._selection = None
        self.returnContext.setVisible(self._context is not None or self._reveal is not None)

    def _restoreBookmarkedPosition(self, bookmark):
        item = self.events.currentItem()
        selected = item.data(0, _ROLE) if item is not None else None
        if (selected != bookmark["selection"] or self._context != bookmark["context"] or self._reveal != bookmark["reveal"]
                or any(self.structure.representativeEventId(key) is None for key in self._anchors.values())):
            self.jobHint.setText("原阅读位置无法在当前可查询范围定位，已显示可用记录。")
        if selected == bookmark["selection"] and bookmark["drawer"]:
            self._restoreSelectionDrawer()
            if self._detailEntry is not None:
                self.detail.tabs.setCurrentIndex(bookmark["detailTab"])

    def _queryScope(self):
        return dict(projectId=self.job.project_id, jobId=self.job.job_id, runtimeInstanceId=self.instance)

    def _requirePage(self, reply):
        if not reply["ok"] or reply["runtimeInstanceId"] != self.instance:
            raise ValueError(reply["message"] or "Runtime 实例已切换。")

    def _pageEntries(self, reply):
        return [self.makeEntry(dict(row["event"], eventId=row["eventId"])) for row in reply["events"]]

    def _fillGap(self, lower, upper, readerGeneration):
        client, options = self.client, self._queryScope()
        maximum = self.maximumEntries

        def request(context):
            cursor = lower
            rows = deque(maxlen=maximum)
            count = 0
            while cursor < upper:
                reply = client.queryRuntimeLogs(**options, afterEventId=cursor, upperEventId=upper, cancellation=context)
                if not reply["ok"] or reply["runtimeInstanceId"] != options["runtimeInstanceId"]:
                    raise ValueError(reply["message"] or "Runtime 实例已切换。")
                rows.extend(reply["events"])
                count += len(reply["events"])
                nextCursor = reply["nextEventId"]
                if nextCursor <= cursor:
                    raise ValueError("Runtime 日志游标未推进。")
                cursor = nextCursor
                if not reply["hasMore"]:
                    break
            return rows, count

        def accept(value):
            rows, count = value
            self._completeLatest(readerGeneration, self._pageEntries({"events": rows}), truncated=count > maximum)

        def failed(message):
            self._completeLatest(readerGeneration, error="增量补齐失败：" + message + "。可返回最新重试。")

        self._read(request, accept, self.jobHint, reportError=failed)

    def _readEarlier(self, *, replace=False):
        if not self._canReadEarlier():
            return
        if self._earlierUpper is None:
            self._earlierUpper = self._upper
            if self.window.liveTrimmed:
                self._before = self.window.firstEventId
                self._earlierUpper = max(self._upper, self.window.lastEventId)
        self.window.pause()
        client = self.client
        scope = dict(self._queryScope(), beforeEventId=self._before, upperEventId=self._earlierUpper, latestFirst=True,
                     limit=min(200, self.maximumEntries))

        def accept(reply):
            self._requirePage(reply)
            self.jobHint.clear()
            entries = self._pageEntries(reply)
            if replace:
                self.window.replaceEarlier(entries)
                self._closeDrawer()
                self._selection = None
                self._anchors.clear()
                self._context = self._reveal = self._previousContext = None
                self.returnContext.hide()
            else:
                self._rememberAnchor()
                keys = [self._selection, *self._anchors.values()]
                if self._context is not None or self._reveal is not None:
                    previousSelection, previousAnchors, _ = self._previousContext
                    keys.extend((previousSelection, *previousAnchors.values()))
                if self._context is not None:
                    keys.append(("event", self._context))
                protected = {self.structure.representativeEventId(key) for key in keys if key}
                if not self.window.mergeEarlier(entries, protected=protected):
                    self.jobHint.setText("已达窗口上限。可切换到更早范围，或返回最新。")
                    self.olderWindow.show()
                    return
            self.olderWindow.hide()
            self._before = reply["nextEventId"] if reply["hasMore"] else None
            self._render()

        self._read(lambda context: client.queryRuntimeLogs(**scope, cancellation=context), accept, self.jobHint)

    def _canReadEarlier(self):
        return self.window is not None and not self._opening and bool(
            self._before or self._earlierUpper is None and self.window.liveTrimmed and self.window.firstEventId)

    def appendEntry(self, entry):
        if entry.eventId and entry.effectiveJobId in self.runs:
            self.activity[entry.effectiveJobId] = entry.displayMessage()[:200]
        if self.window is None:
            return
        if not entry.eventId or entry.effectiveJobId != self.window.jobId:
            return
        if self.job.project_id and entry.projectId != self.job.project_id:
            return
        if self._staged is not None:
            self._stagedTruncated |= len(self._staged) == self._staged.maxlen
            self._staged.append(entry)
        elif self._upper is not None and entry.eventId > self._upper:
            self.window.appendLive((entry,))
            self._scheduleRender()

    def _toggleInfo(self):
        self.info.setVisible(self.info.isHidden())

    def _toggleFilters(self):
        self.filters.setVisible(self.filters.isHidden())
        if not self.filters.isHidden() and self.window is not None:
            updateIdentityChoices(self.inputs, self.window.entries)

    def _toggleTechnicalFilters(self, checked):
        self.technicalFilters.setVisible(checked)

    def _acceptDateFilter(self):
        if self.dateRange.isValidRange() and self.dateRange.bounds() != self._effectiveDateBounds:
            self._effectiveDateBounds = self.dateRange.bounds()
            self._scheduleRender()
        self._updateRange()

    def _filterValues(self):
        return {key: edit.text().strip() for key, edit in self.inputs.items() if edit.text().strip()}

    def _captureFilters(self):
        return dict(values=self._filterValues(), search=self.search.text(), warning=self.warning.isChecked(),
                    level=self.level.currentIndex(), draft=self.dateRange.bounds(), dates=self._effectiveDateBounds,
                    advanced=not self.filters.isHidden(), technical=self.technicalToggle.isChecked())

    def _restoreFilters(self, state):
        for key, edit in self.inputs.items():
            edit.setText(state["values"].get(key, ""))
        self.search.setText(state["search"])
        self.warning.setChecked(state["warning"])
        self.level.setCurrentIndex(state["level"])
        self.dateRange.setBounds(*state["draft"])
        self._effectiveDateBounds = state["dates"]
        self.filters.setVisible(state["advanced"])
        self.technicalToggle.setChecked(state["technical"])
        self._toggleTechnicalFilters(state["technical"])
        self._scheduleRender()

    def _resetFilters(self):
        self._restoreFilters(dict(values={}, search="", warning=False, level=0, draft=(None, None), dates=(None, None),
                                  advanced=not self.filters.isHidden(), technical=self.technicalToggle.isChecked()))

    def _effectiveLevels(self) -> tuple[str, ...] | None:
        level = self.level.currentText() if self.level.currentIndex() else ""
        if self.warning.isChecked():
            levels = ("WARN", "ERROR")
            return tuple(value for value in levels if not level or value == level)
        return (level,) if level else None

    def _advancedHistory(self):
        values = self._filterValues()
        filters = {name: values[name] for name in ("deviceId", "component", "workflowId", "nodeId") if name in values}
        filters.update({key: value for key, value in zip(("startTimestampMs", "endTimestampMs"), self._effectiveDateBounds)
                        if value is not None})
        if self.job is not None:
            filters.update(jobId=self.job.job_id, projectId=self.job.project_id)
        filters["levels"] = self._effectiveLevels()
        self.historyRequested.emit(filters)

    def _export(self):
        if self.job is not None:
            self.exportRequested.emit(self._queryScope())

    def exportFeedback(self, message, busy):
        self.exportStatus.setText(message)
        self.exportStatus.setVisible(bool(message))
        self.exportCancel.setVisible(bool(busy))

    def _scopePredicate(self):
        if self._context is not None:
            return self.structure.contextMatches(self._context) or (lambda _entry: False)
        filters = self._filterValues()
        levels = self._effectiveLevels()
        needle = self.search.text().strip().casefold()
        bounds = self._effectiveDateBounds

        def matches(entry):
            if self._reveal == ("event", entry.eventId):
                return True
            if levels is not None and entry.level not in levels:
                return False
            if needle and needle not in " ".join((entry.message, entry.displayMessage(), entry.code,
                    entry.eventType, entry.displayWorkflow(), entry.displayNode())).casefold():
                return False
            if not includesTimestamp(entry.rawTimestampMs, bounds):
                return False
            for key in ("projectId", "workflowId", "nodeId", "eventType", "component", "workflowRunId"):
                if key in filters and getattr(entry, key) != filters[key]:
                    return False
            if "iterationPath" in filters and (
                    not entry.iterationPathRecorded or "/".join(map(str, entry.iterationPath)) != filters["iterationPath"]):
                return False
            return "deviceId" not in filters or any(device == filters["deviceId"] for _, device, _ in _deviceChoices(entry))

        return matches

    def _scheduleRender(self, *_):
        self._dirty = True
        if self.isVisible() and not self.redraw.isActive():
            self.redraw.start()

    def _rememberAnchor(self):
        item = self.events.itemAt(0, 1)
        if item is not None and item.data(0, _ROLE):
            self._anchors[self._shownMode] = item.data(0, _ROLE)

    def _modeChanged(self):
        self._rememberAnchor()
        self._shownMode = self.mode.currentIndex()
        self._render(captureAnchor=False)

    def _render(self, *, captureAnchor=True, restore=None):
        if self.window is None:
            return
        if captureAnchor:
            self._rememberAnchor()
        follow = not self.window.paused
        self._dirty = False
        self.structure = ExecutionTree(self.window.entries)
        if restore is not None:
            self._restoreBookmarkedScope(restore)
        if not self.filters.isHidden():
            updateIdentityChoices(self.inputs, self.window.entries)
        matches = self._scopePredicate()
        self._items.clear()
        self._branches.clear()
        self.events.blockSignals(True)
        self.events.setUpdatesEnabled(False)
        scrollBar = self.events.verticalScrollBar()
        previousScrollBlock = scrollBar.blockSignals(True)
        try:
            self.events.clear()
            if self.mode.currentIndex():
                for entry in sorted(filter(matches, self.window.entries), key=lambda row: (row.timestampMs, row.eventId)):
                    self.events.addTopLevelItem(self._entryItem(entry))
            else:
                for branch in self.structure.project(matches):
                    self._addBranch(branch, self.events.invisibleRootItem())
            self._expanded.intersection_update(self.structure.branchKeys())
            selection = self._items.get(self._selection)
            if selection is not None:
                self.events.setCurrentItem(selection)
            anchor = self._items.get(self._anchors.get(self.mode.currentIndex()))
            if anchor is not None and self.window.paused:
                self.events.scrollToItem(anchor, QAbstractItemView.PositionAtTop)
            elif follow:
                self.events.scrollToBottom()
        finally:
            scrollBar.blockSignals(previousScrollBlock)
            self.events.setUpdatesEnabled(True)
            self.events.blockSignals(False)
        if self._focus:
            workflowRunId, nodeRunId = self._focus
            target = next((row for row in reversed(self.window.entries)
                           if row.workflowRunId == workflowRunId and row.nodeRunId == nodeRunId), None)
            if target is not None:
                self._focus = None
                self._select(("event", target.eventId))
            elif not self._opening:
                self.jobHint.setText("该次节点执行未在已加载范围内。可以加载更早的记录。")
        self._updateRange()
        self._updateTimes()

    def _entryItem(self, entry):
        key = ("event", entry.eventId)
        item = QTreeWidgetItem([entry.displayMessage() or entry.eventType, entry.level,
                               entry.displayTime() if entry.rawTimestampMs else "未记录"])
        item.setData(0, _ROLE, key)
        item.setToolTip(0, "\n".join(part for part in (entry.message, entry.eventType, entry.code) if part))
        self._items[key] = item
        return item

    def _addBranch(self, branch, parent):
        if branch.entry is not None:
            parent.addChild(self._entryItem(branch.entry))
            return
        item = QTreeWidgetItem([branch.title])
        item.setData(0, _ROLE, branch.key)
        tooltip = [branch.title]
        kind, *identity = branch.key
        if kind == "workflow":
            tooltip.append(f"工作流调用 ID {identity[0]}")
            source = branch.start or branch.end
            if source is not None and source.parentWorkflowRunId:
                tooltip.append(f"父调用 ID {source.parentWorkflowRunId}")
        elif kind in {"node", "loop", "iteration"}:
            tooltip.append(f"工作流调用 ID {identity[0]}")
            if kind == "node":
                tooltip.append(f"节点执行 ID {identity[1]}")
            else:
                tooltip.append(f"循环节点 ID {identity[1]}")
                tooltip.append("原始轮次路径 " + ("/".join(map(str, identity[2:])) or "[]"))
        item.setToolTip(0, "\n".join(tooltip))
        self._items[branch.key] = item
        self._branches[branch.key] = branch
        parent.addChild(item)
        for child in branch.children:
            self._addBranch(child, item)
        item.setExpanded(branch.key in self._expanded or (
            parent == self.events.invisibleRootItem() and not self._expanded))

    def _updateRange(self):
        if self.window is None:
            return
        suffix = []
        job = self.job
        if job is not None:
            self.filterScope.setText("当前任务范围 · 名称候选来自已加载事件")
            self.filterScope.setToolTip(f"工程 {job.project_id} · Job {job.job_id}")
        filters = self._filterValues()
        dates = describeDateBounds(self._effectiveDateBounds)
        invalidDates = not self.dateRange.isValidRange()
        self.clearFilters.setVisible(bool(filters or dates or invalidDates or self.search.text().strip()
                                         or self.warning.isChecked() or self.level.currentIndex()))
        if self._context is not None:
            suffix.append("本轮已加载上下文" + (" · 可加载更早事件" if self._canReadEarlier() else ""))
        else:
            summary = [f"{label} {filters[key]}" for key, label in (*_COMMON_FIELDS, *_TECHNICAL_FIELDS) if key in filters]
            if dates:
                summary.append(dates)
            if invalidDates:
                summary.append("开始时间不能晚于结束时间，沿用有效范围")
            if self.search.text().strip():
                summary.insert(0, f"关键字 {self.search.text().strip()}")
            if self.warning.isChecked():
                summary.insert(0, "告警 / 错误")
            if self.level.currentIndex():
                summary.insert(0, self.level.currentText())
            suffix.append("筛选范围：已加载事件" + (" · " + "，".join(summary) if summary else ""))
        if self.structure is not None and self.structure.rootCount:
            suffix.append(f"已加载 {self.structure.rootCount} 次根调用")
        if self.window.trimmed:
            suffix.append("部分事件已移出当前窗口")
        if self.window.paused:
            suffix.append(f"历史阅读 · 已观察到 {self.window.newCount} 条新事件"
                          if self.window.newCount else "历史阅读")
        else:
            suffix.append("实时跟随" if not self._opening and self._upper is not None else "等待最近页")
        self.rangeStatus.setText(f"已加载 {len(self.window.entries)} 条 · " + " · ".join(suffix))
        self.earlier.setEnabled(self._canReadEarlier())

    def _updateTimes(self):
        if self.job is None:
            return
        time = jobExecutionTime(self.job)
        title = _jobName(self.job)
        status = _STATUS.get(self.job.status, self.job.status or "状态未确认")
        self.jobTitle.setText(f"{title}   ·   {status}   ·   {time.display()}")
        secondary = self._secondaryStatus(self.job)
        self.jobSecondary.setText(secondary)
        self.jobSecondary.setVisible(bool(secondary))
        self.relatedButton.setVisible(bool(self._relatedMembers(self.job)))
        extra = "\n重启确认 " + _stamp(time.confirmedAtMs) if time.confirmedAtMs else ""
        if secondary == "结束处理未完成":
            extra += "\n" + _UNFINISHED_PROCESSING
        active = self.structure.activeKeys(jobStatus=self.job.status, runtimeInstanceId=self.instance,
            continuous=self._continuous and self.window is not None and not self.window.paused) if self.structure else set()
        if self.job.status in {"RUNNING", "STOPPING"}:
            calls = sorted(key[1] for key in active if key[0] == "workflow")
            description = f"{len(calls)} 次" if calls else "未确认"
            extra += "\n活动调用 " + description
            self.info.setToolTip("活动工作流调用 ID\n" + "\n".join(calls) if calls else "")
        else:
            self.info.setToolTip("")
        if self.job.job_id in self.activity:
            extra += "\n最近活动 " + self.activity[self.job.job_id]
        self.info.setText(f"Job {self.job.job_id}   ·   工程 {self.job.project_id or '未记录'}\n"
                          f"受理 {_stamp(time.acceptedAtMs)}   ·   实际开始 {_stamp(time.startedAtMs)}"
                          f"   ·   实际结束 {_stamp(time.endedAtMs)}{extra}\n"
                          + " ".join(value for value in (time.note, self.job.error_code, _jobMessage(self.job)) if value))
        if self.structure is None or self.window is None:
            return
        for key, branch in self._branches.items():
            timing = branch.executionTime(active=key in active, hasEarlier=self._canReadEarlier())
            item = self._items[key]
            if branch.end is not None:
                eventType = branch.end.eventType
                status = {"node.skipped": "已跳过", "loop.timeout": "已超时", "loop.limit_reached": "达到次数上限"}.get(eventType)
                if status is None:
                    status = "失败" if eventType.endswith(".failed") else "已完成"
                if branch.kind == "iteration" and branch.bodyExecuted is not True and branch.mode not in {"foreach", "repeat"}:
                    status = "已结束"
            else:
                status = "执行中" if key in active else ""
            item.setText(1, status)
            if timing.startedAtMs is not None and branch.start is not None:
                item.setText(2, branch.start.displayTime())
                item.setToolTip(2, _stamp(timing.startedAtMs))
            label = "时间信息不完整" if timing.note else timing.display(showRange=self.timeRange.isChecked())
            item.setText(3, label)
            item.setToolTip(3, timing.display(showRange=True))

    def _readAt(self, value):
        if self.window is not None and value < self.events.verticalScrollBar().maximum():
            self.window.pause()
            self._updateRange()

    def _expandedItem(self, item):
        self._expanded.add(item.data(0, _ROLE))
        if self.window and self.events.itemBelow(item) is not None:
            self.window.pause()
            self._updateRange()

    def _eventClicked(self, item, _column):
        if self.drawer.isHidden():
            self._eventSelected()

    def _eventSelected(self):
        item = self.events.currentItem()
        if item is None or self.window is None:
            return
        key = item.data(0, _ROLE)
        self._selection = key
        if not key or key[0] != "event":
            if key in self._branches:
                self.jobHint.setText(self._items[key].text(3))
            self._closeDrawer()
            return
        entry = next((row for row in self.window.entries if row.eventId == key[1]), None)
        if entry is None:
            return
        if not self.window.entries or entry != self.window.entries[-1]:
            self.window.pause()
        self._showEntry(entry)
        self._updateRange()

    def _showEntry(self, entry):
        self._detailEntry = entry
        raw = entry.rawRecord()
        context = {}
        branch = self.structure.branchForEvent(entry.eventId) if self.structure else None
        if branch is not None:
            timing = branch.executionTime(hasEarlier=bool(self._before))
            if timing.startedAtMs is not None:
                context["实际开始"] = _stamp(timing.startedAtMs)
            if timing.endedAtMs is not None:
                context["实际结束"] = _stamp(timing.endedAtMs)
            if timing.elapsedMs is not None:
                context["执行时间"] = timing.display()
            if timing.note:
                context["时间说明"] = timing.note
        if entry.effectiveJobId != entry.jobId:
            context["所属任务"] = entry.effectiveJobId
        for key, label in (("deviceId", "设备"), ("slot", "设备槽位"), ("channelId", "通道"),
                           ("requestId", "请求 ID"), ("occurrences", "设备同类问题累计次数（可跨任务）"),
                           ("durationMs", "聚合经过时间（ms，可跨任务）")):
            if key in entry.payload and (key not in {"durationMs", "occurrences"} or "occurrences" in entry.payload):
                context[label] = entry.payload[key]
        self.detail.setEvent(raw, displayMessage=entry.displayMessage(), context=context)
        self.readContext.setEnabled(bool(self.structure and self.structure.contextMatches(entry.eventId)))
        self.navigate.setEnabled(bool(self._onNavigate and entry.nodeId))
        self.deviceChoice.clear()
        for label, device, scope in _deviceChoices(entry):
            self.deviceChoice.addItem(f"{label} · {device}" if label != device else device, (device, scope))
        self.deviceActions.setVisible(bool(self.deviceChoice.count()))
        self.deviceHistoryNote.setText("已关联 Job，执行轮次未记录"
            if self.deviceChoice.count() and entry.effectiveJobId and not entry.iterationPathRecorded else "")
        self.deviceHistoryNote.setVisible(bool(self.deviceHistoryNote.text()))
        self.drawer.show()

    def _closeDrawer(self):
        self.drawer.hide()
        self._detailEntry = None
        self.detail.clear()

    def _navigate(self):
        if self._detailEntry is not None and self._onNavigate:
            self.jobHint.setText(self._onNavigate(self._detailEntry))

    def _select(self, key, *, followEntry=None):
        item = self._items.get(key)
        if item is None:
            return
        scrollBar = self.events.verticalScrollBar()
        previousScrollBlock = scrollBar.blockSignals(True)
        self.events.blockSignals(True)
        try:
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                self._expanded.add(parent.data(0, _ROLE))
                parent = parent.parent()
            self.events.setCurrentItem(item)
            self.events.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        finally:
            self.events.blockSignals(False)
            scrollBar.blockSignals(previousScrollBlock)
        if followEntry is None:
            self._eventSelected()
        else:
            self._selection = key
            self._showEntry(followEntry)
            self._updateRange()

    def _viewContext(self):
        if self._detailEntry is None:
            return
        eventId = self._detailEntry.eventId
        if self.structure.contextMatches(eventId) is not None:
            self._saveReadingPosition()
            self.window.pause()
            self._context = eventId
            self.returnContext.show()
            self._render(captureAnchor=False)

    def _saveReadingPosition(self):
        if self._context is None and self._reveal is None:
            self._rememberAnchor()
            self._previousContext = self._selection, dict(self._anchors), set(self._expanded)

    def _leaveContext(self):
        if self._context is not None or self._reveal is not None:
            self._selection, self._anchors, self._expanded = self._previousContext
        self._context = self._reveal = self._previousContext = None
        self.returnContext.hide()
        self._render(captureAnchor=False)
        if self._selection is not None and self._selection not in self._items:
            self.jobHint.setText("原阅读位置无法在当前可查询范围定位，已显示可用记录。")
        if not self.drawer.isHidden():
            self._restoreSelectionDrawer()

    def _restoreSelectionDrawer(self):
        item = self.events.currentItem()
        key = item.data(0, _ROLE) if item is not None else None
        if key is not None and key == self._selection and key[0] == "event":
            event = next((entry for entry in self.window.entries if entry.eventId == key[1]), None)
            if event is not None:
                self._showEntry(event)
                return
        self._closeDrawer()

    def _locate(self):
        if self.window is None or self.job is None or self._opening:
            return
        if self._context is not None or self._reveal is not None:
            self._leaveContext()
        if self.window.paused or not self._continuous:
            self._openLatest(locate=True)
        else:
            self._applyLocation()

    def _applyLocation(self):
        if self._dirty:
            self.redraw.stop()
            self._render()
        entry = self.structure.currentNode(jobStatus=self.job.status, runtimeInstanceId=self.instance,
            continuous=self._continuous and not self.window.paused)
        if entry is None:
            self.events.scrollToBottom()
            self.jobHint.setText("当前执行节点尚未确认。")
            return
        key = ("event", entry.eventId)
        if key not in self._items:
            self._saveReadingPosition()
            self._reveal = key
            self.returnContext.show()
            self._render(captureAnchor=False)
        self.jobHint.clear()
        self._select(key, followEntry=entry)

    def _openDevice(self):
        selected = self.deviceChoice.currentData()
        if selected is None or self._detailEntry is None:
            return
        device, scope = selected
        self.window.pause()
        self._rememberAnchor()
        self.pages.setCurrentWidget(self.devicePage)
        self.deviceTitle.setText("设备诊断 · " + device)
        self.devices.setPage({})
        self.deviceNext.setEnabled(False)
        self._deviceScope, self._deviceId = scope, device
        if (not all(scope.get(key) for key in ("runtimeInstanceId", "projectId", "projectLoadGeneration"))
                or scope["runtimeInstanceId"] != self.instance or scope["projectId"] != self.job.project_id):
            self.deviceInfo.setText("近期设备缓存与此记录范围无法匹配。可返回查看持久化任务日志。")
            return
        self._deviceRead(0)

    def _deviceNext(self):
        self._deviceRead(int(self.devices.page["cursor"]))

    def _deviceRead(self, cursor):
        client, scope, device = self.client, self._deviceScope, self._deviceId

        def failed(message):
            self.deviceInfo.setText("近期设备缓存不可用于此记录：" + message)

        def accept(page):
            if any(page.get(key) != scope[key] for key in ("runtimeInstanceId", "projectId", "projectLoadGeneration")):
                raise ValueError("Runtime 或工程加载代次已切换。")
            channels = dict.fromkeys(str(row.get("channelId") or row.get("selectors", {}).get("channelId") or "")
                                     for row in page.get("records", ()))
            self.devices.setPage(page, [{"name": key, "channelId": key} for key in channels if key])
            self.deviceNext.setEnabled(bool(page.get("more")))
            scopeText = "本设备近期缓存，可能包含其他任务或轮次。" if page.get("records") else "此范围没有近期设备诊断缓存。"
            if page.get("dropped"):
                scopeText += "Runtime 设备缓存曾淘汰早期记录。"
            self.deviceInfo.setText(scopeText)

        self._read(lambda context: client.getDeviceDiagnostics(scope, device, cursor, cancellation=context), accept,
                   self.deviceInfo, reportError=failed)

    def _deviceBack(self):
        self._cancel()
        self.pages.setCurrentWidget(self.jobPage)
