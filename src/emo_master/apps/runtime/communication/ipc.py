"""One bounded request/response pipe per Job; never carries a TCP socket."""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import json
import threading
import time

from emo_master.core.contracts.tcp_service import TcpServiceError, TcpSendReceipt
from emo_master.apps.runtime.communication.pipe_io import sendBytes, receiveBytes

MAX_IPC_BYTES = 24 * 1024 * 1024
MAX_REPLIES = 128


class WorkerBridge:
    def __init__(self, manager, jobId, connection, cancelEvent):
        self.manager, self.jobId = manager, jobId
        self.connection, self.cancelEvent = connection, cancelEvent
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"tcp-ipc-{jobId}", daemon=True)
        self.lastSequence = 0
        self.replies: OrderedDict[int, tuple[str, dict]] = OrderedDict()
        self.closeLock = threading.Lock()
        self.identity = manager.executionIdentity()

    def start(self):
        self.thread.start()

    def cancelled(self):
        return self.stopped.is_set() or self.cancelEvent.is_set() or self.identity != self.manager.executionIdentity()

    def dispatch(self, raw):
        request = json.loads(raw)
        sequence = request.get("sequence")
        if request.get("protocolVersion") != "1.0" or type(sequence) is not int or sequence < 1:
            raise TcpServiceError("E_TCP_IPC_PROTOCOL", "无效通信 IPC 请求")
        digest = hashlib.sha256(raw).hexdigest()
        if sequence <= self.lastSequence:
            saved = self.replies.get(sequence)
            if saved is None:
                raise TcpServiceError("E_TCP_REQUEST_EXPIRED", "请求回执已过期，不允许重放发送")
            if saved[0] != digest:
                raise TcpServiceError("E_TCP_REQUEST_MISMATCH", "相同请求标识的数据摘要不同")
            return saved[1]
        self.lastSequence = sequence
        if self.cancelled():
            raise TcpServiceError("E_CANCELLED", "Job 的通信权限已撤销")
        action = request.get("action")
        try:
            deadlineNs = request.get("deadlineNs")
            if type(deadlineNs) is not int or deadlineNs <= 0:
                raise TcpServiceError("E_TCP_IPC_PROTOCOL", "通信请求缺少绝对时限")
            deadline = deadlineNs / 1_000_000_000
            if time.monotonic() >= deadline:
                raise TcpServiceError("E_TCP_REQUEST_EXPIRED", "请求已超时，未进入发送边界")
            if action == "acquire":
                timeout = request.get("timeoutMs")
                if type(timeout) is not int or not 1 <= timeout <= 60000:
                    raise TcpServiceError("E_PARAM_INVALID", "等待时限必须在 1—60000 毫秒之间")
                result = self.manager.acquire(self.jobId, request["serviceId"], request["channelId"], timeout,
                                              self.cancelled, deadline=deadline)
            elif action == "send":
                data = base64.b64decode(request["dataBase64"], validate=True)
                if len(data) > 16777216:
                    raise TcpServiceError("E_TCP_MESSAGE_TOO_LARGE", "报文超过传输上限")
                source = request.get("source", {})
                if not isinstance(source, dict) or len(json.dumps(source)) > 4096:
                    raise TcpServiceError("E_TCP_IPC_PROTOCOL", "无效节点来源")
                result = self.manager.send(self.jobId, f"{self.jobId}:{sequence}", request["session"], data,
                                           self.cancelled, source, deadline=deadline)
            else:
                raise TcpServiceError("E_TCP_IPC_PROTOCOL", "Worker 不允许启动或停止通信服务")
            reply = dict(ok=True, result=result, sendTimeoutMs=self.manager.effective.tcpServer.sendTimeoutMs)
        except Exception as error:
            reply = dict(ok=False, code=getattr(error, "code", "E_TCP_IPC_REQUEST"), message=str(error),
                         receipt=getattr(error, "receipt", None))
        self.replies[sequence] = (digest, reply)
        while len(self.replies) > MAX_REPLIES:
            self.replies.popitem(last=False)
        return reply

    def run(self):
        try:
            while not self.stopped.is_set():
                if not self.connection.poll(0.05):
                    continue
                raw = receiveBytes(self.connection, MAX_IPC_BYTES,
                                   lambda: self.stopped.is_set() or self.cancelEvent.is_set(), time.monotonic() + 60)
                try:
                    reply = self.dispatch(raw)
                except Exception as error:
                    reply = dict(ok=False, code=getattr(error, "code", "E_TCP_IPC_PROTOCOL"), message=str(error))
                sendBytes(self.connection, json.dumps(reply, ensure_ascii=True).encode(),
                          self.stopped.is_set, time.monotonic() + 1.0)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            self.stopped.set()
            self.closeConnection()

    def closeConnection(self):
        # Windows Connection.close is idempotent but not concurrently atomic.
        with self.closeLock:
            self.connection.close()

    def close(self):
        self.stopped.set()
        self.thread.join(2.0)
        if self.thread.is_alive():
            raise TcpServiceError("E_TCP_RELEASE_FAILED", "Job 通信 IPC 尚未释放")
        self.closeConnection()


class CommunicationProxy:
    def __init__(self, connection, cancelEvent):
        self.connection, self.cancelEvent = connection, cancelEvent
        self.sequence = 0
        self.lock = threading.Lock()
        self.closed = False
        self.sendTimeoutMs = 60000

    def call(self, action, **fields):
        deadline = time.monotonic() + fields.get("timeoutMs", self.sendTimeoutMs) / 1000
        while not self.lock.acquire(timeout=.02):
            if self.closed or self.cancelEvent.is_set() or time.monotonic() >= deadline:
                raise TcpServiceError("E_CANCELLED", "通信请求排队已取消或超时")
        try:
            if self.closed or self.cancelEvent.is_set():
                raise TcpServiceError("E_CANCELLED", "Job 通信已取消或关闭")
            self.sequence += 1
            request = dict(protocolVersion="1.0", sequence=self.sequence, action=action,
                           deadlineNs=int(deadline * 1_000_000_000), **fields)
            raw = json.dumps(request, ensure_ascii=True).encode()
            if len(raw) > MAX_IPC_BYTES:
                raise TcpServiceError("E_TCP_MESSAGE_TOO_LARGE", "通信请求超过 IPC 字节上限")
            dispatched = False
            try:
                dispatched = True
                def cancelled():
                    return self.closed or self.cancelEvent.is_set()
                sendBytes(self.connection, raw, cancelled, deadline)
                reply = json.loads(receiveBytes(self.connection, MAX_IPC_BYTES, cancelled, deadline + .25))
                if not reply.get("ok"):
                    raise TcpServiceError(reply.get("code", "E_TCP_IPC"), reply.get("message", "通信请求失败"), reply.get("receipt"))
                self.sendTimeoutMs = reply.get("sendTimeoutMs", self.sendTimeoutMs)
                return reply["result"]
            except (OSError, EOFError, ValueError) as error:
                self.close()
                code = "E_TCP_OUTCOME_UNKNOWN" if action == "send" and dispatched else "E_TCP_IPC_CLOSED"
                raise TcpServiceError(code, str(error)) from error
        finally:
            self.lock.release()

    def acquire(self, serviceId, channelId, timeoutMs):
        return self.call("acquire", serviceId=serviceId, channelId=channelId, timeoutMs=timeoutMs)

    def send(self, session, data, source):
        try:
            return self.call("send", session=session, dataBase64=base64.b64encode(data).decode("ascii"), source=source)
        except TcpServiceError as error:
            if error.receipt is None:
                receipt = TcpSendReceipt(requestId=f"{session['jobId']}:{self.sequence}", jobId=session["jobId"],
                    channelId=session["channelId"], serviceGeneration=session["serviceGeneration"],
                    connectionGeneration=session["connectionGeneration"], bytesRequested=len(data), bytesSent=0,
                    outcome="OUTCOME_UNKNOWN" if error.code == "E_TCP_OUTCOME_UNKNOWN" else "NOT_SENT",
                    code=error.code, message=str(error)).toPayload()
                raise TcpServiceError(error.code, str(error), receipt) from error
            raise

    def close(self):
        self.closed = True
        self.connection.close()
