"""One bounded TCP data pipe per Job, routed through DeviceManager authority."""
import json
from threading import Event, Lock, Thread
from time import monotonic
from uuid import uuid4

from emo_master.core.contracts.device_driver import DeviceError
from emo_master.core.contracts.tcp_service import tcpActionBudget
from .pipe_io import sendBytes, receiveBytes

MAX_IPC_BYTES = 24 * 1024 * 1024


class WorkerBridge:
    def __init__(self, manager, jobId, connection, cancelEvent):
        self.manager, self.jobId, self.connection, self.cancelEvent = manager, jobId, connection, cancelEvent
        self.stopped = Event()
        self.identity = dict(manager.scope)
        self.lastSequence = 0
        self.thread = Thread(target=self.run, name='tcp-device-ipc-' + jobId, daemon=True)

    def start(self):
        self.thread.start()

    def cancelled(self):
        return self.stopped.is_set() or self.cancelEvent.is_set() or self.identity != self.manager.scope

    def dispatch(self, raw):
        request = json.loads(raw)
        sequence = request['sequence']
        if type(sequence) is not int or sequence != self.lastSequence+1 or request.get('kind') != 'device_action':
            raise DeviceError('E_TCP_IPC_PROTOCOL', 'invalid sequence or request kind; business actions are never replayed')
        self.lastSequence = sequence
        if self.cancelled():
            raise DeviceError('E_DEVICE_CANCELLED', 'TCP owner was revoked')
        deviceId = request['deviceId']
        definition = self.manager.definitions.get(deviceId)
        if definition is None or not definition.deviceTypeId.startswith('device.tcp.'):
            raise DeviceError('E_DEVICE_ACTION', 'TCP bridge requires a TCP device')
        budget = tcpActionBudget(definition.deviceTypeId, request['action'], request['params'], definition.policies)
        absolute = request['deadlineNs']/1_000_000_000
        if monotonic() >= absolute:
            raise DeviceError('E_TCP_REQUEST_EXPIRED', 'request expired before device admission')
        return self.manager.execute(self.jobId, deviceId, request['action'], request['params'],
            cycleId=request['cycleId'], arguments=request.get('arguments', {}), source=request['source'],
            selectors=request.get('selectors', {}), cancelled=self.cancelled,
            requestId=request['requestId'],
            requestDeadline=min(absolute, monotonic()+(budget['queueWaitMs']+budget['actionBudgetMs'])/1000))

    def run(self):
        try:
            while not self.cancelled():
                if not self.connection.poll(.02):
                    continue
                raw = receiveBytes(self.connection, MAX_IPC_BYTES, self.cancelled, monotonic()+60)
                try:
                    result = self.dispatch(raw)
                    reply = dict(ok=True, result=result)
                except Exception as error:
                    reply = dict(ok=False, code=getattr(error, 'code', 'E_TCP_IPC'), message=str(error),
                        outcome=getattr(error, 'outcome', 'UNKNOWN'), diagnostics=getattr(error, 'diagnostics', {}))
                sendBytes(self.connection, json.dumps(reply, ensure_ascii=True).encode(), self.stopped.is_set, monotonic()+5)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            self.stopped.set()
            self.connection.close()

    def close(self):
        self.stopped.set()
        self.thread.join(5)
        if self.thread.is_alive():
            raise DeviceError('E_DEVICE_RELEASE_PENDING', 'TCP data bridge is still exiting')
        self.connection.close()


class CommunicationProxy:
    def __init__(self, connection, cancelEvent):
        self.connection, self.cancelEvent = connection, cancelEvent
        self.sequence = 0
        self.lock = Lock()
        self.closed = False

    def call(self, action, *, deviceTypeId, policies, **fields):
        budget = tcpActionBudget(deviceTypeId, action, fields['params'], policies)
        deadline = monotonic()+(budget['queueWaitMs']+budget['actionBudgetMs'])/1000
        def cancelled():
            return self.closed or self.cancelEvent.is_set()
        while not self.lock.acquire(timeout=.02):
            if cancelled() or monotonic() >= deadline:
                raise DeviceError('E_DEVICE_CANCELLED', 'TCP proxy queue cancelled')
        try:
            if cancelled():
                raise DeviceError('E_DEVICE_CANCELLED', 'TCP owner cancelled')
            self.sequence += 1
            request = dict(sequence=self.sequence, kind='device_action', action=action,
                           requestId=uuid4().hex, deadlineNs=int(deadline*1_000_000_000), **fields)
            raw = json.dumps(request, ensure_ascii=True).encode()
            if len(raw) > MAX_IPC_BYTES:
                raise DeviceError('E_TCP_MESSAGE_TOO_LARGE', 'TCP request exceeds bounded data bridge')
            try:
                sendBytes(self.connection, raw, cancelled, deadline)
                reply = json.loads(receiveBytes(self.connection, MAX_IPC_BYTES, cancelled, deadline+.25))
            except (OSError, EOFError, ValueError) as error:
                self.close()
                diagnostics = {}
                if deviceTypeId == 'device.tcp.server' and action == 'send':
                    import base64
                    from emo_master.core.contracts.tcp_service import TcpSendReceipt
                    ref = fields['arguments']['session']
                    receipt = TcpSendReceipt(deviceId=fields['deviceId'], requestId=request['requestId'],
                        jobId=ref['jobId'], channelId=ref['channelId'], serviceGeneration=ref['serviceGeneration'],
                        connectionGeneration=ref['connectionGeneration'], outcome='OUTCOME_UNKNOWN',
                        bytesRequested=len(base64.b64decode(fields['arguments']['dataBase64'])), bytesSent=0,
                        code='E_TCP_OUTCOME_UNKNOWN', message='回复不可用；禁止自动重发').toPayload()
                    diagnostics['tcpReceipt'] = receipt
                raise DeviceError('E_TCP_OUTCOME_UNKNOWN', 'TCP reply unavailable; do not replay business action',
                                  outcome='UNKNOWN', diagnostics=diagnostics) from error
            if not reply['ok']:
                raise DeviceError(reply['code'], reply['message'], outcome=reply['outcome'], diagnostics=reply['diagnostics'])
            return reply['result']
        finally:
            self.lock.release()

    def close(self):
        self.closed = True
        self.connection.close()
