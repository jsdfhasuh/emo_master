from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import select
import socket
import threading
import time
from uuid import uuid4

from emo_master.core.contracts.tcp_service import TcpServiceError, TcpSessionRef, TcpSendReceipt
from emo_master.core.project.communication import CommunicationSettings, projectCommunication


MAX_QUEUED_SENDS = 32
MAX_QUEUED_BYTES = 16777216
MAX_RECORDS = 512
MAX_RECORD_BYTES = 1048576
MAX_RECORD_DATA = 1024
MAX_LEASES = 4096
MAX_WORKERS = 128


def revision(config):
    value = json.dumps(config.transportPayload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(value.encode()).hexdigest()


def configurationRevision(config):
    """Management CAS includes policy and enablement; leases bind transport only."""
    value = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Peer:
    channelId: str
    sock: object
    address: tuple
    generation: int
    connectedAtMs: int
    sendLock: object = field(default_factory=threading.Lock)
    queuedCount: int = 0
    queuedBytes: int = 0
    lastSend: dict | None = None


class CommunicationManager:
    def __init__(self, runtimeInstanceId):
        self.runtimeInstanceId = runtimeInstanceId
        self.deviceId = "tcp-server"
        self.eventSink = None
        self.projectId = ""
        self.projectKey = ""
        self.projectLoadGeneration = str(uuid4())
        self.desired = CommunicationSettings()
        self.effective = CommunicationSettings()
        self.state = "STOPPED"
        self.error = ""
        self.serviceGeneration = 0
        self.controlRevision = 0
        self._connectionOrdinal = 0
        self._lock = threading.RLock()
        self._control = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._listener = None
        self._stop = threading.Event()
        self._threads: set[threading.Thread] = set()
        self._peers: dict[str, Peer] = {}
        self._lastSends: dict[str, dict] = {}
        self._channelGenerations: dict[str, int] = {}
        self._leases: OrderedDict[str, dict] = OrderedDict()
        self._workers: dict[str, object] = {}
        self._records: deque[tuple[dict, int]] = deque()
        self._recordBytes = 0
        self._sequence = 0
        self._dropped = 0
        self._closed = False
        self._disableSaveToken = ""
        self._saveRequiresDisabled = False

    def _record(self, kind, message, channelId="", data=None, **fields):
        with self._lock:
            self._sequence += 1
            record = dict(sequence=self._sequence, timestampMs=int(time.time() * 1000),
                          kind=kind, message=message, channelId=channelId,
                          serviceGeneration=fields.pop("serviceGeneration", self.serviceGeneration), **fields)
            if data is not None:
                record.update(bytes=len(data), dataHex=data[:MAX_RECORD_DATA].hex(" ").upper(),
                              truncated=len(data) > MAX_RECORD_DATA)
            size = len(json.dumps(record, ensure_ascii=True).encode())
            self._records.append((record, size))
            self._recordBytes += size
            while len(self._records) > MAX_RECORDS or self._recordBytes > MAX_RECORD_BYTES:
                self._recordBytes -= self._records.popleft()[1]
                self._dropped += 1
            if self.eventSink is not None:
                self.eventSink("device.tcp." + kind, message, **{k: v for k, v in record.items() if k != "message"})

    def configure(self, projectId, projectKey, config, *, saveToken=""):
        config = CommunicationSettings.model_validate(config.model_dump())
        with self._control:
            with self._lock:
                if self._closed:
                    raise TcpServiceError("E_RUNTIME_CLOSING", "Runtime 正在关闭")
                if self._disableSaveToken and saveToken != self._disableSaveToken:
                    raise TcpServiceError("E_TCP_SAVE_PENDING", "通信配置正在保存，暂不能同步或切换项目")
                if saveToken and (saveToken != self._disableSaveToken or (self._saveRequiresDisabled and config.enabled)):
                    raise TcpServiceError("E_TCP_SAVE_TOKEN", "配置保存凭据已失效")
            same = self.projectId == projectId and self.projectKey == projectKey
            if not same:
                if self._workers:
                    raise TcpServiceError("E_TCP_BUSY", "旧项目的通信 Worker 尚未释放")
                self.stop()
            with self._lock:
                if self._closed:
                    raise TcpServiceError("E_RUNTIME_CLOSING", "Runtime 正在关闭")
                if same and not config.enabled and (self.state in {"STARTING", "LISTENING", "STOPPING"}
                        or self._listener is not None or self._peers or any(t.is_alive() for t in self._threads)):
                    raise TcpServiceError("E_TCP_BUSY", "请先停止通信服务，再关闭本项目 TCP 通信")
                if not same:
                    self.projectLoadGeneration = str(uuid4())
                    self.projectId, self.projectKey = projectId, projectKey
                    self._leases.clear()
                    self._records.clear()
                    self._lastSends.clear()
                    self._channelGenerations.clear()
                    self._recordBytes = self._sequence = self._dropped = 0
                self.desired = config
                self._disableSaveToken = ""
                self._saveRequiresDisabled = False
                if (self.state not in {"STARTING", "LISTENING", "STOPPING"}
                        or revision(config) == revision(self.effective)):
                    self.effective = config.model_copy(deep=True)
                self._changed.notify_all()

    def load(self, document, projectKey):
        self.configure(document.project.projectId, projectKey, projectCommunication(document))

    def prepareDisableSave(self, token):
        self.prepareSave(token, disabling=True)

    def prepareReplacement(self, token):
        with self._control, self._lock:
            if self._closed:
                raise TcpServiceError("E_RUNTIME_CLOSING", "Runtime is closing")
            if self._workers or self.state not in {"STOPPED", "FAILED"} or self.snapshot()["ownsResources"]:
                raise TcpServiceError("E_CONFIG_REQUIRES_STOP", "stop and retire communication before replacement")
            self.prepareSave(token)

    def prepareSave(self, token, *, disabling=False):
        # One reservation for both ordinary and disable saves. Existing disable
        # RPCs retain their stopped-service requirement and wire compatibility.
        with self._control, self._lock:
            if not token or len(token) > 160:
                raise TcpServiceError("E_TCP_SAVE_TOKEN", "配置保存需要唯一凭据")
            if self._disableSaveToken and (self._disableSaveToken != token or self._saveRequiresDisabled != disabling):
                raise TcpServiceError("E_TCP_SAVE_PENDING", "另一个配置保存尚未结束")
            if disabling and (self.state not in {"STOPPED", "FAILED"} or self.snapshot()["ownsResources"]):
                raise TcpServiceError("E_TCP_BUSY", "请先停止通信服务，再关闭本项目 TCP 通信")
            self._disableSaveToken = token
            self._saveRequiresDisabled = disabling

    def abortDisableSave(self, token):
        with self._control, self._lock:
            if self._disableSaveToken and token != self._disableSaveToken:
                raise TcpServiceError("E_TCP_SAVE_TOKEN", "不能撤销其他客户端的配置保存")
            self._disableSaveToken = ""
            self._saveRequiresDisabled = False

    def start(self):
        with self._control:
            with self._lock:
                if self._disableSaveToken:
                    raise TcpServiceError("E_TCP_SAVE_PENDING", "配置保存尚未结束，禁止启动通信服务")
                if self._closed:
                    raise TcpServiceError("E_RUNTIME_CLOSING", "Runtime 正在关闭")
                if not self.projectId or not self.desired.enabled:
                    raise TcpServiceError("E_TCP_DISABLED", "本项目未启用 TCP 通信")
                if self.state == "LISTENING":
                    if revision(self.desired) != revision(self.effective):
                        raise TcpServiceError("E_TCP_CONFIG_PENDING", "通信配置等待生效，请停止流程并重启服务")
                    return self.snapshot()
                needsCleanup = self.state == "FAILED" or self._listener is not None or bool(self._peers)
            # Do not join under _lock: listener/client finalizers need that lock.
            # A failed cleanup must prevent bind and retain truthful ownership.
            if needsCleanup:
                self.stop()
            with self._lock:
                if any(t.is_alive() for t in self._threads):
                    raise TcpServiceError("E_TCP_RELEASE_FAILED", "上次通信线程尚未释放，不能重新启动")
                self.effective = self.desired.model_copy(deep=True)
                config = self.effective.tcpServer
                self.state, self.error = "STARTING", ""
                self._stop = threading.Event()
                self.serviceGeneration += 1
                self._lastSends.clear()
                self._channelGenerations.clear()
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if exclusive is not None:
                    listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                else:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((config.bindAddress, config.port))
                listener.listen(min(64, len(config.channels)))
                listener.settimeout(0.1)
            except OSError as error:
                listener.close()
                with self._lock:
                    self.state, self.error = "FAILED", str(error)
                    self._record("event", f"监听失败：{config.bindAddress}:{config.port} · {error}", code="E_TCP_BIND_FAILED")
                raise TcpServiceError("E_TCP_BIND_FAILED", self.error) from error
            with self._lock:
                self._listener = listener
                self.state = "LISTENING"
                self._record("event", f"开始监听 {config.bindAddress}:{config.port}")
                try:
                    self._spawn(self._accept, "tcp-listener", listener)
                except Exception as error:
                    self._failService(error, "E_TCP_THREAD_FAILED")
                    raise TcpServiceError("E_TCP_THREAD_FAILED", str(error)) from error
                self._changed.notify_all()
            return self.snapshot()

    def _spawn(self, target, name, *args):
        def run():
            try:
                target(*args)
            except Exception as error:
                self._failService(error, "E_TCP_THREAD_FAILED")
            finally:
                with self._lock:
                    self._threads.discard(threading.current_thread())
                    self._changed.notify_all()
        thread = threading.Thread(target=run, name=name, daemon=True)
        self._threads.add(thread)
        try:
            thread.start()
        except BaseException:
            self._threads.discard(thread)
            raise

    def _failService(self, error, code):
        # Called from owned I/O threads: close sockets and wake waits, but never
        # join this thread or acquire _control (STOP may be joining us there).
        with self._changed:
            if self._stop.is_set():
                return
            self.state, self.error = "FAILED", str(error)
            self._stop.set()
            self._leases.clear()
            if self._listener is not None:
                try:
                    self._listener.close()
                except OSError as failure:
                    self.error += f"；监听资源未释放：{failure}"
                else:
                    self._listener = None
            for peer in list(self._peers.values()):
                self._disconnect(peer)
            self._record("event", f"通信服务异常：{self.error}", code=code)
            self._changed.notify_all()

    def _accept(self, listener):
        routes = {ip: channel.channelId for channel in self.effective.tcpServer.channels for ip in channel.allowedIps}
        lastRejection = 0.0
        while not self._stop.is_set():
            try:
                client, address = listener.accept()
            except socket.timeout:
                continue
            except OSError as error:
                self._failService(error, "E_TCP_LISTENER_FAILED")
                break
            with self._lock:
                channelId = routes.get(address[0], "")
                if self._stop.is_set() or not channelId or channelId in self._peers:
                    client.close()
                    if time.monotonic() - lastRejection >= 0.2:
                        self._record("event", f"拒绝客户端 {address[0]}：未知地址或通道已有连接", channelId, code="E_TCP_CLIENT_REJECTED")
                        lastRejection = time.monotonic()
                    continue
                client.setblocking(False)
                self._connectionOrdinal += 1
                peer = Peer(channelId, client, address, self._connectionOrdinal, int(time.time() * 1000))
                self._peers[channelId] = peer
                self._channelGenerations[channelId] = peer.generation
                self._lastSends.pop(channelId, None)
                self._record("event", f"客户端连接 {address[0]}:{address[1]}", channelId, connectionGeneration=peer.generation)
                try:
                    self._spawn(self._read, f"tcp-client-{channelId}", peer)
                except Exception:
                    self._disconnect(peer)
                    raise
                self._changed.notify_all()

    def _read(self, peer):
        try:
            while not self._stop.is_set():
                readable, _, _ = select.select([peer.sock], [], [], 0.1)
                if not readable:
                    continue
                data = peer.sock.recv(4096)
                if not data:
                    break
                self._record("rx", "RX 原始字节块（非协议报文）", peer.channelId, data, connectionGeneration=peer.generation)
        except (OSError, ValueError):
            pass
        finally:
            self._disconnect(peer)

    def _disconnect(self, peer):
        with self._lock:
            if self._peers.get(peer.channelId) is peer:
                del self._peers[peer.channelId]
                self._record("event", "客户端断开", peer.channelId, connectionGeneration=peer.generation)
                self._changed.notify_all()
            try:
                peer.sock.close()
            except OSError:
                pass

    def beginStop(self):
        with self._changed:
            self._stop.set()
            self._leases.clear()
            self._changed.notify_all()
            listener, peers = self._listener, list(self._peers.values())
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        for peer in peers:
            self._disconnect(peer)

    def stop(self):
        with self._control:
            with self._lock:
                threads = list(self._threads)
                if self.state == "STOPPED" and not threads and self._listener is None and not self._peers:
                    return self.snapshot()
                self.state = "STOPPING"
                self._stop.set()
                listener = self._listener
                peers = list(self._peers.values())
                self._leases.clear()
                self._changed.notify_all()
            if listener is not None:
                try:
                    listener.close()
                except OSError as error:
                    with self._lock:
                        self.state, self.error = "FAILED", f"监听资源未释放：{error}"
                    raise TcpServiceError("E_TCP_RELEASE_FAILED", self.error) from error
                with self._lock:
                    self._listener = None
            for peer in peers:
                self._disconnect(peer)
            deadline = time.monotonic() + 2.0
            for thread in threads:
                if thread is not threading.current_thread():
                    thread.join(max(0.0, deadline - time.monotonic()))
            with self._lock:
                if any(t.is_alive() for t in threads):
                    self.state, self.error = "FAILED", "通信线程仍持有资源"
                    raise TcpServiceError("E_TCP_RELEASE_FAILED", self.error)
                self.state, self.error = "STOPPED", ""
                self._record("event", "服务已停止，连接已释放；此前已发数据不会撤销")
                return self.snapshot()

    def snapshot(self, afterSequence=0):
        with self._lock:
            channels = []
            for channel in self.effective.tcpServer.channels:
                peer = self._peers.get(channel.channelId)
                channels.append(dict(**channel.model_dump(), connected=peer is not None,
                    peerAddress=list(peer.address) if peer else None,
                    connectionGeneration=peer.generation if peer else 0,
                    connectedAtMs=peer.connectedAtMs if peer else 0, lastSend=deepcopy(self._lastSends.get(channel.channelId))))
            records = [deepcopy(item) for item, _ in self._records if item["sequence"] > afterSequence]
            return dict(protocolVersion="1.1", runtimeInstanceId=self.runtimeInstanceId,
                projectId=self.projectId, projectLoadGeneration=self.projectLoadGeneration,
                projectPath=self.projectKey,
                enabled=self.desired.enabled, state=self.state, error=self.error,
                ownsResources=self._listener is not None or bool(self._peers) or any(t.is_alive() for t in self._threads),
                serviceGeneration=self.serviceGeneration, configRevision=revision(self.effective),
                pending=revision(self.desired) != revision(self.effective),
                desiredRevision=configurationRevision(self.desired),
                savePending=bool(self._disableSaveToken),
                disableSavePending=bool(self._disableSaveToken) and self._saveRequiresDisabled,
                desired=self.desired.model_dump(), effective=self.effective.model_dump(),
                channels=channels, sequence=self._sequence, records=records[-128:],
                gap=bool(records and records[0]["sequence"] > afterSequence + 1) or len(records) > 128,
                droppedRecords=self._dropped)

    def acquire(self, jobId, serviceId, channelId, timeoutMs, cancelled, *, deadline=None):
        deadline = min(deadline or float("inf"), time.monotonic() + timeoutMs / 1000)
        with self._changed:
            while True:
                if cancelled():
                    raise TcpServiceError("E_CANCELLED", "等待连接已取消")
                if self._closed:
                    raise TcpServiceError("E_RUNTIME_CLOSING", "Runtime 正在关闭")
                if self.state != "LISTENING":
                    raise TcpServiceError("E_TCP_NOT_RUNNING", "通信服务尚未监听，请先启动服务")
                if serviceId != self.effective.tcpServer.serviceId or channelId not in {c.channelId for c in self.effective.tcpServer.channels}:
                    raise TcpServiceError("E_TCP_CHANNEL_MISSING", "所选服务或通道不存在")
                if time.monotonic() >= deadline:
                    raise TcpServiceError("E_TCP_CONNECT_TIMEOUT", "等待客户端连接超时")
                peer = self._peers.get(channelId)
                if peer:
                    session = TcpSessionRef(deviceId=self.deviceId, runtimeInstanceId=self.runtimeInstanceId, projectId=self.projectId,
                        projectLoadGeneration=self.projectLoadGeneration, serviceId=serviceId,
                        serviceGeneration=self.serviceGeneration, configRevision=revision(self.effective),
                        channelId=channelId, connectionGeneration=peer.generation, leaseId=uuid4().hex, jobId=jobId).toPayload()
                    self._leases[session["leaseId"]] = session
                    while len(self._leases) > MAX_LEASES:
                        self._leases.popitem(last=False)
                    return session
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TcpServiceError("E_TCP_CONNECT_TIMEOUT", "等待客户端连接超时")
                self._changed.wait(min(0.05, remaining))

    def send(self, jobId, requestId, session, data, cancelled, source=None, *, deadline=None):
        ref = TcpSessionRef.fromPayload(session)
        receipt = TcpSendReceipt(deviceId=self.deviceId, requestId=requestId, jobId=jobId, channelId=ref.channelId,
            serviceGeneration=ref.serviceGeneration, connectionGeneration=ref.connectionGeneration,
            outcome="NOT_SENT", bytesRequested=len(data), bytesSent=0)
        started = False
        queued = locked = False
        peer = None
        deadline = min(deadline or float("inf"), time.monotonic() + self.effective.tcpServer.sendTimeoutMs / 1000)

        def check():
            if cancelled() or self._closed:
                raise TcpServiceError("E_CANCELLED", "通信请求已取消")
            if (self.state != "LISTENING" or ref.jobId != jobId
                    or self._leases.get(ref.leaseId) != session
                    or self._peers.get(ref.channelId) is not peer or peer.generation != ref.connectionGeneration):
                raise TcpServiceError("E_TCP_SESSION_EXPIRED", "会话已失效，不能发送给重连后的客户端")
            if time.monotonic() >= deadline:
                raise TcpServiceError("E_TCP_SEND_TIMEOUT", "排队或发送超时")

        try:
            with self._lock:
                peer = self._peers.get(ref.channelId)
                if peer is None:
                    raise TcpServiceError("E_TCP_SESSION_EXPIRED", "客户端已断开")
                check()
                if len(data) > self.effective.tcpServer.maxMessageBytes:
                    raise TcpServiceError("E_TCP_MESSAGE_TOO_LARGE", "报文超过配置的字节上限")
                if peer.queuedCount >= MAX_QUEUED_SENDS or peer.queuedBytes + len(data) > MAX_QUEUED_BYTES:
                    raise TcpServiceError("E_TCP_BACKPRESSURE", "客户端发送队列已满，请检查客户端读取状态")
                peer.queuedCount += 1
                peer.queuedBytes += len(data)
                queued = True
            while not locked:
                with self._lock:
                    check()
                locked = peer.sendLock.acquire(timeout=0.02)
            while receipt.bytesSent < len(data):
                with self._lock:
                    check()
                _, writable, _ = select.select([], [peer.sock], [], min(0.02, max(0, deadline - time.monotonic())))
                if not writable:
                    continue
                # After this boundary an exception cannot prove that no byte reached the peer.
                started = True
                try:
                    count = peer.sock.send(data[receipt.bytesSent:])
                except BlockingIOError:
                    continue
                if count <= 0:
                    raise OSError("client closed during send")
                receipt.bytesSent += count
            receipt.outcome = "SENT_TO_TRANSPORT"
            receipt.message = "本机 TCP 栈已接收全部字节；设备确认未启用"
        except (TcpServiceError, OSError, ValueError) as error:
            receipt.outcome = "OUTCOME_UNKNOWN" if started else "NOT_SENT"
            receipt.code = getattr(error, "code", "E_TCP_SEND_FAILED")
            receipt.message = str(error)
            if started and peer is not None:
                # A partial frame must not be concatenated with a later result.
                # Disconnect; never complete it on a replacement connection.
                self._disconnect(peer)
        finally:
            if locked:
                peer.sendLock.release()
            with self._lock:
                if queued:
                    peer.queuedCount -= 1
                    peer.queuedBytes -= len(data)
                payload = receipt.toPayload()
                sameProject = (ref.runtimeInstanceId == self.runtimeInstanceId
                               and ref.projectLoadGeneration == self.projectLoadGeneration)
                if (sameProject and peer is not None and peer.generation == ref.connectionGeneration
                        and ref.serviceGeneration == self.serviceGeneration
                        and self._channelGenerations.get(ref.channelId) == ref.connectionGeneration):
                    peer.lastSend = dict(timestampMs=int(time.time() * 1000), **payload)
                    self._lastSends[ref.channelId] = peer.lastSend
                # Never append another project's late completion into its new
                # history. Within this project retain the original generation.
                if sameProject:
                    self._record("tx", receipt.message, ref.channelId, data,
                                 outcome=receipt.outcome, code=receipt.code, requestId=requestId,
                                 source=source or {}, receipt=payload, connectionGeneration=ref.connectionGeneration,
                                 serviceGeneration=ref.serviceGeneration)
        return payload

    def openWorker(self, jobId, cancelEvent, context):
        from emo_master.apps.runtime.communication.ipc import WorkerBridge
        with self._lock:
            if len(self._workers) >= MAX_WORKERS or self._closed or jobId in self._workers:
                raise TcpServiceError("E_TCP_BUSY", "通信 Worker 数达到上限或 Runtime 正在关闭")
            parent, child = context.Pipe(duplex=True)
            bridge = WorkerBridge(self, jobId, parent, cancelEvent)
            self._workers[jobId] = bridge
            try:
                bridge.start()
            except BaseException:
                self._workers.pop(jobId, None)
                parent.close()
                child.close()
                raise
            return child

    def revokeJob(self, jobId):
        with self._lock:
            bridge = self._workers.get(jobId)
            self._leases = OrderedDict((key, value) for key, value in self._leases.items() if value["jobId"] != jobId)
            self._changed.notify_all()
        if bridge is not None:
            bridge.close()
            with self._lock:
                if self._workers.get(jobId) is bridge:
                    self._workers.pop(jobId, None)

    def close(self):
        with self._lock:
            self._closed = True
            jobs = list(self._workers)
        self.stop()
        for job in jobs:
            self.revokeJob(job)

    def beginClosing(self):
        with self._changed:
            self._closed = True
            self._changed.notify_all()

    def executionIdentity(self):
        with self._lock:
            return self.runtimeInstanceId, self.projectLoadGeneration, self.serviceGeneration, revision(self.effective)

    def preflight(self, document, workflowId):
        from emo_master.core.project.tcp_dependencies import communicationNodes
        dependencies = communicationNodes(document, workflowId)
        with self._control:
            config = projectCommunication(document)
            if dependencies and not config.enabled:
                wid, node = dependencies[0]
                raise TcpServiceError("E_TCP_DISABLED", f"[{wid}/{node.nodeId}] 请先启用本项目 TCP 通信")
            if dependencies and revision(config) != revision(self.effective) and self.state == "LISTENING":
                raise TcpServiceError("E_TCP_CONFIG_PENDING", "通信配置待生效，请停止流程并重启服务")
            if config.enabled and config.startupPolicy == "on_project_run" and self.state != "LISTENING":
                self.start()
            if dependencies and self.state != "LISTENING":
                wid, node = dependencies[0]
                raise TcpServiceError("E_TCP_NOT_RUNNING", f"[{wid}/{node.nodeId}] 请在通信面板手动启动服务")
