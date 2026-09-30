"""Read-only current-project viewing and separately owned isolated draft debugging."""
import queue
import threading
import time

import grpc
from PySide2.QtCore import QObject, QTimer
from PySide2.QtWidgets import QInputDialog

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.core.presentation.models import walkComponents
from emo_master.core.presentation.coverage import CaptureCoverage


LOCAL_OPERATORS = frozenset({'vision.io.image_loader', 'vision.analysis.blob',
    'vision.collection.count', 'vision.io.image_saver'})


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
        raise ValueError('P2 每 Job 单图像槽：请跨页复用同一图像来源')
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
            raise ValueError('当前 Runtime 不支持本地草稿准备；请使用内嵌开发入口或只读连接已有 Job')
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
        self.error = None
        self.coverage = None
        self.selectedJob = None
        self.observationLabel = ''
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(25)

    def message(self, text):
        if self.coordinator.editor:
            self.coordinator.editor.message.setText(text)
        self.coordinator.window.appendRuntimeLog('INFO', text)

    def launch(self, operation):
        if self.busy:
            raise ValueError('上一操作正在收尾，请稍候')
        self.busy = True
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
        if self.session or self.backend or self.closing:
            raise ValueError('请先明确停止本次本地调试 / 断开观察，再启动新草稿')
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
            self.session = DisplaySession(self.backend.address, job)
            return job
        self.message('正在冻结草稿、物化临时输入/数据库/输出；不会热修改当前采集计划')
        self.launch(start)

    def connect(self, address, job):
        if self.session or self.backend or self.closing:
            raise ValueError('先断开当前观察会话')
        host, separator, port = address.rpartition(':')
        if (host not in {'127.0.0.1', 'localhost'} or not separator or not port.isdecimal()
                or not 0 < int(port) < 65536 or not job.strip()):
            raise ValueError('仅接受本机 loopback 地址和明确 Job ID')
        job = job.strip()
        projectId = self.coordinator.session.document().project.projectId
        def attach():
            # Negotiate using the supplied endpoint only. Viewing never creates a
            # local Runtime/service, prepares a snapshot, or starts a Job.
            with grpc.insecure_channel(address) as channel:
                stub = rpc.DisplayServiceStub(channel)
                capabilities = stub.Capabilities(pb.DisplayEmpty(), timeout=5)
                self._checkCapabilities(capabilities)
                jobs = stub.ListJobs(pb.DisplayEmpty(), timeout=5).jobs
                metadata = next((item for item in jobs if item.job_id == job), None)
                if metadata is None:
                    raise ValueError('指定任务不存在或展示结果已释放')
                modern = {'project_jobs', 'source_coverage'} <= set(capabilities.capabilities)
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
        if self.backend is not None:
            raise ValueError('请先明确停止自有隔离调试，再观看正常运行任务')
        if self.session is not None:
            self.closeAsync(self.watchCurrent)
            return
        self.coordinator.sync()
        projectId = self.coordinator.session.document().project.projectId
        client = self.coordinator.window.runtimeClient
        if not all(callable(getattr(client, name, None)) for name in
                   ('displayAddress', 'getDisplayCapabilities', 'listDisplayJobs')):
            raise ValueError('当前 Runtime 尚未提供只读任务目录；页面可继续编辑，不会为观看启动 Runtime')
        def listJobs():
            capabilities = client.getDisplayCapabilities()
            self._checkCapabilities(capabilities, currentProject=True)
            address = client.displayAddress()
            if not address:
                raise ValueError('当前 Runtime 尚无展示端点；请在下一次明确开始运行时启用采集')
            jobs = [item for item in client.listDisplayJobs(projectId)
                    if item.project_id == projectId]
            return {'kind': 'choices', 'jobs': jobs, 'address': address,
                    'projectId': projectId, 'capabilities': capabilities}
        self.message('正在读取当前工程任务；只观察，不启动、不更改采集计划')
        self.launch(listJobs)

    def _chooseJob(self, value):
        if value['projectId'] != self.coordinator.session.document().project.projectId:
            raise ValueError('工程已切换，忽略旧工程任务列表')
        jobs = value['jobs']
        if not jobs:
            raise ValueError('当前工程没有可观看任务；请从主工具栏明确开始运行')
        current = self.coordinator.window.currentJobId
        # Preserve an explicitly selected page observer independently of the
        # controller's owned Job; never assign currentJobId and inherit Stop rights.
        preferred = current or getattr(self.selectedJob, 'job_id', '')
        index = next((i for i, item in enumerate(jobs) if item.job_id == preferred), 0)
        if len(jobs) > 1:
            labels = [self._jobLabel(item) for item in jobs]
            label, ok = QInputDialog.getItem(self.coordinator.window, '观看当前工程任务',
                '选择已有任务（仅观察；执行状态不等于产品 OK/NG）', labels, index, False)
            if not ok:
                self.message('已取消选择任务；未启动或停止任何任务')
                return
            index = labels.index(label)
        metadata = jobs[index]
        coverage = self._coverage(metadata, value['capabilities'])
        self.launch(lambda: self._attach(value['address'], metadata, coverage))

    @staticmethod
    def _jobLabel(metadata):
        mode = {'runtime': '正常运行', 'debug': '隔离调试', 'release': '测试发布'}.get(
            getattr(metadata, 'mode', ''), '旧任务（语义未声明）')
        capture = '已采集页面来源' if getattr(metadata, 'capture_enabled', False) else '未采集页面来源'
        return f'{metadata.job_id} · {metadata.status} · {mode} · {capture}'

    def _attach(self, address, metadata, coverage):
        if coverage is None or (coverage.enabled and not getattr(metadata, 'resources_released', False)):
            self.session = DisplaySession(address, metadata.job_id)
        return {'kind': 'attached', 'metadata': metadata, 'coverage': coverage}

    def refreshCoverage(self):
        editor = self.coordinator.editor
        if editor is None:
            return
        editor.renderer.setCaptureCoverage(self.coverage)
        if self.selectedJob is None:
            editor.observation.setText(self.observationLabel)
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
        text = self.observationLabel
        if missing:
            text += '\n' + '\n'.join(missing)
        editor.observation.setText(text)

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            return
        self.worker.join()
        self.busy = False
        if kind == 'error':
            self.error = value
            self.message('操作失败：' + value)
            # Keep owners reachable and allow an explicit cleanup retry.
            self.closing = False
            return
        if value == '__closed__':
            self.closing = False
            self.error = None
            self.message('本观察者已断开；外部 Runtime 未停止')
            callback, self.onClosed = self.onClosed, None
            if callback:
                callback()
            return
        if self.closing:
            self.closeAsync(self.onClosed)
            return
        if isinstance(value, dict) and value['kind'] == 'choices':
            try:
                self._chooseJob(value)
            except ValueError as error:
                self.error = str(error)
                self.message('无法观看：' + str(error))
            return
        if isinstance(value, dict):
            self.selectedJob = value['metadata']
            self.coverage = value['coverage']
            label = self._jobLabel(self.selectedJob)
            self.observationLabel = '只读观看 · 选择时状态：' + label + ' · 执行状态不等于产品 OK/NG'
            if self.coverage is None:
                self.observationLabel += '\n旧 Runtime 未提供来源清单；仅允许完整采集摘要匹配的结果'
            elif getattr(self.selectedJob, 'resources_released', False):
                self.observationLabel += '\n任务未保留页面资源（未采集或已释放）；不会重跑任务补取数据'
        else:
            self.selectedJob = None
            self.coverage = None
            label = value
            self.observationLabel = '隔离草稿调试 · ' + value + ' · 临时数据库/输出，与正常运行分开'
        self.hub = DisplayHub(self.session, self) if self.session is not None else None
        if self.coordinator.editor:
            renderer = self.coordinator.editor.renderer
            renderer.hub = self.hub
            renderer.setCaptureCoverage(self.coverage)
            if self.hub:
                self.hub.attach(renderer)
            else:
                from types import MappingProxyType
                from emo_master.clients.runtime.view_state import SessionView
                empty = MappingProxyType({})
                renderer.submit(SessionView(0, 0, self.coverage.runtimeInstanceId, self.coverage.jobId,
                    'NOT_CAPTURED', '任务未采集页面来源或资源已释放；下一次明确启动才生效', empty, empty, empty))
            self.coordinator.editor.tools.preview.setChecked(True)
            renderer.banner.setText(self.observationLabel)
            self.refreshCoverage()
        self.message('已选择明确任务 ' + label)

    def closeAsync(self, callback=None):
        self.closing = True
        self.onClosed = callback
        if self.busy:
            return
        if self.hub:
            for renderer in tuple(self.hub.windows):
                self.hub.detach(renderer)
                renderer.hub = None
                renderer.setCaptureCoverage(None)
                renderer.reload(renderer.config)
                renderer.banner.setText('模拟布局预览 · 已断开实时结果')
            self.hub.deleteLater()
            self.hub = None
        self.coverage = None
        self.selectedJob = None
        self.observationLabel = ''
        self.refreshCoverage()
        if self.coordinator.editor:
            renderer = self.coordinator.editor.renderer
            renderer.reload(renderer.config)
            renderer.banner.setText('模拟布局预览 · 已断开实时结果')
        def close():
            if self.session:
                self.session.close()
                self.session = None
            if self.backend:
                self.backend.close()
                self.backend = None
            return '__closed__'
        self.message('正在异步收尾本观察者及本次自有调试；界面仍可响应')
        self.launch(close)

    def active(self):
        return self.busy or self.session is not None or self.backend is not None
