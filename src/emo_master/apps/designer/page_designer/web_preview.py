"""Optional Dot renderer process; its lifetime never owns a Runtime or Job."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qs, urlsplit

from PySide2.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide2.QtGui import QDesktopServices

from emo_master.core.project.web_panel import buildWebPanel, readWebPanel
from emo_panel_protocol.connection import createChannel
from emo_panel_protocol.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_panel_protocol.preview import PreviewContext
from emo_panel_protocol.published import PanelManifest


def runtimePreviewContext(client, manifest, scope, service):
    """Negotiate against a frozen endpoint on a worker; never invoke control RPCs."""
    def checkConnection():
        if scope != client.runtimeScope or service is not client.runtimeService:
            raise ValueError('Runtime 连接已切换，请重新打开 Web 预览')
    checkConnection()
    profile = client.ensureReadOnlyEndpoint()
    checkConnection()
    with createChannel(profile) as channel:
        info = rpc.RuntimeServiceStub(channel).GetRuntimeInfo(pb.RuntimeInfoRequest(), timeout=profile.timeoutSeconds)
    checkConnection()
    if not info.ok or not info.project_id:
        raise ValueError(info.message or 'Runtime 尚未配置正式工程，请先明确写入配置，或使用示例预览')
    return PreviewContext.forRuntime(manifest, profile, info)


class WebPreviewProcess(QObject):
    ready = Signal(str)
    failed = Signal(str)
    closed = Signal()

    def __init__(self, parent=None, *, command=None, directory=None):
        super().__init__(parent)
        self.command = command
        self.directory = directory
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._output)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._fail('Web 预览启动超时，请检查预览工具安装'))
        self.killTimer = QTimer(self)
        self.killTimer.setSingleShot(True)
        self.killTimer.timeout.connect(self.process.kill)
        self.buffer = b''
        self.isReady = self.closing = self.finished = self.hasFailed = False
        self.pool = None
        self.pending = None
        self.prepareTimer = QTimer(self)
        self.prepareTimer.setInterval(25)
        self.prepareTimer.timeout.connect(self._prepared)

    def prepareRuntime(self, package, manifest, client):
        self.package = package
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='web-preview')
        self.pending = self.pool.submit(runtimePreviewContext, client, manifest, client.runtimeScope, client.runtimeService)
        self.prepareTimer.start()

    def _prepared(self):
        if self.pending is None or not self.pending.done():
            return
        self.prepareTimer.stop()
        pending, self.pending = self.pending, None
        if self.pool:
            self.pool.shutdown(wait=False)
            self.pool = None
        if self.closing:
            return
        try:
            context = pending.result()
        except Exception as error:
            self._fail('无法连接 Web 预览数据源：' + str(error))
            return
        self.start(self.package, context)

    def start(self, package, context: PreviewContext):
        if self.closing:
            return
        command = self.command
        if command is None:
            executable = shutil.which('emo-web-panel')
            if not executable:
                self._fail('未安装 Web 预览工具。请安装 Dot 交付的 emo-web-panel 后重新预览。')
                return
            command = [executable]
        if self.directory is None:
            self.directory = tempfile.TemporaryDirectory(prefix='emo-web-preview-')
        contextPath = Path(self.directory.name) / 'preview-context.json'
        contextPath.write_text(context.model_dump_json(indent=2, exclude_none=True), encoding='utf-8')
        self.process.start(command[0], [*command[1:], 'preview', '--package', str(Path(package).resolve()),
            '--context', str(contextPath), '--host', '127.0.0.1', '--port', '0', '--json', '--no-control'])
        self.timer.start(15000)

    def _stderr(self):
        # Drain bounded startup diagnostics without writing temporary access
        # credentials into persistent Designer logs.
        self.process.readAllStandardError()

    def _output(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        if self.closing:
            return
        if len(self.buffer) > 65536:
            self._fail('Web 预览启动信息超过限制')
            return
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if self.isReady or not line.strip():
                continue
            try:
                value = json.loads(line)
                address = urlsplit(value['url'])
                if (value.get('event') != 'ready' or value.get('protocolVersion') != '1'
                        or address.scheme != 'http' or address.hostname != '127.0.0.1'
                        or not address.port or address.username or address.password
                        or not parse_qs(address.query).get('token')):
                    raise ValueError('preview startup contract')
            except (ValueError, KeyError, TypeError):
                self._fail('Web 预览启动信息不符合 Panel Contract v1')
                return
            self.timer.stop()
            self.isReady = True
            self.ready.emit(value['url'])

    def _fail(self, message):
        if self.hasFailed or self.finished:
            return
        self.hasFailed = True
        self.timer.stop()
        self.failed.emit(message)
        self.close()

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self._fail('无法启动 Web 预览工具，请检查安装路径')
            self._finished(-1, QProcess.CrashExit)

    def close(self):
        self.closing = True
        self.timer.stop()
        self.prepareTimer.stop()
        if self.pending:
            self.pending.cancel()
            self.pending = None
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None
        if self.process.state() == QProcess.NotRunning:
            self._finished(0, QProcess.NormalExit)
        else:
            self.process.terminate()
            self.killTimer.start(3000)

    def _finished(self, exitCode, _status):
        if self.finished:
            return
        self.finished = True
        self.timer.stop()
        self.killTimer.stop()
        if not self.closing and not self.hasFailed:
            self.failed.emit('Web 预览已退出' if self.isReady else f'Web 预览启动失败（退出码 {exitCode}）')
        if self.directory:
            self.directory.cleanup()
            self.directory = None
        self.closed.emit()


def previewWeb(coordinator, *, online=False):
    coordinator.sync()
    if coordinator.directory is None:
        raise ValueError('请先保存工程，确定静态资源目录')
    directory = tempfile.TemporaryDirectory(prefix='emo-web-preview-')
    try:
        from .editing import manifestsFromCatalog
        package = buildWebPanel(coordinator.session.document(), coordinator.directory,
            Path(directory.name) / 'preview.emoweb',
            manifests=manifestsFromCatalog(coordinator.window.operatorCatalog) or None)
        manifest = PanelManifest.model_validate(readWebPanel(package)[0])
    except Exception:
        directory.cleanup()
        raise
    previous = getattr(coordinator, 'webPreview', None)
    if previous:
        previous.close()
    preview = WebPreviewProcess(coordinator.window, directory=directory)
    coordinator.webPreview = preview
    preview.ready.connect(lambda address: QDesktopServices.openUrl(QUrl(address)))
    preview.failed.connect(coordinator.preview.message)
    preview.closed.connect(preview.deleteLater)
    preview.closed.connect(lambda: setattr(coordinator, 'webPreview', None)
                           if getattr(coordinator, 'webPreview', None) is preview else None)
    if online:
        coordinator.preview.message('正在连接当前 Runtime，打开只读 Web 预览')
        preview.prepareRuntime(package, manifest, coordinator.window.runtimeClient)
    else:
        coordinator.preview.message('正在打开 Web 页面示例预览')
        preview.start(package, PreviewContext.forPackage(manifest))
    return preview
