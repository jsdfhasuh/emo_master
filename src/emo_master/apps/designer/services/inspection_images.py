"""One latest-only image worker and a renewable, read-only inspection lease.

No Qt dependency, second result cache, observer or execution ownership. The UI
injects an owned-buffer decoder and receives a small notification; pixels stay
in one bounded mailbox instead of accumulating in a GUI event queue.
"""
from dataclasses import dataclass
import threading
import time

from .display_calls import DisplayCallContext
from .runtime_client import RuntimeClientError


@dataclass(frozen=True)
class ImageSelection:
    projectId: str
    jobId: str
    workflowId: str
    nodeId: str
    workflowRunId: str
    nodeRunId: str
    revision: int
    port: str


class InspectionImages:
    def __init__(self, client, decoder, notify):
        self.client, self.decoder, self.notify = client, decoder, notify
        self._condition = threading.Condition()
        self.generation = 0
        self._projectGeneration = 0
        self.projectId = ''
        self.sessionId = ''
        self.runtimeId = ''
        self.message = '明确运行后提供真实节点图片'
        self._renewAt = float('inf')
        self._pending = None
        self._selection = None
        self._disconnected = False
        self._mailbox = None
        self._active = None
        self._retiring = []
        self._closed = False
        self.reads = 0
        self.decodedBytes = 0
        self.peakDecodedBytes = 0
        self.peakScratchBytes = 0
        self._thread = None

    def ensureSession(self, projectId):
        """Called by the existing Start worker, never by a QWidget slot."""
        with self._condition:
            if self._closed:
                raise RuntimeClientError('E_INSPECTION_CLOSED', '检查会话已关闭')
            existing = self.sessionId if self.projectId == projectId else ''
            epoch = self._projectGeneration
        if not callable(getattr(self.client, 'inspectionSession', None)):
            self.message = 'E_INSPECTION_UNSUPPORTED: 旧 Runtime 客户端不支持两次运行图片检查会话'
            return ''
        if existing:
            try:
                reply = self.client.inspectionSession('renew', projectId, existing)
                if str(reply.runtime_instance_id) != self.runtimeId:
                    raise RuntimeClientError('E_INSPECTION_IDENTITY', 'Runtime 实例已变化；未启动任务')
                return existing
            except RuntimeClientError as error:
                if error.code != 'E_INSPECTION_SESSION_EXPIRED':
                    raise
        try:
            reply = self.client.inspectionSession('open', projectId)
        except Exception as error:
            if getattr(error, 'code', '') != 'E_INSPECTION_UNSUPPORTED':
                raise
            with self._condition:
                self.message = str(error)
            return ''  # Legacy execution remains available; history images do not.
        with self._condition:
            if self._closed or epoch != self._projectGeneration:
                self._retiring.append((projectId, str(reply.session_id)))
                self._condition.notify_all()
                raise RuntimeClientError('E_INSPECTION_CANCELLED', '工程或窗口已变化；未启动任务')
            self.projectId, self.sessionId = projectId, str(reply.session_id)
            self.runtimeId = str(reply.runtime_instance_id)
            self.message = ''
            self._renewAt = time.monotonic() + 10
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='designer-node-image', daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return self.sessionId

    def select(self, selection):
        with self._condition:
            self.generation += 1
            if self._active:
                self._active.cancel()
            self._mailbox = None
            self.decodedBytes = 0
            self._pending = (self.generation, selection) if selection else None
            self._selection = selection
            self._condition.notify_all()
            generation = self.generation
        if selection and not self.sessionId and self.message:
            self._publish(generation, {'message': self.message})
        return generation

    def take(self, generation):
        with self._condition:
            if generation != self.generation or self._mailbox is None:
                return None
            value, self._mailbox = self._mailbox, None
            self.decodedBytes = 0
            return value

    def reset(self):
        with self._condition:
            self._projectGeneration += 1
            self.select(None)
            if self.sessionId:
                self._retiring.append((self.projectId, self.sessionId))
            self.sessionId, self.projectId, self.runtimeId = '', '', ''
            self._renewAt = float('inf')
            self.message = '明确运行后提供真实节点图片'
            self._condition.notify_all()

    def close(self):
        with self._condition:
            self.reset()
            self._closed = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(11)
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError('读图或会话清理尚未退出，仍保留实际所有者')

    def _publish(self, generation, value):
        with self._condition:
            if self._closed or generation != self.generation:
                return
            self._mailbox = value
            self.decodedBytes = value.get('bytes', 0)
            self.peakDecodedBytes = max(self.peakDecodedBytes, self.decodedBytes)
        self.notify(generation)

    def _run(self):
        while True:
            with self._condition:
                while (not self._retiring and not self._pending and not self._closed
                       and time.monotonic() < self._renewAt):
                    self._condition.wait(min(1, max(0, self._renewAt - time.monotonic())))
                retiring, self._retiring = self._retiring, []
                pending, self._pending = self._pending, None
                sessionId, projectId = self.sessionId, self.projectId
                renew = bool(sessionId and time.monotonic() >= self._renewAt)
                closed = self._closed
            for project, session in retiring:
                try:
                    self.client.inspectionSession('close', project, session)
                except Exception:
                    pass  # Unreachable Runtime owns final cleanup at its 30s lease.
            if closed:
                return
            if renew:
                try:
                    reply = self.client.inspectionSession('renew', projectId, sessionId)
                    if str(reply.runtime_instance_id) != self.runtimeId:
                        raise RuntimeClientError('E_INSPECTION_IDENTITY', 'Runtime 实例已变化')
                except Exception as error:
                    generation = None
                    with self._condition:
                        if self.sessionId == sessionId:
                            self.message = '检查会话不可用：' + str(error)[:200]
                            self._disconnected = True
                            self._renewAt = time.monotonic() + 10
                            generation = self.generation
                    if generation is not None:
                        self._publish(generation, {'message': self.message})
                else:
                    with self._condition:
                        self._renewAt = time.monotonic() + 10
                        if self._disconnected and self.sessionId == sessionId:
                            self.message = ''
                            self._disconnected = False
                            if self._selection:
                                self._pending = (self.generation, self._selection)
            if pending:
                generation, selection = pending
                context = DisplayCallContext()
                with self._condition:
                    if generation != self.generation:
                        continue
                    self._active = context
                try:
                    self._publish(generation, self._read(selection, context))
                except Exception as error:
                    self._publish(generation, {'message': str(error)[:300]})
                finally:
                    with self._condition:
                        self._active = None

    def _read(self, selection, context):
        with self._condition:
            sessionId = self.sessionId
            if not sessionId or self.projectId != selection.projectId:
                return {'message': self.message or 'E_INSPECTION_EXPIRED: 检查会话不可用'}
        context.start(5000)
        while True:
            listing = self.client.listNodePreviewSourcesWithMetadata(selection.projectId, selection.workflowId,
                selection.nodeId, jobId=selection.jobId, inspectionSessionId=sessionId, cancellation=context)
            context.check()
            if listing.captureState != 'PREPARING':
                break
            with self._condition:
                self._condition.wait(.02)
            context.check()
        source = next((source for source in listing.sources if source.port == selection.port), None)
        if source is None:
            return {'message': listing.message or 'E_INSPECTION_MISSING: 未采集此端口'}
        if (source.originJobId != selection.jobId or source.originProjectRevision != selection.revision
                or source.workflowRunId != selection.workflowRunId or source.nodeRunId != selection.nodeRunId
                or not source.captureId or source.workflowId != selection.workflowId or source.nodeId != selection.nodeId):
            raise RuntimeClientError('E_INSPECTION_IDENTITY', '快照与所选节点最后一次执行身份不匹配')
        with self._condition:
            self.reads += 1
        payload, mime = self.client.readInspectionAsset(selection.projectId, sessionId, source.sourceId, context)
        context.check()
        self.peakScratchBytes = max(self.peakScratchBytes, 2 * len(payload))
        image, size, formatName = self.decoder(payload, source.width, source.height)
        self.peakScratchBytes = max(self.peakScratchBytes, size)
        del payload
        context.check()
        return {'image': image, 'bytes': size, 'format': formatName, 'mime': mime,
                'source': source, 'selection': selection}
