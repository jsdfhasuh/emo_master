"""Lazy per-camera spawn Host. Only the admitted request crosses this pipe."""
import json
import math
import multiprocessing
from multiprocessing.shared_memory import SharedMemory
from time import monotonic
from uuid import uuid4

import numpy as np

from emo_master.apps.runtime.communication.pipe_io import receiveBytes, sendBytes
from emo_master.core.contracts.device_driver import DeviceDriver
from .executor import DeviceError


MAX_MESSAGE = 64 * 1024


def runImvHost(pipe, cancellation, connection, generation, apiFactory=None):
    from .imv_capture import ImvCapture
    camera = ImvCapture(connection, apiFactory)
    sequence = 0
    try:
        while True:
            if not pipe.poll(.1):
                continue
            request = json.loads(receiveBytes(pipe, MAX_MESSAGE, lambda: False, monotonic() + 2))
            if request.get("generation") != generation or request.get("sequence") != sequence + 1:
                return
            sequence += 1
            if request["action"] == "close":
                break
            memory = None
            try:
                deadline = float("inf") if request["budget"] is None else monotonic() + request["budget"]
                if request["budget"] is None and (request["action"] != "capture"
                        or request["params"].get("triggerMode") != "hardware"
                        or request["params"].get("captureTimeoutMs") != 0):
                    raise DeviceError("E_DEVICE_BUDGET", "unlimited Host budget requires cancellable hardware capture")
                if request["action"] == "connect":
                    reply = dict(ok=True, metadata=camera.connect(request["params"], cancellation.is_set, deadline))
                elif request['action'] == 'read_parameters':
                    reply = dict(ok=True, metadata=camera.readParameters(request['params'], cancellation.is_set, deadline))
                elif request["action"] in {"capture", "trial_capture"}:
                    memory = SharedMemory(name=request["memory"])
                    if request['action'] == 'trial_capture':
                        image, metadata = camera.trialCapture(request['params'], request['changes'], cancellation.is_set, deadline, memory.size)
                        metadata['restored'] = True
                    else:
                        image, metadata = camera.capture(request["params"], cancellation.is_set, deadline, memory.size)
                    if image.nbytes > memory.size:
                        raise DeviceError("E_FRAME_CAPACITY", "SDK frame exceeds Host memory", unhealthy=True)
                    target = np.ndarray(image.shape, dtype=np.uint8, buffer=memory.buf)
                    np.copyto(target, image)
                    del target
                    reply = dict(ok=True, shape=list(image.shape), metadata=metadata)
                else:
                    raise DeviceError("E_DEVICE_ACTION", "unsupported camera Host action")
            except Exception as error:
                reply = dict(ok=False, code=getattr(error, "code", "E_CAMERA_HOST"), message=str(error),
                             outcome=getattr(error, "outcome", "UNKNOWN"), unhealthy=getattr(error, "unhealthy", True))
            finally:
                if memory is not None:
                    memory.close()
            reply.update(generation=generation, sequence=sequence)
            sendBytes(pipe, json.dumps(reply).encode(), lambda: False, monotonic() + 2)
    except (EOFError, OSError, ValueError):
        pass
    finally:
        try:
            camera.close()
        finally:
            pipe.close()


class ManagedImv(DeviceDriver):
    def __init__(self, definition, *, apiFactory=None):
        self.definition = definition
        self.apiFactory = apiFactory
        self.process = None
        self.pipe = None
        self.cancellation = None
        self.generation = uuid4().hex
        self.sequence = 0
        self.memory = None
        self.unhealthy = False
        self.connectionGeneration = None
        self.hardwareStream = False
        self.resourceLeasePath = None

    @staticmethod
    def physicalIdentity(definition):
        # A stable, single selector avoids IP/user-name/index aliases creating
        # several Runtime owners for the same camera.
        key = definition.connection["cameraKey"]
        if not key.strip() or key != key.strip() or len(key.encode("utf-8")) > 255 or "\x00" in key:
            raise ValueError("cameraKey must be an exact nonempty SDK device key within 255 UTF-8 bytes")
        return "huaray-imv:" + key

    def frameBytes(self, params):
        return self.definition.connection["maxFrameBytes"]

    def _start(self):
        if self.process is not None:
            if not self.process.is_alive():
                self.unhealthy = True
                raise DeviceError("E_CAMERA_HOST_EXITED", "camera Host exited; explicit recovery required", unhealthy=True)
            return
        context = multiprocessing.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.cancellation = context.Event()
        target = runImvHost
        args = (child, self.cancellation, self.definition.connection, self.generation, self.apiFactory)
        if self.resourceLeasePath:
            from emo_master.apps.runtime.project_config.resource_lease import runWithResourceLease
            target, args = runWithResourceLease, (self.resourceLeasePath, target, args)
        self.process = context.Process(target=target, args=args, name="imv-host-" + self.generation, daemon=True)
        try:
            self.process.start()
        except BaseException:
            self.pipe.close()
            self.pipe = self.process = None
            raise
        finally:
            child.close()

    def execute(self, action, params, cancelled, deadline, *, changes=None, arguments=None, operationContext=None):
        if action not in {"capture", "connect", "read_parameters", "trial_capture"}:
            raise DeviceError("E_DEVICE_ACTION", "unsupported camera action")
        if self.unhealthy:
            raise DeviceError("E_DEVICE_UNAVAILABLE", "camera requires explicit recovery", unhealthy=True)
        if cancelled() or monotonic() >= deadline:
            raise DeviceError("E_DEVICE_CANCELLED", "camera request cancelled before dispatch")
        self._start()
        self.cancellation.clear()
        self.memory = SharedMemory(create=True, size=self.frameBytes(params)) if action in {"capture", "trial_capture"} else None
        self.sequence += 1
        request = dict(action=action, params=params, memory=self.memory.name if self.memory else None, generation=self.generation,
                       changes=changes, sequence=self.sequence, budget=None if math.isinf(deadline) else max(.001, deadline - monotonic()))
        try:
            sendBytes(self.pipe, json.dumps(request, allow_nan=False).encode(), cancelled, min(deadline, monotonic() + 2))
            interrupted = None
            while not self.pipe.poll(.01):
                if not self.process.is_alive():
                    raise DeviceError("E_CAMERA_HOST_EXITED", "camera Host died during acquisition", outcome="UNKNOWN", unhealthy=True)
                if cancelled() or monotonic() >= deadline:
                    self.cancellation.set()
                    if interrupted is None:
                        interrupted = monotonic()
                    if monotonic() - interrupted > 2:
                        raise DeviceError("E_CAMERA_HOST_STUCK", "SDK did not exit after cancellation", outcome="UNKNOWN", unhealthy=True)
            reply = json.loads(receiveBytes(self.pipe, MAX_MESSAGE, lambda: False, monotonic() + 2))
            if reply.get("generation") != self.generation or reply.get("sequence") != self.sequence:
                raise DeviceError("E_CAMERA_HOST_FENCE", "stale camera Host response", outcome="UNKNOWN", unhealthy=True)
            if not reply["ok"]:
                if action == "capture" and params["triggerMode"] == "hardware" and not reply["unhealthy"]:
                    self.hardwareStream = True
                raise DeviceError(reply["code"], reply["message"], outcome=reply["outcome"], unhealthy=reply["unhealthy"])
            if cancelled() or monotonic() >= deadline:
                raise DeviceError("E_DEVICE_CANCELLED", "discarded late camera frame", outcome="UNKNOWN", unhealthy=True)
            self.connectionGeneration = reply["metadata"]["connectionGeneration"]
            if action in {"connect", "read_parameters"}:
                return reply["metadata"]
            self.hardwareStream = params["triggerMode"] == "hardware"
            shape = reply["shape"]
            if (not isinstance(shape, list) or len(shape) not in (2, 3)
                    or any(type(value) is not int or value <= 0 for value in shape)
                    or len(shape) == 3 and shape[2] != 3 or math.prod(shape) > self.memory.size):
                raise DeviceError("E_CAMERA_HOST_FRAME", "invalid Host frame descriptor", unhealthy=True)
            image = np.ndarray(tuple(shape), dtype=np.uint8, buffer=self.memory.buf).copy()
            return image, reply["metadata"]
        except BaseException as error:
            if not isinstance(error, DeviceError) or error.unhealthy:
                self.unhealthy = True
                self._stop(force=True)
            if isinstance(error, DeviceError):
                raise
            raise DeviceError("E_CAMERA_HOST_TRANSPORT", str(error), outcome="UNKNOWN", unhealthy=True) from error
        finally:
            # A live SDK/Host must never retain a mapping whose owner has retired.
            # Healthy replies are sent only after the Host closes its mapping.
            if self.memory is not None and (not self.unhealthy or self.process is None):
                self.memory.close()
                self.memory.unlink()
                self.memory = None

    def _stop(self, *, force=False):
        process = self.process
        if process is None:
            return
        self.cancellation.set()
        if not force and process.is_alive():
            self.sequence += 1
            try:
                sendBytes(self.pipe, json.dumps(dict(action="close", generation=self.generation,
                    sequence=self.sequence)).encode(), lambda: False, monotonic() + 1)
            except (OSError, ValueError):
                force = True
            process.join(2)
        if process.is_alive():
            process.terminate()
            process.join(2)
        if process.is_alive():
            raise DeviceError("E_DEVICE_RELEASE_PENDING", "camera Host has not exited", outcome="UNKNOWN", unhealthy=True)
        exitCode = process.exitcode
        self.pipe.close()
        process.close()
        self.pipe = self.process = None
        if not force and exitCode != 0:
            self.unhealthy = True
            raise DeviceError("E_CAMERA_HOST_CLEANUP", "camera Host cleanup failed: exit=" + str(exitCode),
                              outcome="UNKNOWN", unhealthy=True)

    def close(self, deadline=None):
        self._stop()
        if self.memory is not None:
            self.memory.close()
            self.memory.unlink()
            self.memory = None
        self.connectionGeneration = None
        self.hardwareStream = False
        self.generation = uuid4().hex
        self.sequence = 0

    def connect(self, params, cancelled, deadline):
        if self.connectionGeneration is None:
            return self.execute("connect", params, cancelled, deadline)

    def holdsConnection(self):
        return self.hardwareStream

    def snapshot(self):
        return dict(ready=self.connectionGeneration is not None and not self.unhealthy, ownsResources=self.process is not None, connectionState="QUARANTINED" if self.unhealthy else
                    "CONNECTED" if self.connectionGeneration is not None else "DISCONNECTED",
                    connectionGeneration=self.connectionGeneration, hostGeneration=self.generation)
