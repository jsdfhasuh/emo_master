"""Read-only Runtime-wide diagnostics, independent of Run and connection ownership."""
import inspect
import queue
import threading

import grpc

from emo_master.apps.designer.services.display_calls import DisplayCallContext
from emo_master.apps.designer.page_designer.runtime_logs import eventPayload
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


class RuntimeGlobalLogs:
    def __init__(self, service, *, afterSequence=0):
        self.service = service
        self.cursor = afterSequence
        self.events = queue.Queue(maxsize=256)
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.stream = None
        self.context = DisplayCallContext()
        self.thread = threading.Thread(target=self._read, name="designer-global-logs", daemon=True)
        self.thread.start()

    def _publish(self, kind, event):
        while not self.stop.is_set():
            try:
                self.events.put((kind, event), timeout=.05)
                return True
            except queue.Full:
                continue
        return False

    def _notice(self, level, code, message, **payload):
        self._publish("local", dict(level=level, code=code, message=message,
                                    eventType="designer.runtime_logs.connection", payload=payload))

    def _read(self):
        failures = 0
        disconnected = False
        try:
            method = self.service.StreamJobEvents
            embedded = "context" in inspect.signature(method).parameters
            while not self.stop.is_set():
                stream = None
                try:
                    request = pb.StreamJobEventsRequest(job_id="__runtime__", after_sequence=self.cursor, follow=True)
                    stream = method(request, self.context) if embedded else method(request)
                    with self.lock:
                        self.stream = stream
                        if self.stop.is_set():
                            cancel = getattr(stream, "cancel", None)
                            if callable(cancel):
                                cancel()
                            return
                    for event in stream:
                        if self.stop.is_set():
                            return
                        if event.sequence <= self.cursor:
                            continue
                        if disconnected:
                            self._notice("INFO", "GLOBAL_LOG_RECOVERED", "Runtime 全局日志订阅已恢复")
                            disconnected = False
                        failures = 0
                        if not self._publish("event", eventPayload(event)):
                            return
                        self.cursor = event.sequence
                    if self.stop.is_set():
                        return
                    raise ConnectionError("Runtime 全局日志流已结束")
                except Exception as error:
                    if self.stop.is_set():
                        return
                    failures += 1
                    code = error.code().name if isinstance(error, grpc.RpcError) else type(error).__name__
                    permanent = isinstance(error, grpc.RpcError) and error.code() not in {
                        grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
                    if permanent or failures > 3:
                        self._notice("WARN", "GLOBAL_LOG_STOPPED",
                                     "Runtime 全局日志订阅已停止；重新打开日志窗口可重连",
                                     errorCode=code, reason=str(error))
                        return
                    if not disconnected:
                        self._notice("WARN", "GLOBAL_LOG_DISCONNECTED", "Runtime 全局日志订阅中断，正在重连",
                                     errorCode=code, reason=str(error))
                        disconnected = True
                    if self.stop.wait(.5):
                        return
                finally:
                    if stream is not None:
                        cancel = getattr(stream, "cancel", None)
                        if callable(cancel):
                            cancel()
                        close = getattr(stream, "close", None)
                        if callable(close):
                            close()
                    with self.lock:
                        self.stream = None
        finally:
            self.finished.set()

    def take(self):
        result = []
        for _ in range(256):
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                break
        return result

    def close(self):
        self.stop.set()
        self.context.cancel()
        with self.lock:
            cancel = getattr(self.stream, "cancel", None)
            if callable(cancel):
                cancel()
        self.thread.join(2)
        if self.thread.is_alive():
            raise TimeoutError("Runtime 全局日志订阅读取尚未结束")
