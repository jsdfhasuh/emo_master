"""Explicit local draft execution on the existing Runtime, with async ownership."""
from pathlib import Path
import queue
import tempfile
import threading
import time

import grpc
from PySide2.QtCore import QObject, QTimer

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.ui.presentation.hub import DisplayHub
from emo_master.core.presentation.models import walkComponents


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
        self.temp = tempfile.TemporaryDirectory(prefix='emo-p4-debug-')
        self.root = Path(self.temp.name).resolve()
        self.service = self.server = self.channel = None
        self.jobId = self.preparedId = None
        try:
            from emo_master.apps.runtime.presentation.service import PresentationService
            from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
            self.service = PresentationService(runtime, self.root)
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
        if self.jobId:
            job = self.runtime.jobRepository.get(self.jobId)
            if job and not job.isTerminal:
                self.runtime.StopJob(pb.StopJobRequest(job_id=self.jobId, mode='force'), None)
            deadline = time.monotonic() + 5
            while self.runtime.jobRepository.get(self.jobId) and not self.runtime.jobRepository.get(self.jobId).isTerminal:
                if time.monotonic() > deadline:
                    raise TimeoutError('自有调试 Job 尚未停止；保留资源，不伪报清理')
                time.sleep(.02)
        if self.channel:
            self.channel.close()
        if self.server:
            self.server.close()
            self.server = None
        if self.service:
            self.service.close()
            self.service = None
        self.temp.cleanup()


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
        if not address.startswith(('127.0.0.1:', 'localhost:')) or not job.strip():
            raise ValueError('仅接受本机 loopback 地址和明确 Job ID')
        def attach():
            self.session = DisplaySession(address, job)
            return job
        self.launch(attach)

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
        self.hub = DisplayHub(self.session, self)
        if self.coordinator.editor:
            renderer = self.coordinator.editor.renderer
            renderer.hub = self.hub
            self.hub.attach(renderer)
            renderer.banner.setText('真实结果 · ' + value + ' · 新绑定下次明确启动调试生效')
            self.coordinator.editor.tools.preview.setChecked(True)
        self.message('已连接明确任务 ' + value)

    def closeAsync(self, callback=None):
        self.closing = True
        self.onClosed = callback
        if self.busy:
            return
        if self.hub:
            for renderer in tuple(self.hub.windows):
                self.hub.detach(renderer)
                renderer.hub = None
                renderer.reload(renderer.config)
                renderer.banner.setText('模拟布局预览 · 已断开实时结果')
            self.hub.deleteLater()
            self.hub = None
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
