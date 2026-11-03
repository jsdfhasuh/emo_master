"""A single Runtime query source for live desktop diagnostics."""
import inspect
import queue
import threading

import grpc

from emo_master.apps.designer.services.display_calls import DisplayCallContext
from emo_master.apps.designer.page_designer.runtime_logs import eventPayload
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


class RuntimeGlobalLogs:
    def __init__(self, service, *, afterEventId=None, runtimeInstanceId="", filters=None):
        self.service, self.cursor = service, afterEventId
        self.runtimeInstanceId, self.filters = runtimeInstanceId, dict(filters or {})
        self.supported = None
        self.events = queue.Queue(maxsize=256)
        self.stop, self.finished = threading.Event(), threading.Event()
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
            eventType="designer.runtime_logs.connection", component="runtime_client", payload=payload))

    def _query(self, request):
        method = getattr(self.service, "QueryRuntimeLogs", None)
        if not callable(method):
            return pb.QueryRuntimeLogsReply(ok=False, code="UNIMPLEMENTED", message="此 Runtime 不支持历史日志查询")
        future = getattr(method, "future", None)
        if callable(future):
            call = future(request, timeout=5, wait_for_ready=False)
            with self.lock:
                self.stream = call
                if self.stop.is_set():
                    call.cancel()
            try:
                return call.result()
            finally:
                with self.lock:
                    self.stream = None
        return method(request, self.context) if "context" in inspect.signature(method).parameters else method(request)

    def _read(self):
        failures, disconnected, upper = 0, False, None
        try:
            while not self.stop.is_set():
                try:
                    initial = self.cursor is None
                    request = pb.QueryRuntimeLogsRequest(runtime_instance_id=self.runtimeInstanceId,
                        after_event_id=0 if initial else self.cursor, limit=1 if initial else 200,
                        **({} if initial else self.filters))
                    if upper is not None and not initial:
                        request.upper_event_id = upper
                    reply = self._query(request)
                    if self.stop.is_set():
                        return
                    if not reply.ok:
                        if reply.code == "RESET_REQUIRED":
                            self.runtimeInstanceId, self.cursor, upper = reply.runtime_instance_id, None, None
                            self._publish("reset", dict(runtimeInstanceId=self.runtimeInstanceId))
                            continue
                        if reply.code == "UNIMPLEMENTED":
                            self.supported = False
                        self._notice("WARN", reply.code, reply.message or "Runtime 日志查询失败")
                        return
                    self.runtimeInstanceId = reply.runtime_instance_id
                    self.supported = True
                    if disconnected:
                        self._notice("INFO", "GLOBAL_LOG_RECOVERED", "Runtime 日志查询已恢复")
                    failures, disconnected = 0, False
                    if initial:
                        self.cursor = reply.upper_event_id
                    else:
                        for row in reply.events:
                            if not self._publish("event", dict(eventPayload(row.event), eventId=row.event_id)):
                                return
                        self.cursor = reply.next_event_id
                    upper = reply.upper_event_id if not initial and reply.has_more else None
                    if upper is None and self.stop.wait(.5):
                        return
                except Exception as error:
                    if self.stop.is_set():
                        return
                    failures += 1
                    code = error.code().name if isinstance(error, grpc.RpcError) else type(error).__name__
                    if code == "UNIMPLEMENTED":
                        self.supported = False
                        self._notice("WARN", code, "此 Runtime 不支持历史日志查询")
                        return
                    if failures > 3 or (isinstance(error, grpc.RpcError) and error.code() not in {
                            grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}):
                        self._notice("WARN", "GLOBAL_LOG_STOPPED", "Runtime 日志查询已停止；重新打开日志窗口可重连",
                                     errorCode=code, reason=str(error))
                        return
                    if not disconnected:
                        self._notice("WARN", "GLOBAL_LOG_DISCONNECTED", "Runtime 日志查询中断，正在重连",
                                     errorCode=code, reason=str(error))
                        disconnected = True
                    if self.stop.wait(.5):
                        return
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

    @property
    def unsupported(self):
        return self.supported is False

    def close(self):
        self.stop.set()
        self.context.cancel()
        with self.lock:
            cancel = getattr(self.stream, "cancel", None)
            if callable(cancel):
                cancel()
        self.thread.join(6)
        if self.thread.is_alive():
            raise TimeoutError("Runtime 日志查询尚未结束")
