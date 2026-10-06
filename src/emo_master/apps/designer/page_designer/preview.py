"""Read-only current-project viewing and separately owned isolated draft debugging."""
import queue
import threading
import time

import grpc
from PySide2.QtCore import QObject, QTimer
from PySide2.QtWidgets import QInputDialog
from shiboken2 import isValid

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.coverage import CaptureCoverage


LOCAL_OPERATORS = frozenset({'vision.io.image_loader', 'vision.analysis.blob',
    'vision.collection.count', 'vision.io.image_saver'})
STATUS_LABELS = {'ACCEPTED': '等待开始', 'STARTING': '正在启动', 'RUNNING': '运行中',
                 'STOPPING': '正在停止', 'COMPLETED': '已完成', 'FAILED': '失败', 'ABORTED': '已停止'}


def checkLocalDraft(document):
    """P4 permission boundary: trusted local image pipeline only, no device operators."""
    if document.presentation is None or document.resources is None:
        raise ValueError('请先启用页面设计')
    imageSources = {document.presentation.dataSources[key].model_dump_json()
        for page in document.presentation.pages.values()
        for component in walkComponents(page.components)
        for key in component.bindings.values() if key in document.presentation.dataSources
        and document.presentation.dataSources[key].expectedType == 'image'}
    if len(imageSources) > 1:
        raise ValueError('隔离草稿调试/测试入口仅支持单图来源，请跨页复用；正常运行可按 Runtime 广告能力使用有界双图配置')
    inputs = {(b.target.workflowId, b.target.nodeId, tuple(b.target.parameterPath))
              for b in document.resources.parameterBindings}
    outputs = {(b.target.workflowId, b.target.nodeId, tuple(b.target.parameterPath))
               for b in document.resources.siteBindings if b.purpose == 'output_file'}
    for workflowId, workflow in document.workflows.items():
        for node in workflow.nodes:
            if node.kind != 'operator':
                continue
            if node.operatorId not in LOCAL_OPERATORS:
                raise ValueError(f'本地调试尚不支持算子 {node.operatorId}；不执行设备或未知插件')
            if node.operatorId == 'vision.io.image_loader' and (workflowId, node.nodeId, ('imagePath',)) not in inputs:
                raise ValueError(f'请先登记输入资源：{workflowId}/{node.nodeId}')
            if node.operatorId == 'vision.io.image_saver' and (workflowId, node.nodeId, ('outputPath',)) not in outputs:
                raise ValueError('文件输出必须先声明隔离输出目标；当前编辑入口不自动猜路径')


class LocalBackend:
    """Owns display services and debug Jobs; borrows the only device Runtime."""
    def __init__(self, runtime):
        from emo_master.apps.runtime.grpc_server.service import RuntimeService
        if not isinstance(runtime, RuntimeService):
            raise ValueError('当前连接不支持图片测试，请使用本机内嵌运行模式；已有运行结果仍可在页面预览中查看')
        self.runtime = runtime
        self.root = runtime.workspaceRoot.parent / 'presentation'
        self.service = self.server = self.channel = None
        self.jobId = self.preparedId = None
        try:
            from emo_master.apps.runtime.presentation.service import PresentationService
            from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
            with runtime._previewJobLock:
                self.service = getattr(runtime, '_presentationOwner', None)
                if self.service is None:
                    self.service = PresentationService(runtime, self.root)
                self.root = self.service.root
            self.server = AioRuntimeServer(runtime, self.service)
            self.address = f'127.0.0.1:{self.server.port}'
            self.channel = grpc.insecure_channel(self.address)
            self.stub = rpc.DisplayServiceStub(self.channel)
        except BaseException:
            self.close()
            raise

    def start(self, document, root):
        checkLocalDraft(document)
        modules = {'vision.io.image_loader': ('image_loader', '1.1.0'),
                   'vision.analysis.blob': ('blob_analysis', '1.2.0'),
                   'vision.collection.count': ('collection_count', '1.1.0'),
                   'vision.io.image_saver': ('image_saver', '1.0.0')}
        for workflow in document.workflows.values():
            for node in workflow.nodes:
                if node.kind == 'operator':
                    descriptor = self.runtime.pluginScanResult.activeOperators.get(node.operatorId)
                    module, version = modules[node.operatorId]
                    if (descriptor is None or descriptor.manifest.version != version or
                            descriptor.operatorClass.__module__ != 'emo_master.plugins.builtins.' + module + '.operator'):
                        raise ValueError('本地调试要求已验证的内置算子版本：' + node.operatorId)
        prepared = self.stub.Prepare(pb.DisplayPrepareRequest(project_json=document.model_dump_json(),
            resource_root=str(root)), timeout=20)
        self.preparedId = prepared.prepared_id
        # Use the same typed API as external clients. No implicit Start on observe.
        self.jobId = self.stub.Start(pb.DisplayStartRequest(prepared_id=self.preparedId), timeout=20).job_id
        return self.jobId

    def close(self):
        if self.jobId is None and self.preparedId and self.service:
            # A lost Start acknowledgement can still have created our one Job.
            # Reconcile the exact preparation under the same service lock before
            # cleanup; never issue another Start to find out.
            with self.service.lock:
                owned = [key for key, config in self.service.jobs.items()
                         if config.get('preparedId') == self.preparedId]
            if len(owned) > 1:
                raise ValueError('自有调试任务关联不唯一；保留资源，需明确核实')
            if owned:
                self.jobId = owned[0]
        if self.jobId:
            job = self.runtime.jobRepository.get(self.jobId)
            if job and not job.isTerminal:
                self.runtime.StopJob(pb.StopJobRequest(job_id=self.jobId, mode='force'), None)
            deadline = time.monotonic() + 5
            while self.runtime.jobRepository.get(self.jobId) and not self.runtime.jobRepository.get(self.jobId).isTerminal:
                if time.monotonic() > deadline:
                    raise TimeoutError('自有调试 Job 尚未停止；保留资源，不伪报清理')
                time.sleep(.02)
            # Terminal state alone does not prove worker/IPC/export ownership
            # has ended. Retry only safe release, never a second Start.
            while True:
                try:
                    self.service.release(self.jobId)
                    self.jobId = None
                    break
                except ValueError:
                    if time.monotonic() > deadline:
                        raise TimeoutError('自有调试资源尚未释放；保留所有者以便明确重试')
                    time.sleep(.02)
        if self.preparedId and self.service:
            self.service.discardPrepared(self.preparedId)
            self.preparedId = None
        if self.channel:
            self.channel.close()
        if self.server:
            self.server.close()
            self.server = None
        # The Runtime owns this shared service. A debug observer must not tear
        # it down while a normal Job or another observer still uses it.
        self.service = None


class PreviewController(QObject):
    """One bounded operation and one session; Qt consumes completions by polling."""
    def __init__(self, coordinator):
        super().__init__(coordinator.window)
        self.coordinator = coordinator
        self.session = self.hub = self.backend = None
        self.busy = False
        self.worker = None
        self.events = queue.Queue(maxsize=1)
        self.closing = False
        self.onClosed = None
        self.closeExecution = False
        self.error = None
        self.coverage = None
        self.selectedJob = None
        self.observationLabel = ''
        self.coverageDetails = ''
        self.observer = None
        self.observerToken = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)

    def message(self, text):
        from .property_panel import editorMessage
        self.coordinator.window.appendRuntimeLog('INFO', text)
        text = editorMessage(text)
        for original, readable in [
                ('当前 Runtime 尚无页面服务；请先明确运行工程', '尚未运行：请切换到流程设计，运行流程或开始图片测试'),
                ('当前 Runtime 不支持工程任务/来源元数据协商；页面设计仍可使用，请升级 Runtime 或使用高级只读连接',
                 '当前运行服务不支持查看项目结果，请更新运行服务；也可通过“高级连接”查看已有结果'),
                ('当前 Runtime 尚未提供只读任务目录；页面可继续编辑，不会为观看启动 Runtime',
                 '当前连接无法查询运行结果；请检查运行服务，或通过“高级连接”查看已有结果')]:
            text = text.replace(original, readable)
        if self.coordinator.editor:
            self.coordinator.editor.message.setText(text)
        self.coordinator.window.statusBar().showMessage(text)
        if self.observer is not None and isValid(self.observer):
            self.observer.status.setText(text)
        if hasattr(self.coordinator, 'chrome'):
            self.coordinator.chrome.update()

    def launch(self, operation, *, cleanup=False):
        if self.busy:
            raise ValueError('上一操作正在收尾，请稍候')
        self.busy = True
        self.cleaning = cleanup
        self.error = None
        def work():
            try:
                result = operation()
                self.events.put(('ready', result))
            except BaseException as error:
                self.events.put(('error', str(error)))
        self.worker = threading.Thread(target=work, name='designer-page-operation')
        self.worker.start()

    def startDebug(self):
        if self.busy or self.backend or self.closing:
            raise ValueError('请先停止当前图片测试，再开始新的测试')
        self.coordinator.sync()
        document = self.coordinator.session.document()
        checkLocalDraft(document)
        root = self.coordinator.directory
        if root is None:
            raise ValueError('请先保存项目并登记本地输入资源')
        runtime = getattr(self.coordinator.window.runtimeClient, 'runtimeService', None)
        def start():
            self.backend = LocalBackend(runtime)
            job = self.backend.start(document, root)
            return {'kind': 'debug_started', 'job': job}
        self.message('正在使用当前配置和测试图片开始测试')
        self.launch(start)

    def connect(self, address, job):
        if self.busy or self.session or self.closing:
            raise ValueError('请先停止查看当前结果')
        host, separator, port = address.rpartition(':')
        if (host not in {'127.0.0.1', 'localhost'} or not separator or not port.isdecimal()
                or not 0 < int(port) < 65536 or not job.strip()):
            raise ValueError('仅接受本机 loopback 地址和明确 Job ID')
        job = job.strip()
        if self.observer is None:
            self.openObserver()
        projectId = self.coordinator.session.document().project.projectId
        def attach():
            # Negotiate using the supplied endpoint only. Viewing never creates a
            # local Runtime/service, prepares a snapshot, or starts a Job.
            with grpc.insecure_channel(address) as channel:
                stub = rpc.DisplayServiceStub(channel)
                capabilities = stub.Capabilities(pb.DisplayEmpty(), timeout=5)
                self._checkCapabilities(capabilities)
                modern = {'project_jobs', 'source_coverage'} <= set(capabilities.capabilities)
                jobs = stub.ListJobs(pb.DisplayEmpty(project_id=projectId if modern else ''), timeout=5).jobs
                metadata = next((item for item in jobs if item.job_id == job), None)
                if metadata is None:
                    raise ValueError('指定任务不存在或展示结果已释放')
                if modern and metadata.project_id != projectId:
                    raise ValueError('指定任务不属于当前工程')
                coverage = self._coverage(metadata, capabilities) if modern else None
            return self._attach(address, metadata, coverage)
        self.launch(attach)

    @staticmethod
    def _checkCapabilities(capabilities, *, currentProject=False):
        required = {'snapshot', 'subscribe'}
        if currentProject:
            required.update({'project_jobs', 'source_coverage'})
        if capabilities.protocol_version != '1.0' or not required <= set(capabilities.capabilities):
            raise ValueError('当前 Runtime 不支持工程任务/来源元数据协商；页面设计仍可使用，请升级 Runtime 或使用高级只读连接')

    @staticmethod
    def _coverage(metadata, capabilities):
        if metadata.runtime_instance_id != capabilities.runtime_instance_id:
            raise ValueError('Runtime 代际已变化，请重新选择任务')
        return CaptureCoverage.fromMetadata(metadata)

    def watchCurrent(self):
        """Offer only verified current-project Jobs, with the explicit run selected."""
        if self.busy or self.closing:
            raise ValueError('上一操作正在收尾，请稍候')
        if self.session is not None:
            self.stopViewing(self.watchCurrent)
            return
        if self.observer is None:
            self.openObserver()
        self.coordinator.sync()
        observer = self.observer
        if observer is not None:
            observer.setDesignExamples(False)
            observer.setSimulationState(None)
            observer.setSource(True)
            observer.banner.setText('运行结果 · 等待选择运行记录')
        projectId = self.coordinator.session.document().project.projectId
        client = self.coordinator.window.runtimeClient
        if self.backend is None and not all(callable(getattr(client, name, None)) for name in
                   ('displayAddress', 'getDisplayCapabilities', 'listDisplayJobs')):
            raise ValueError('当前 Runtime 尚未提供只读任务目录；页面可继续编辑，不会为观看启动 Runtime')
        def listJobs():
            backend = self.backend
            if backend is not None:
                capabilities = backend.stub.Capabilities(pb.DisplayEmpty(), timeout=5)
                address = backend.address
                jobs = list(backend.stub.ListJobs(pb.DisplayEmpty(project_id=projectId), timeout=5).jobs)
            else:
                capabilities = client.getDisplayCapabilities()
                self._checkCapabilities(capabilities, currentProject=True)
                address = client.displayAddress()
                jobs = client.listDisplayJobs(projectId)
            self._checkCapabilities(capabilities, currentProject=True)
            if not address:
                raise ValueError('当前 Runtime 尚无展示端点；请在下一次明确开始运行时启用采集')
            jobs = [item for item in jobs if item.project_id == projectId]
            return {'kind': 'choices', 'jobs': jobs, 'address': address,
                    'projectId': projectId, 'capabilities': capabilities}
        self.message('正在查找已有运行结果，不会启动流程')
        self.launch(listJobs)

    def _chooseJob(self, value):
        if value['projectId'] != self.coordinator.session.document().project.projectId:
            raise ValueError('工程已切换，忽略旧工程任务列表')
        jobs = value['jobs']
        if not jobs:
            raise ValueError('尚未运行：请切换到流程设计并运行或进行图片测试')
        current = self.coordinator.window.currentJobId
        # Preserve an explicitly selected page observer independently of the
        # controller's owned Job; never assign currentJobId and inherit Stop rights.
        preferred = (self.backend.jobId if self.backend else current) or getattr(self.selectedJob, 'job_id', '')
        index = next((i for i, item in enumerate(jobs) if item.job_id == preferred), 0)
        if len(jobs) > 1:
            labels = [self._jobLabel(item) for item in jobs]
            label, ok = QInputDialog.getItem(self.coordinator.window, '查看运行结果',
                '选择已有运行记录', labels, index, False)
            if not ok:
                self.message('已取消选择任务；未启动或停止任何任务')
                return
            index = labels.index(label)
        metadata = jobs[index]
        coverage = self._coverage(metadata, value['capabilities'])
        self.launch(lambda: self._attach(value['address'], metadata, coverage))

    @staticmethod
    def _jobLabel(metadata, *, includeStatus=True):
        mode = {'runtime': '正常运行', 'debug': '图片测试', 'release': '测试发布'}.get(
            getattr(metadata, 'mode', ''), '旧任务（语义未声明）')
        capture = '已采集页面来源' if getattr(metadata, 'capture_enabled', False) else '未采集页面来源'
        status = ' · ' + STATUS_LABELS.get(metadata.status, metadata.status) if includeStatus else ''
        return f'{mode}{status} · {capture} · {metadata.job_id[:8]}'

    def _attach(self, address, metadata, coverage):
        if coverage is None:
            self.session = DisplaySession(address, metadata.job_id, imageDemand=True)
        else:
            self.session = DisplaySession(address, metadata.job_id, imageDemand=True,
                expectedRuntimeInstanceId=metadata.runtime_instance_id, projectId=metadata.project_id,
                readResults=bool(metadata.capture_enabled and not metadata.resources_released))
        return {'kind': 'attached', 'metadata': metadata, 'coverage': coverage}

    def _observerDestroyed(self, token):
        # No native Qt calls here: destruction can follow the Designer owner.
        if self.observerToken is token:
            self.observer = self.observerToken = None
            QTimer.singleShot(0, self.stopViewing)

    def retireObserver(self):
        observer = self.observer
        self.observer = self.observerToken = None
        if observer is not None and isValid(observer):
            observer.retiring = True
            observer.close()
            observer.deleteLater()

    def previewClosed(self, observer):
        if self.observer is observer:
            self.observer = self.observerToken = None
            self.stopViewing()

    def openObserver(self):
        if self.busy or self.closing:
            raise ValueError('上一操作正在收尾，请稍候')
        self.coordinator.sync()
        presentation = self.coordinator.session.presentation.snapshot()
        if not presentation.pages:
            raise ValueError('请先新建页面')
        observer = self.observer
        if observer is None or not isValid(observer):
            from .preview_window import PreviewWindow
            observer = PreviewWindow(self, presentation)
            token = object()
            self.observer, self.observerToken = observer, token
            observer.destroyed.connect(lambda _object=None: self._observerDestroyed(token))
            editor = self.coordinator.editor
            if editor and editor.pageId:
                observer.navigate(editor.pageId)
        else:
            self.refreshObserver(presentation)
        observer.showNormal() if observer.isMinimized() else observer.show()
        observer.raise_()
        observer.activateWindow()
        return observer

    def refreshObserver(self, presentation):
        observer = self.observer
        if observer is None or not isValid(observer) or observer.detached:
            return
        if observer.frozen is not None:
            # Undo may return to the displayed configuration while a result is
            # pinned. Never apply an obsolete intermediate edit on resume.
            observer.pendingPresentation = (presentation.model_copy(deep=True)
                                            if observer.config != presentation else None)
        elif observer.config != presentation:
            observer.reload(presentation)

    def refreshJobStatus(self):
        from emo_master.ui.presentation.job_status import jobStatusText
        backend = self.backend
        runtime = getattr(backend, 'runtime', None)
        job = runtime.jobRepository.getCurrentSnapshot(backend.jobId) if runtime and backend.jobId else None
        if hasattr(self.coordinator, 'chrome'):
            status = self.coordinator.chrome.testStatus
            if backend is not None:
                label = STATUS_LABELS.get(job.status, job.status) if job else '正在准备'
                status.setText('图片测试：' + label + (' · ' + job.message if job and job.message else ''))
            status.setVisible(backend is not None and not self.coordinator.pageActive())
        observer = self.observer
        if observer is None or not isValid(observer):
            return
        text = self.observationLabel
        if self.session is not None and not self.closing:
            text += '\n' + jobStatusText(self.session.readSnapshot().job)
        if self.coverageDetails:
            text += '\n' + self.coverageDetails
        observer.jobStatus.setText(text)

    def refreshCoverage(self):
        observer = self.observer
        if (observer is not None and isValid(observer) and not observer.detached
                and observer.hub is self.hub):
            observer.setCaptureCoverage(self.coverage)
        self.coverageDetails = ''
        if self.selectedJob is None:
            self.refreshJobStatus()
            return
        document = self.coordinator.session.document()
        presentation = document.presentation
        missing = []
        if self.coverage and presentation:
            used = {key for page in presentation.pages.values() for component in walkComponents(page.components)
                    for key in component.bindings.values()}
            for key in sorted(used):
                reason = self.coverage.sourceProblem(presentation, key)
                if reason:
                    source = presentation.dataSources.get(key)
                    workflow = document.workflows.get(source.workflowId) if source else None
                    node = next((node for node in workflow.nodes if node.nodeId == source.nodeId), None) if workflow else None
                    label = f'{workflow.name if workflow else ""}/{node.displayName or node.nodeId if node else key}.{source.port if source else ""}'
                    missing.append(label + '：' + reason)
        self.coverageDetails = '\n'.join(missing)
        self.refreshJobStatus()

    def poll(self):
        self.refreshJobStatus()
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            return
        self.worker.join()
        self.busy = False
        if kind == 'error':
            self.error = value
            self.message('操作失败：' + value)
            if self.closing and not self.cleaning:
                self._finishClose()
                return
            # Failed cleanup retains ownership and permits an explicit retry.
            self.closing = False
            self.onClosed = None
            return
        if value['kind'] == 'closed':
            if self.closeExecution and not value['execution']:
                self._finishClose()
                return
            self.closing = False
            self.error = None
            self.message('已停止查看' if not self.closeExecution else '图片测试和结果查看已收尾')
            self.closeExecution = False
            callback, self.onClosed = self.onClosed, None
            if callback:
                callback()
            return
        if self.closing:
            self._finishClose()
            return
        if value['kind'] == 'debug_started':
            self.message('图片测试已启动；可在页面预览中查看运行结果')
            return
        if value['kind'] == 'choices':
            try:
                self._chooseJob(value)
            except ValueError as error:
                self.error = str(error)
                self.message('无法查看：' + str(error))
            return
        self.selectedJob = value['metadata']
        self.coverage = value['coverage']
        self.observationLabel = self._jobLabel(self.selectedJob, includeStatus=False)
        self.hub = DisplayHub(self.session, self) if self.session is not None else None
        observer = self.observer
        if observer is not None and isValid(observer):
            observer.setDesignExamples(False)
            observer.setSource(True)
            observer.hub = self.hub
            observer.setCaptureCoverage(self.coverage)
            if self.hub:
                try:
                    self.hub.attach(observer)
                except BaseException as error:
                    self.hub.detach(observer)
                    observer.hub = None
                    self.retireObserver()
                    self.error = str(error)
                    self.message('无法查看：' + str(error))
                    return
            observer.banner.setText('运行结果')
            self.refreshCoverage()
        self.message('已连接运行结果')

    def stopViewing(self, callback=None):
        self._requestClose(False, callback)

    def stopTest(self):
        if self.backend is None:
            return
        self._requestClose(True, None)

    def closeAsync(self, callback=None):
        # Project/application lifecycle is the only combined cleanup path.
        self.retireObserver()
        self._requestClose(True, callback)

    def _requestClose(self, execution, callback):
        # A newer view intent cancels an older sample/reconnect callback; closing
        # execution has priority over subsequent window destruction callbacks.
        if execution or not self.closeExecution:
            self.onClosed = callback
        self.closeExecution = self.closeExecution or execution
        self.closing = True
        if not self.busy:
            self._finishClose()

    def _finishClose(self):
        if self.hub:
            for renderer in tuple(self.hub.windows):
                self.hub.detach(renderer)
                if isValid(renderer):
                    renderer.hub = None
            self.hub.deleteLater()
            self.hub = None
        self.coverage = None
        self.selectedJob = None
        self.observationLabel = ''
        observer = self.observer
        if observer is not None and isValid(observer):
            observer.hub = None
            observer.setCaptureCoverage(None)
            observer.reload(observer.config)
            observer.setDesignExamples(False)
            observer.setSimulationState(None)
            observer.banner.setText('运行结果 · 已停止查看')
        execution = self.closeExecution
        def close():
            if self.session:
                self.session.close()
                self.session = None
            if execution and self.backend:
                self.backend.close()
                self.backend = None
            return {'kind': 'closed', 'execution': execution}
        self.message('正在结束图片测试' if self.closeExecution else '正在停止查看，流程继续运行')
        self.launch(close, cleanup=True)

    def active(self):
        return self.busy or self.session is not None or self.backend is not None
