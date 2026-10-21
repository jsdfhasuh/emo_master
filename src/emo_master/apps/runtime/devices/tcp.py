"""TCP resources owned by the same device authority as cameras and PLCs."""
import base64
import socket
from threading import Event, RLock
from time import monotonic

from emo_master.core.contracts.device_driver import DeviceDriver, DeviceError
from emo_master.core.contracts.tcp_service import TcpServiceError
from emo_master.core.project.communication import tcpServerConfiguration
from emo_master.plugins.builtins._tcp_transport import (
    CancellableSocket, configureListener, connectCancellable, receiveFramed, sendAll,
)


def listenerIdentity(host, port):
    return f'listener://{host}:{port}'


class ManagedTcpServer(DeviceDriver):
    @property
    def definition(self):
        return self._definition

    @definition.setter
    def definition(self, value):
        self._definition = value
        if getattr(self, 'backend', None) is not None:
            context = self.runtimeContext
            self.backend.configure(context.projectId, context.projectLoadGeneration, tcpServerConfiguration(value))

    def __init__(self, definition):
        self.definition = definition
        self.backend = None

    @staticmethod
    def physicalIdentity(definition):
        return listenerIdentity(definition.connection['bindAddress'], definition.connection['port'])

    def bindRuntime(self, context):
        super().bindRuntime(context)
        from emo_master.apps.runtime.communication.manager import CommunicationManager
        self.backend = CommunicationManager(context.runtimeInstanceId)
        self.backend.configure(context.projectId, context.projectLoadGeneration, tcpServerConfiguration(self.definition))
        self.backend.projectLoadGeneration = context.projectLoadGeneration
        self.backend.deviceId = context.deviceId
        self.backend.eventSink = context.events

    def connect(self, params, cancelled, deadline):
        if self.disposed or cancelled() or monotonic() >= deadline:
            raise DeviceError('E_DEVICE_CANCELLED', 'TCP startup cancelled or disposed')
        try:
            self.backend.start()
        except TcpServiceError as error:
            raise DeviceError(error.code, str(error), diagnostics=error.diagnostics) from error

    def validateAction(self, action, arguments):
        if action == 'send':
            base64.b64decode(arguments['dataBase64'], validate=True)

    def execute(self, action, params, cancelled, deadline, *, arguments, operationContext):
        try:
            if action == 'acquire_session':
                return self.backend.acquire(operationContext.ownerId, 'tcp-server',
                    operationContext.selectors['channelId'], params['timeoutMs'], cancelled, deadline=deadline)
            if action == 'send':
                if arguments['session']['deviceId'] != self.runtimeContext.deviceId:
                    raise DeviceError('E_TCP_SESSION_DEVICE', 'session belongs to a different bound device')
                data = base64.b64decode(arguments['dataBase64'], validate=True)
                receipt = self.backend.send(operationContext.ownerId, operationContext.requestId,
                    arguments['session'], data, cancelled, operationContext.source, deadline=deadline)
                if receipt['outcome'] != 'SENT_TO_TRANSPORT':
                    raise DeviceError(receipt['code'] or 'E_TCP_SEND_FAILED', receipt['message'],
                        outcome=receipt['outcome'], diagnostics={'tcpReceipt': receipt})
                return receipt
            raise DeviceError('E_DEVICE_ACTION', 'unsupported TCP server action')
        except TcpServiceError as error:
            raise DeviceError(error.code, str(error), diagnostics=error.diagnostics,
                outcome=error.receipt['outcome'] if error.receipt else 'NOT_SENT') from error

    def revokeOwner(self, ownerId):
        self.backend.revokeJob(ownerId)

    def requestStop(self, reason):
        self.backend.beginStop()

    def close(self, deadline=None):
        if self.backend is not None:
            self.backend.stop()

    def dispose(self, deadline=None):
        if self.backend is not None:
            self.backend.close()
        self.disposed = True
        self.runtimeContext = None

    def snapshot(self):
        state = self.backend.snapshot() if self.backend else {}
        state.pop('records', None)
        return dict(state, ready=state.get('state') == 'LISTENING')

    def testConnection(self, params, cancelled, deadline):
        message = '监听端点可用；本次测试不应用草稿通道规则，未验证外部客户端'
        if self.snapshot().get('ready'):
            return dict(endpointAvailable=True, reused=True, message=message)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((self.definition.connection['bindAddress'], self.definition.connection['port']))
        return dict(endpointAvailable=True, message=message)


class ManagedTcpOnce(DeviceDriver):
    def __init__(self, definition):
        self.definition = definition
        self.stop = Event()
        self.lock = RLock()
        self.sockets = set()
        self.lastResult = {}
        self.active = False

    def validateAction(self, action, arguments):
        if "dataBase64" in arguments:
            base64.b64decode(arguments["dataBase64"], validate=True)

    def _track(self, sock):
        with self.lock:
            self.sockets.add(sock)
        return sock

    def requestStop(self, reason):
        self.stop.set()
        with self.lock:
            for sock in self.sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self, deadline=None):
        with self.lock:
            if self.active:
                raise DeviceError('E_DEVICE_RELEASE_PENDING', 'TCP transaction still owns resources', unhealthy=True)
            for sock in self.sockets:
                sock.close()
            self.sockets.clear()

    def snapshot(self):
        with self.lock:
            return dict(ready=not self.disposed and not self.active, ownsResources=bool(self.sockets),
                state='BUSY' if self.active else 'TRANSACTION_READY', lastResult=self.lastResult)

    def execute(self, action, params, cancelled, deadline, *, arguments, operationContext):
        if self.disposed:
            raise DeviceError('E_DEVICE_UNAVAILABLE', 'device disposed')
        self.stop.clear()
        self.active = True
        sent = 0
        wire = None
        attempted = False
        def stopped():
            return cancelled() or self.stop.is_set()
        try:
            if stopped():
                raise DeviceError('E_DEVICE_CANCELLED', 'TCP action cancelled before execution')
            if isinstance(self, ManagedTcpClientOnce):
                sock = self._track(connectCancellable(self.definition.connection['host'], self.definition.connection['port'],
                    stopped, min(deadline, monotonic() + params['connectTimeoutMs']/1000)))
                wire = CancellableSocket(sock, stopped, deadline, params['responseTimeoutMs']/1000)
                data = base64.b64decode(arguments['dataBase64'], validate=True)
                if not data or len(data) > params['maxRequestBytes']:
                    raise DeviceError('E_TCP_MESSAGE_SIZE', 'TCP request is empty or exceeds maxRequestBytes')
                attempted = True
                sendAll(wire, data)
                sent = len(data)
                if params['shutdownWrite']:
                    sock.shutdown(socket.SHUT_WR)
                result = dict(sentBytes=sent)
                if action == 'exchange':
                    wire.settimeout(params['responseTimeoutMs']/1000)
                    response = receiveFramed(wire, framing=params['responseFraming'], maxBytes=params['maxResponseBytes'],
                        expectedBytes=params['expectedBytes'], rejectTrailing=params['rejectTrailingResponse'])
                    result.update(dataBase64=base64.b64encode(response).decode('ascii'),
                        peerHost=self.definition.connection['host'], peerPort=self.definition.connection['port'])
            else:
                server = self._track(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                configureListener(server)
                server.bind((self.definition.connection['bindHost'], self.definition.connection['port']))
                server.listen(1)
                acceptEnd = min(deadline, monotonic()+params['acceptTimeoutMs']/1000)
                while True:
                    if stopped():
                        raise DeviceError('E_DEVICE_CANCELLED', 'TCP accept cancelled')
                    if monotonic() >= acceptEnd:
                        raise DeviceError('E_TCP_ACCEPT_TIMEOUT', 'waiting for one TCP client timed out')
                    server.settimeout(min(.05, max(.001, acceptEnd-monotonic())))
                    try:
                        sock, peer = server.accept()
                        break
                    except socket.timeout:
                        continue
                self._track(sock)
                wire = CancellableSocket(sock, stopped, deadline, params['readTimeoutMs']/1000)
                data = receiveFramed(wire, framing=params['framing'], maxBytes=params['maxMessageBytes'], expectedBytes=params['expectedBytes'])
                if params['ackText']:
                    from emo_master.plugins.builtins._communication_operators import _encodeTcpData
                    ack = _encodeTcpData(params['ackText'], params['ackEncoding']) + (b'\n' if params['ackAppendNewline'] else b'')
                    if len(ack) > params['maxAckBytes']:
                        raise DeviceError('E_TCP_ACK_SIZE', 'ACK exceeds maxAckBytes')
                    wire.settimeout(params['readTimeoutMs']/1000)
                    attempted = True
                    sendAll(wire, ack)
                result = dict(dataBase64=base64.b64encode(data).decode('ascii'), peerHost=str(peer[0]), peerPort=peer[1])
            self.lastResult = dict(outcome='COMPLETED', bytesSent=wire.bytesSent if wire else sent)
            return result
        except Exception as error:
            self.lastResult = dict(code=getattr(error, 'code', 'E_TCP_IO'), message=str(error),
                outcome='OUTCOME_UNKNOWN' if attempted else 'NOT_SENT', bytesSent=wire.bytesSent if wire else sent,
                automaticRetry=False)
            raise DeviceError(self.lastResult['code'], str(error), outcome=self.lastResult['outcome'],
                              diagnostics={'tcpTransaction': dict(self.lastResult)}) from error
        finally:
            with self.lock:
                for sock in self.sockets:
                    sock.close()
                self.sockets.clear()
                self.active = False


class ManagedTcpClientOnce(ManagedTcpOnce):
    @staticmethod
    def physicalIdentity(definition):
        return None

    def testConnection(self, params, cancelled, deadline):
        from emo_master.plugins.builtins._tcp_transport import TcpConnectError
        import errno
        try:
            with connectCancellable(self.definition.connection['host'], self.definition.connection['port'], cancelled, deadline):
                return dict(endpointAvailable=True, message='远端连接可用；未发送业务报文')
        except TcpConnectError as error:
            if isinstance(error.__cause__, OSError) and error.__cause__.errno in {errno.ECONNREFUSED, 10061}:
                raise DeviceError('E_DEVICE_CONNECTION_REFUSED', str(error)) from error
            raise


class ManagedTcpReceiveOnce(ManagedTcpOnce):
    @staticmethod
    def physicalIdentity(definition):
        return listenerIdentity(definition.connection['bindHost'], definition.connection['port'])

    def testConnection(self, params, cancelled, deadline):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            configureListener(sock)
            sock.bind((self.definition.connection['bindHost'], self.definition.connection['port']))
        return dict(endpointAvailable=True, message='单次监听端点可用；未验证外部客户端')
