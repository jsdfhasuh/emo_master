"""One bounded, non-retrying device channel per spawned Job."""
import json
from threading import Event, Lock, Thread
import time
from uuid import uuid4

from emo_master.apps.runtime.communication.pipe_io import sendBytes, receiveBytes
from .executor import DeviceError
from .frame_store import copyFrame


MAX_BYTES = 3 * 1024 * 1024


class DeviceBridge:
    def __init__(self, manager, owner, connection, cancellation):
        self.manager, self.owner, self.connection, self.cancellation = manager, owner, connection, cancellation
        self.stopped = Event()
        self.thread = Thread(target=self.run, name="device-ipc-" + owner, daemon=True)
        self.sequence = 0

    def cancelled(self):
        return self.stopped.is_set() or self.cancellation.is_set()

    def run(self):
        try:
            while not self.cancelled():
                if not self.connection.poll(.02):
                    continue
                raw = receiveBytes(self.connection, MAX_BYTES, self.cancelled, time.monotonic() + 2)
                try:
                    request = json.loads(raw)
                    sequence = request["sequence"]
                    if type(sequence) is not int or sequence != self.sequence + 1:
                        raise DeviceError("E_DEVICE_IPC_SEQUENCE", "device request is duplicate or out of order")
                    self.sequence = sequence
                    kind = request.get('kind')
                    if kind == 'device_action':
                        result = self.manager.execute(self.owner, request['deviceId'], request['action'],
                            request['params'], cycleId=request['cycleId'], arguments=request.get('arguments'),
                            cancelled=self.cancelled, source=request.get('source'), selectors=request.get('selectors'),
                            requestId=request['requestId'])
                    elif kind != 'framework_operation':
                        raise DeviceError('E_DEVICE_IPC_KIND', 'unknown device request kind')
                    elif request["action"].startswith("dispatch"):
                        group = self.manager.dispatchGroups.get(self.owner)
                        if group is None:
                            raise DeviceError("E_DISPATCH_UNAVAILABLE", "Job has no run group")
                        result = group.handle(self.owner, request["action"], request)
                    elif request["action"] == "frameBeginWrite":
                        revision = self.manager.configuration.requireCycle(self.owner, request["cycleId"])
                        result = self.manager.frames.beginWrite(self.owner, request["shape"], request["cycleId"], revision)
                    elif request["action"] == "frameCommitWrite":
                        result = self.manager.frames.commitWrite(self.owner, request["frameId"])
                    elif request["action"] == "beginCycle":
                        result = self.manager.configuration.beginCycle(self.owner, request["knownRevision"], self.cancelled,
                            parentCycleId=request.get("parentCycleId"))
                        result['deviceScope'] = dict(self.manager.scope)
                    elif request["action"] == "endCycle":
                        self.manager.waitOwner(self.owner, request['cycleId'])
                        self.manager.configuration.endCycle(self.owner, request["cycleId"])
                        group = self.manager.dispatchGroups.get(self.owner)
                        if group is not None:
                            group.endCycle(self.owner, request["cycleId"])
                            with self.manager.frames.lock:
                                for key, frame in list(self.manager.frames.frames.items()):
                                    if frame.descriptor["cycleId"] == request["cycleId"]:
                                        self.manager.frames.release(key, self.owner)
                        result = None
                    elif request["action"] == "release":
                        self.manager.frames.release(request["frameId"], self.owner)
                        result = None
                    else:
                        raise DeviceError('E_DEVICE_IPC_OPERATION', 'unknown framework operation')
                    reply = dict(ok=True, result=result)
                except Exception as error:
                    reply = dict(ok=False, code=getattr(error, "code", "E_DEVICE_IPC"), message=str(error),
                                 outcome=getattr(error, "outcome", "UNKNOWN"), diagnostics=getattr(error, "diagnostics", {}))
                sendBytes(self.connection, json.dumps(reply).encode(), self.cancelled, time.monotonic() + 2)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            self.stopped.set()
            self.connection.close()

    def close(self):
        self.stopped.set()
        self.thread.join(3)
        if self.thread.is_alive():
            raise DeviceError("E_DEVICE_RELEASE_PENDING", "device IPC bridge is still exiting")


class DeviceProxy:
    def __init__(self, connection, cancellation):
        self.connection, self.cancellation = connection, cancellation
        self.sequence = 0
        self.lock = Lock()
        self.closed = False
        self.retainFrames = False
        self.frameCopies = {}

    def call(self, action, **fields):
        return self._call('device_action', action, **fields)

    def framework(self, action, **fields):
        return self._call('framework_operation', action, **fields)

    def _call(self, kind, action, **fields):
        def cancelled():
            return self.closed or self.cancellation.is_set()
        while not self.lock.acquire(timeout=.02):
            if cancelled():
                raise DeviceError("E_DEVICE_CANCELLED", "device proxy cancelled")
        try:
            if cancelled():
                raise DeviceError("E_DEVICE_CANCELLED", "device proxy cancelled")
            self.sequence += 1
            raw = json.dumps(dict(sequence=self.sequence, kind=kind, action=action, requestId=uuid4().hex, **fields)).encode()
            if len(raw) > MAX_BYTES:
                raise DeviceError("E_DEVICE_IPC_SIZE", "device request exceeds IPC budget")
            try:
                deadline = time.monotonic() + 1202
                if kind == 'device_action' and action == "capture" and fields.get("params", {}).get("captureTimeoutMs") == 0:
                    # Runtime validates the plugin's cancellable-wait declaration.
                    # EOF and cancellation still retire this non-retrying call.
                    deadline = float("inf")
                sendBytes(self.connection, raw, cancelled, time.monotonic() + 2)
                reply = json.loads(receiveBytes(self.connection, MAX_BYTES, cancelled, deadline))
            except (OSError, EOFError, ValueError) as error:
                self.close()
                raise DeviceError("E_DEVICE_OUTCOME_UNKNOWN", "device transport interrupted; do not retry",
                                  outcome="UNKNOWN") from error
            if not reply["ok"]:
                raise DeviceError(reply["code"], reply["message"], outcome=reply["outcome"], diagnostics=reply.get("diagnostics", {}))
            if kind == 'framework_operation' and action == "endCycle":
                self.frameCopies = {key: item for key, item in self.frameCopies.items()
                                    if item[0]() is not None and item[1]["cycleId"] != fields["cycleId"]}
            return reply["result"]
        finally:
            self.lock.release()

    def capture(self, deviceId, params, cycleId, *, source=None, selectors=None):
        descriptor = self.call("capture", deviceId=deviceId, params=params, cycleId=cycleId, source=source, selectors=selectors)
        image = copyFrame(descriptor)
        if self.retainFrames:
            self.rememberFrame(image, descriptor)
        else:
            self.framework("release", frameId=descriptor["frameId"])
        return image, descriptor

    def rememberFrame(self, image, descriptor):
        import weakref
        self.frameCopies[id(image)] = (weakref.ref(image), descriptor)

    def encode(self, value, context):
        import numpy as np
        from multiprocessing.shared_memory import SharedMemory
        if isinstance(value, np.ndarray):
            if value.dtype != np.uint8:
                raise DeviceError("E_FRAME_FORMAT", "Dispatch images must use uint8")
            known = self.frameCopies.get(id(value))
            descriptor = None
            if known is not None and known[0]() is value:
                candidate = known[1]
                if candidate["cycleId"] == context.cycleId and candidate["planRevision"] == context.planRevision:
                    # Untrusted operators may modify their isolated ndarray in place.
                    if np.array_equal(value, copyFrame(candidate)):
                        descriptor = candidate
            if descriptor is None:
                descriptor = self.framework("frameBeginWrite", shape=list(value.shape), cycleId=context.cycleId)
                memory = SharedMemory(name=descriptor["name"])
                try:
                    destination = np.ndarray(value.shape, dtype=np.uint8, buffer=memory.buf)
                    np.copyto(destination, value)
                    del destination
                finally:
                    memory.close()
                self.framework("frameCommitWrite", frameId=descriptor["frameId"])
                self.rememberFrame(value, descriptor)
            return dict(kind="frame", value=descriptor)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Dispatch object keys must be strings")
            return dict(kind="dict", value={k: self.encode(v, context) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return dict(kind="list", value=[self.encode(v, context) for v in value])
        json.dumps(value, allow_nan=False)
        return dict(kind="value", value=value)

    def decode(self, value):
        if value["kind"] == "frame":
            image = copyFrame(value["value"])
            self.rememberFrame(image, value["value"])
            return image
        if value["kind"] == "dict":
            return {k: self.decode(v) for k, v in value["value"].items()}
        if value["kind"] == "list":
            return [self.decode(v) for v in value["value"]]
        if value["kind"] != "value":
            raise ValueError("invalid Dispatch value kind")
        return value["value"]

    def close(self):
        self.closed = True
        self.connection.close()
