"""Explicit Runtime-host discovery and audited, non-replayed GigE ForceIP."""
from copy import deepcopy
import ipaddress
import json
import multiprocessing
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from emo_master.apps.runtime.communication.pipe_io import receiveBytes, sendBytes
from emo_master.apps.runtime.context.sqlite_transactions import beginImmediate
from emo_master.core.project.configuration import canonical
from emo_master.core.project.snapshots import revisionOf
from .executor import DeviceError

MAX_REPLY = 512 * 1024


def networkAddress(payload):
    result = {}
    for key in ('ipAddress', 'subnetMask', 'defaultGateWay'):
        if type(payload.get(key)) is not str or len(payload[key]) > 15:
            raise ValueError('IPv4 fields must be bounded address strings')
        result[key] = str(ipaddress.IPv4Address(payload[key]))
    subnet = ipaddress.IPv4Network(result['ipAddress'] + '/' + result['subnetMask'], strict=False)
    address = ipaddress.IPv4Address(result['ipAddress'])
    gateway = ipaddress.IPv4Address(result['defaultGateWay'])
    if result['subnetMask'] != str(subnet.netmask):
        raise ValueError('subnet mask must be a contiguous netmask, not a host mask')
    if address.is_multicast or address.is_unspecified or address.is_loopback or address.is_reserved or address in (subnet.network_address, subnet.broadcast_address):
        raise ValueError('device address must be a usable unicast IPv4 host')
    if not gateway.is_unspecified and (gateway not in subnet or gateway in (address, subnet.network_address, subnet.broadcast_address)):
        raise ValueError('gateway must be zero or a usable host in the selected subnet')
    return result


def executeNetwork(api, action, payload):
    rows = api.enumerate_device_info()
    if action == 'discover':
        return dict(devices=rows, sdkVersion=api.version(), outcome='COMPLETED')
    matches = [row for row in rows if row.get('cameraKey') == payload['cameraKey']]
    if len(matches) != 1 or matches[0].get('cameraType') != 0:
        raise DeviceError('E_NETWORK_IDENTITY', 'selected GigE identity is no longer unique')
    before = matches[0]
    if any(row.get('interfaceIp') == payload['ipAddress'] for row in rows) or any(
            row.get('cameraKey') != payload['cameraKey'] and row.get('ipAddress') == payload['ipAddress'] for row in rows):
        raise DeviceError('E_NETWORK_ADDRESS_CONFLICT', 'address is already used by the Runtime interface or another discovered device')
    if any(before.get(key) != payload['before'].get(key) for key in ('macAddress', 'ipAddress', 'subnetMask', 'defaultGateWay')):
        raise DeviceError('E_NETWORK_STALE', 'hardware network identity changed; discover again')
    code, handle = api.create_handle(1, payload['cameraKey'])
    if code:
        raise DeviceError('E_NETWORK_HANDLE', 'cannot create the selected camera handle')
    try:
        values = networkAddress(payload)
        code = api.force_ip_address(handle, values['ipAddress'], values['subnetMask'], values['defaultGateWay'])
        if code:
            raise DeviceError('E_NETWORK_OUTCOME_UNKNOWN', f'ForceIP returned {code}; verify hardware before another command', outcome='UNKNOWN')
    finally:
        if api.destroy_handle(handle):
            raise DeviceError('E_NETWORK_OUTCOME_UNKNOWN', 'camera handle release failed; verify hardware before another command', outcome='UNKNOWN')
    after = [row for row in api.enumerate_device_info() if row.get('cameraKey') == payload['cameraKey']
             and row.get('macAddress') == before['macAddress']]
    if len(after) != 1 or any(after[0].get(key) != value for key, value in values.items()):
        raise DeviceError('E_NETWORK_OUTCOME_UNKNOWN', 'ForceIP sent but readback did not confirm the requested address', outcome='UNKNOWN')
    return dict(outcome='COMPLETED', before=before, after=after[0], persistence='SDK_FORCE_IP_ONLY')


def networkHost(writer, action, payload, apiFactory=None):
    try:
        from emo_master.plugins.builtins._huaray_imv import ImvApi
        result = dict(ok=True, result=executeNetwork((apiFactory or ImvApi)(), action, payload))
    except Exception as error:
        result = dict(ok=False, code=getattr(error, 'code', 'E_NETWORK_HOST'), message=str(error),
                      outcome=getattr(error, 'outcome', 'UNKNOWN' if action == 'force_ip' else 'NOT_EXECUTED'))
    try:
        sendBytes(writer, json.dumps(result).encode(), lambda: False, monotonic() + 2)
    finally:
        writer.close()


def runNetworkHost(action, payload, cancelled, *, apiFactory=None):
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(False)
    process = context.Process(target=networkHost, args=(writer, action, payload, apiFactory), name='device-network-host', daemon=True)
    try:
        process.start()
        writer.close()
        reply = json.loads(receiveBytes(reader, MAX_REPLY, cancelled, monotonic() + 20))
        if not reply['ok']:
            raise DeviceError(reply['code'], reply['message'], outcome=reply['outcome'])
        return reply['result']
    finally:
        reader.close()
        writer.close()
        if process.pid:
            process.join(2)
            if process.is_alive():
                process.terminate()
                process.join(5)
            if process.is_alive():
                error = DeviceError('E_DEVICE_RELEASE_PENDING', 'network Host did not exit', outcome='UNKNOWN')
                error.process = process
                raise error
            process.close()


class DeviceNetwork:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock, self.closing = Lock(), Event()
        self.discovery = None
        self.hosts, self.held = [], {}
        self.runner = runNetworkHost
        with runtime.sqliteStore.connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS deviceNetworkReceipts (
                projectId TEXT NOT NULL, requestId TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(projectId, requestId))''')

    def close(self):
        self.closing.set()
        if not self.lock.acquire(timeout=10):
            raise DeviceError('E_DEVICE_RELEASE_PENDING', 'network operation is still exiting', outcome='UNKNOWN')
        try:
            for process in list(self.hosts):
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                if process.is_alive():
                    raise DeviceError('E_DEVICE_RELEASE_PENDING', 'network Host still owns hardware', outcome='UNKNOWN')
                process.close()
                self.hosts.remove(process)
            for owner, (manager, executor, lifecycle) in list(self.held.items()):
                self._release(owner, manager, executor, lifecycle)
                del self.held[owner]
        finally:
            self.lock.release()

    @staticmethod
    def _release(owner, manager, executor, lifecycle):
        with manager.lock, executor.condition:
            lifecycle.controlPending = False
            manager.revoke(owner)
            manager.maintenanceOwners.discard(owner)

    def execute(self, request):
        runtime = self.runtime
        if request.runtime_instance_id != runtime.runtimeInstanceId or request.project_id != runtime.loadedProjectId:
            raise DeviceError('E_NETWORK_IDENTITY', 'Runtime or project identity changed')
        if request.action == 'status':
            return self._receipt(request.project_id, request.request_id)[1]
        if not runtime._runtimeDataLockIsPrimary:
            raise DeviceError('E_RUNTIME_PRIMARY_REQUIRED', 'network actions require the primary Runtime owner')
        runtime.jobSupervisor.assertMutationAllowed()
        if self.closing.is_set() or runtime._closing:
            raise DeviceError('E_RUNTIME_CLOSING', 'Runtime is closing')
        if not self.lock.acquire(blocking=False):
            raise DeviceError('E_DEVICE_BUSY', 'another discovery or network command is pending')
        try:
            if self.hosts:
                raise DeviceError('E_DEVICE_RELEASE_PENDING', 'previous network Host has not retired; close Runtime')
            if request.action == 'discover':
                self.discovery = None
                result = self.runner('discover', {}, self.closing.is_set)
                if request.project_id != runtime.loadedProjectId or self.closing.is_set() or runtime._closing:
                    raise DeviceError('E_NETWORK_STALE', 'project changed or Runtime closed during discovery')
                result.update(discoveryId=uuid4().hex, runtimeInstanceId=runtime.runtimeInstanceId, projectId=runtime.loadedProjectId)
                self.discovery = (monotonic(), deepcopy(result))
                return result
            if request.action != 'force_ip':
                raise ValueError('unsupported device network action')
            if len(request.payload_json.encode()) > 8192 or not 1 <= len(request.request_id) <= 160:
                raise ValueError('network command identity or payload exceeds limits')
            payload = json.loads(request.payload_json)
            if set(payload) != {'discoveryId', 'cameraKey', 'ipAddress', 'subnetMask', 'defaultGateWay',
                                'expectedGeneration', 'expectedDefinitionRevision', 'confirmed'} or payload['confirmed'] is not True:
                raise ValueError('network change requires explicit confirmation and exact fields')
            values = networkAddress(payload)
            fingerprint = canonical(dict(deviceId=request.device_id, payload=payload))
            existing = self._receipt(request.project_id, request.request_id, missing=True)
            if existing:
                if existing[0] != fingerprint:
                    raise ValueError('network request ID was reused with different content')
                return existing[1]
            if self.discovery is None or monotonic() - self.discovery[0] > 30 or payload['discoveryId'] != self.discovery[1]['discoveryId']:
                raise DeviceError('E_NETWORK_STALE', 'discovery expired; discover again before changing hardware')
            if self.discovery[1]['projectId'] != request.project_id:
                raise DeviceError('E_NETWORK_STALE', 'discovery belongs to a different project')
            rows = [row for row in self.discovery[1]['devices'] if row.get('cameraKey') == payload['cameraKey'] and row.get('cameraType') == 0]
            if len(rows) != 1:
                raise DeviceError('E_NETWORK_IDENTITY', 'select exactly one discovered GigE device')
            owner = 'network-' + uuid4().hex
            manager = runtime.deviceManager
            with runtime._projectStateLock, manager.lock:
                if request.project_id != runtime.loadedProjectId:
                    raise DeviceError('E_NETWORK_IDENTITY', 'project changed')
                definition = manager.definitions.get(request.device_id)
                executor = manager.executors.get(request.device_id)
                lifecycle = manager.lifecycles.get(request.device_id)
                if (definition is None or executor is None or lifecycle is None
                        or definition.deviceTypeId != 'device.camera.huaray_imv'
                        or definition.connection['cameraKey'] != payload['cameraKey']
                        or executor.generation != payload['expectedGeneration']
                        or revisionOf(definition.model_dump()) != payload['expectedDefinitionRevision']):
                    raise DeviceError('E_NETWORK_IDENTITY', 'selected online device definition changed')
                with executor.condition:
                    if (not lifecycle.paused or lifecycle.connected or lifecycle.controlPending or executor.active or executor.queue
                            or any(request.device_id in ids for ids in manager.permissions.values())):
                        raise DeviceError('E_DEVICE_BUSY', 'stop affected users and explicitly disconnect/pause this device first')
                    manager.authorizeMaintenance(owner, request.device_id)
                    lifecycle.controlPending = True
            try:
                initial = dict(state='UNKNOWN', outcome='UNKNOWN', requestId=request.request_id,
                    runtimeInstanceId=runtime.runtimeInstanceId, message='command admitted; no confirmed receipt yet')
                with runtime.sqliteStore.connection() as connection:
                    beginImmediate(connection)
                    if connection.execute('SELECT count(*) FROM deviceNetworkReceipts').fetchone()[0] >= 4096:
                        raise DeviceError('E_NETWORK_CAPACITY', 'network audit capacity exhausted')
                    connection.execute('INSERT INTO deviceNetworkReceipts VALUES (?,?,?,?)',
                        (request.project_id, request.request_id, fingerprint, canonical(initial)))
                try:
                    result = self.runner('force_ip', dict(values, cameraKey=payload['cameraKey'], before=rows[0]), self.closing.is_set)
                    result.update(state='COMPLETED', requestId=request.request_id, runtimeInstanceId=runtime.runtimeInstanceId)
                except Exception as error:
                    if getattr(error, 'process', None) is not None:
                        self.hosts.append(error.process)
                        self.held[owner] = (manager, executor, lifecycle)
                    result = dict(initial, code=getattr(error, 'code', 'E_NETWORK_OUTCOME_UNKNOWN'), message=str(error),
                                  outcome=getattr(error, 'outcome', 'UNKNOWN'))
                    result['state'] = 'NOT_EXECUTED' if result['outcome'] == 'NOT_EXECUTED' else 'UNKNOWN'
                with runtime.sqliteStore.connection() as connection:
                    connection.execute('UPDATE deviceNetworkReceipts SET result=? WHERE projectId=? AND requestId=?',
                        (canonical(result), request.project_id, request.request_id))
                self.discovery = None
                return result
            finally:
                if owner not in self.held:
                    self._release(owner, manager, executor, lifecycle)
        except Exception as error:
            if getattr(error, 'process', None) is not None:
                self.hosts.append(error.process)
            raise
        finally:
            self.lock.release()

    def _receipt(self, projectId, requestId, *, missing=False):
        with self.runtime.sqliteStore.connection() as connection:
            row = connection.execute('SELECT fingerprint,result FROM deviceNetworkReceipts WHERE projectId=? AND requestId=?',
                                     (projectId, requestId)).fetchone()
        if row is None:
            if missing:
                return None
            raise ValueError('network receipt not found')
        return row[0], json.loads(row[1])
