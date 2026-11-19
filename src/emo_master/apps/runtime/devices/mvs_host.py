"""Persistent Hikrobot MVS acquisition inside an isolated camera process."""
from time import monotonic
from uuid import uuid4

from emo_master.core.contracts.device_driver import DeviceError
from .camera_host import ManagedCameraHost, runCameraHost


def _acquisitionSettings(params):
    settings = {"triggerMode": params["triggerMode"], "exposureMode": params["exposureMode"],
                "gainMode": params["gainMode"]}
    if params["triggerMode"] == "hardware":
        settings.update(triggerSource=params["triggerSource"], triggerActivation=params["triggerActivation"])
    if params["exposureMode"] == "manual":
        settings["exposureUs"] = params["exposureUs"]
    if params["gainMode"] == "manual":
        settings["gainDb"] = params["gainDb"]
    return settings


class MvsCapture:
    def __init__(self, connection, apiFactory=None):
        self.serialNumber = connection["serialNumber"]
        self.apiFactory = apiFactory
        self.session = None
        self.settings = None
        self.connectionGeneration = uuid4().hex
        self.streamGeneration = None
        self.quarantined = False

    def _check(self, cancelled, deadline):
        if self.quarantined:
            raise DeviceError("E_DEVICE_UNAVAILABLE", "MVS camera requires explicit recovery", unhealthy=True)
        if cancelled():
            raise DeviceError("E_DEVICE_CANCELLED", "MVS camera request cancelled", outcome="CANCELLED")
        if monotonic() >= deadline:
            raise DeviceError("E_DEVICE_OPERATION_TIMEOUT", "MVS camera operation deadline exceeded")

    def _quarantine(self, error, outcome, recoveryHint=""):
        self.quarantined = True
        message = str(error) + recoveryHint
        try:
            self.close()
        except Exception as closeError:
            message += f" (camera release failed: {closeError})"
            outcome = "UNKNOWN"
        raise DeviceError(getattr(error, "code", "E_CAMERA_IO"), message, outcome=outcome, unhealthy=True) from error

    def connect(self, params, cancelled, deadline):
        self._check(cancelled, deadline)
        if self.session is None:
            try:
                factory = self.apiFactory
                if factory is None:
                    from emo_master.plugins.builtins._hikrobot_mvs import MvsSession
                    factory = MvsSession
                self.session = factory(self.serialNumber)
                self.session.connect()
            except Exception as error:
                self._quarantine(error, "NOT_EXECUTED")
        return {"connectionGeneration": self.connectionGeneration}

    def _prepare(self, params, cancelled, deadline):
        self.connect(params, cancelled, deadline)
        settings = _acquisitionSettings(params)
        if self.settings != settings:
            if self.settings is not None and "hardware" in (self.settings["triggerMode"], settings["triggerMode"]):
                raise DeviceError("E_CAMERA_SYNC_REQUIRED",
                                  "stop external triggers and explicitly recover before changing MVS hardware acquisition settings")
            try:
                if self.settings is not None:
                    self.session.stop()
                self.session.start(params)
            except Exception as error:
                self._quarantine(error, "NOT_EXECUTED")
            self.settings = settings
            self.streamGeneration = uuid4().hex

    def capture(self, params, cancelled, deadline, capacity):
        def check():
            self._check(cancelled, deadline)

        previousStream = self.streamGeneration
        self._prepare(params, cancelled, deadline)
        enteredCapture = False
        frameReceived = False
        try:
            check()
            timeout = params["captureTimeoutMs"] or None
            if deadline != float("inf"):
                remaining = max(1, int((deadline - monotonic()) * 1000))
                timeout = min(timeout, remaining) if timeout is not None else remaining
            enteredCapture = True
            image, metadata = self.session.capture(params, check, capacity, timeout)
            frameReceived = True
            check()
        except Exception as error:
            code = getattr(error, "code", "E_CAMERA_IO")
            consumed = (frameReceived or getattr(error, "frameReceived", False)
                        or getattr(error, "softwareTriggerIssued", False))
            interrupted = code in {"E_CAMERA_TIMEOUT", "E_DEVICE_CANCELLED", "E_DEVICE_OPERATION_TIMEOUT"}
            if (interrupted and self.settings["triggerMode"] == "hardware"
                    and (enteredCapture or self.streamGeneration != previousStream)):
                # Once armed, a delayed external pulse can arrive after this
                # request or Job retires. Only synchronized recovery can admit
                # the next workpiece, even if the SDK has returned no frame.
                outcome = "CANCELLED" if code == "E_DEVICE_CANCELLED" and not consumed else "UNKNOWN"
                self._quarantine(error, outcome, "；硬触发等待已中断，请停止并核对外部触发后在设备页恢复此相机")
            if not consumed and (interrupted or code == "E_FRAME_CAPACITY"):
                raise DeviceError(code, str(error), outcome=getattr(error, "outcome", "NOT_EXECUTED")) from error
            # A failed native wait can lose an external or free-running frame
            # even when no frame buffer was returned to Python.
            uncertain = consumed or params["triggerMode"] != "software"
            self._quarantine(error, "UNKNOWN" if uncertain else "NOT_EXECUTED")
        return image, dict(metadata, sourceId="hikrobot-mvs:" + self.serialNumber,
                           connectionGeneration=self.connectionGeneration, streamGeneration=self.streamGeneration)

    def close(self):
        if self.session is not None:
            self.session.close()
            self.session = None
        self.settings = None


def runMvsHost(pipe, cancellation, connection, generation, apiFactory=None):
    runCameraHost(pipe, cancellation, generation, MvsCapture(connection, apiFactory))


class ManagedMvs(ManagedCameraHost):
    hostTarget = staticmethod(runMvsHost)
    hostName = "mvs-host-"

    @staticmethod
    def physicalIdentity(definition):
        serial = definition.connection["serialNumber"]
        if not serial.strip() or serial.strip() != serial or "\x00" in serial:
            raise ValueError("serialNumber must be an exact nonempty MVS serial number")
        return "hikrobot-mvs:" + serial
