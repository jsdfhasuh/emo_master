"""Device management RPCs shared by Designer and operator clients, without Qt."""
import json
from threading import RLock

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.clients.runtime.calls import DisplayCallContext, DisplayCallError


class DeviceClientError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class DeviceManagementClient:
    def __init__(self, service, *, call=None):
        self.service, self.call = service, call
        self.lock = RLock()
        self.contexts = set()
        self.closed = False
        self.scope = {}

    def _call(self, name, request, cancellation=None, *, timeoutMs=5000):
        context = cancellation or DisplayCallContext()
        with self.lock:
            if self.closed:
                raise DeviceClientError('E_DISPLAY_CANCELLED', '设备观察已关闭')
            self.contexts.add(context)
        try:
            context.start(timeoutMs)
            if self.call is not None:
                return self.call(name, request, timeoutMs=timeoutMs, cancellation=context)
            method = getattr(self.service, name)
            if callable(getattr(method, 'future', None)):
                task = method.future(request, timeout=context.time_remaining(), wait_for_ready=False)
                context.attach(task)
                reply = task.result()
            else:
                reply = method(request, context)
            context.check()
            return reply
        except grpc.RpcError as error:
            raise DeviceClientError(str(error.code()), error.details() or '设备 RPC 失败') from error
        except grpc.FutureCancelledError as error:
            raise DeviceClientError('E_DISPLAY_CANCELLED', '设备操作已取消') from error
        except DisplayCallError as error:
            raise DeviceClientError(error.code, str(error)) from error
        finally:
            with self.lock:
                self.contexts.discard(context)

    def listDeviceTypes(self):
        from emo_master.core.plugin.devices import DeviceRequirement, DeviceType
        reply = self._call('ListDeviceTypes', pb.ListDeviceTypesRequest())
        return ({key: DeviceType.model_validate(value) for key, value in json.loads(reply.types_json).items()},
                {key: {slot: DeviceRequirement.model_validate(value) for slot, value in slots.items()}
                 for key, slots in json.loads(reply.requirements_json).items()}, json.loads(reply.rejected_json))

    def _states(self, reply):
        if not reply.ok:
            raise DeviceClientError(reply.code or 'E_DEVICE_STATE', reply.message)
        self.scope = dict(runtimeInstanceId=reply.runtime_instance_id, projectId=reply.project_id,
                          projectLoadGeneration=reply.project_load_generation)
        return reply.runtime_instance_id, json.loads(reply.states_json)

    def getDeviceStates(self, projectId, *, cancellation=None):
        return self._states(self._call('GetDeviceStates', pb.GetDeviceStatesRequest(project_id=projectId), cancellation))

    def getDeviceOverview(self, projectId, *, cancellation=None):
        reply = self._call('GetDeviceStates', pb.GetDeviceStatesRequest(project_id=projectId), cancellation)
        return dict(runtimeInstanceId=reply.runtime_instance_id, projectId=reply.project_id,
                    available=reply.ok, states=json.loads(reply.states_json or '{}') if reply.ok else {},
                    message=reply.message, code=reply.code,
                    uptimeMs=reply.runtime_uptime_ms if reply.HasField('runtime_uptime_ms') else None)

    def controlDevice(self, projectId, runtimeInstanceId, deviceId, state, action, *, confirmed=False):
        return self._states(self._call('ControlDevice', pb.ControlDeviceRequest(project_id=projectId,
            runtime_instance_id=runtimeInstanceId, device_id=deviceId, expected_generation=state['generation'],
            expected_definition_revision=state['definitionRevision'], expected_control_revision=state['controlRevision'],
            action=action, confirmed=confirmed), timeoutMs=self._budget(state, 'controls', action)))

    def recoverDevice(self, projectId, runtimeInstanceId, deviceId, state, *, hardwareSynchronized=False):
        return self._states(self._call('RecoverDevice', pb.RecoverDeviceRequest(project_id=projectId,
            runtime_instance_id=runtimeInstanceId, device_id=deviceId, expected_generation=state['generation'],
            expected_definition_revision=state['definitionRevision'], hardware_synchronized=hardwareSynchronized),
            timeoutMs=self._budget(state, 'controls', 'recover')))

    @staticmethod
    def _budget(state, group, action):
        return int(state.get('rpcBudgetsMs', {}).get(group, {}).get(action, 5000)) + 2000

    def executeDeviceAction(self, projectId, runtimeInstanceId, deviceId, state, action, arguments):
        from uuid import uuid4
        reply = self._call('ExecuteDeviceAction', pb.ExecuteDeviceActionRequest(project_id=projectId,
            runtime_instance_id=runtimeInstanceId, device_id=deviceId, expected_generation=state['generation'],
            expected_definition_revision=state['definitionRevision'], expected_control_revision=state['controlRevision'],
            action=action, arguments_json=json.dumps(arguments), confirmed=True, request_id=uuid4().hex),
            timeoutMs=self._budget(state, 'actions', action))
        if not reply.ok:
            error = DeviceClientError(reply.code, reply.message)
            error.diagnostics = json.loads(reply.result_json or '{}')
            raise error
        return json.loads(reply.result_json)

    def getDeviceDiagnostics(self, scope, deviceId, cursor=0, *, cancellation=None):
        reply = self._call('GetDeviceDiagnostics', pb.GetDeviceDiagnosticsRequest(
            runtime_instance_id=scope['runtimeInstanceId'], project_id=scope['projectId'],
            project_load_generation=scope['projectLoadGeneration'], device_id=deviceId, cursor=cursor), cancellation)
        if not reply.ok:
            raise DeviceClientError(reply.code, reply.message)
        return json.loads(reply.diagnostics_json)

    def close(self):
        with self.lock:
            self.closed = True
            contexts = list(self.contexts)
        for context in contexts:
            context.cancel()
