"""IMV stream state machine. Every method runs on the Host's one SDK thread."""
from copy import deepcopy
from time import monotonic
from uuid import uuid4

from emo_master.plugins.builtins._huaray_imv import CameraSession, HuarayCameraError, IMV_OK, getImvApi
from emo_master.plugins.builtins.huaray_camera.operator import DEFAULTS
from .executor import DeviceError


class ImvCapture:
    def __init__(self, connection, apiFactory=None):
        self.connection = connection
        self.apiFactory = apiFactory or getImvApi
        self.session = None
        self.params = None
        self.connectionGeneration = uuid4().hex
        self.streamGeneration = uuid4().hex
        self.lastBlockId = None
        self.quarantined = False

    def _settings(self, params):
        return dict(DEFAULTS, **dict(params, selectionMode="cameraKey",
                    cameraKey=self.connection["cameraKey"], retryCount=0))

    def _prepare(self, params):
        settings = self._settings(params)
        if self.params is None:
            self.connect(params, lambda: False, float("inf"))
            self.session.settings = settings
            self.session.open()
            self.params = deepcopy(params)
            return
        previous = self.params
        changed = {key for key in params if params[key] != previous[key]}
        work = changed - {"outputColor", "demosaic", "captureTimeoutMs"}
        if not work:
            self.params = deepcopy(params)
            return
        # An external trigger may already have queued a valid frame. Neither a
        # readback nor a larger blockId proves that it used the new settings.
        if previous["triggerMode"] == "hardware" or params["triggerMode"] == "hardware":
            raise DeviceError("E_CAMERA_SYNC_REQUIRED", "stop external triggers and explicitly recover before changing hardware acquisition settings")
        session = self.session
        session.stopAcquisition()
        session.clearBuffer()
        self.streamGeneration = uuid4().hex
        self.lastBlockId = None
        if "pixelFormat" in work and params["pixelFormat"] != "keep":
            session._setEnum("PixelFormat", params["pixelFormat"])
        if work & {"roiMode", "width", "height", "offsetX", "offsetY"}:
            if params["roiMode"] != "keep":
                session._setInt("OffsetX", 0)
                session._setInt("OffsetY", 0)
                for name, key in (("Width", "width"), ("Height", "height")):
                    value = session._intRange(name)[1] if params["roiMode"] == "full" else params[key]
                    session._setInt(name, value)
                if params["roiMode"] == "custom":
                    session._setInt("OffsetX", params["offsetX"])
                    session._setInt("OffsetY", params["offsetY"])
        for mode, key, auto, feature in (("exposureMode", "exposureUs", "ExposureAuto", "ExposureTime"),
                                        ("gainMode", "gainRaw", "GainAuto", "GainRaw")):
            if work & {mode, key} and params[mode] == "manual":
                if previous[mode] != "manual":
                    session._setEnum(auto, "Off")
                session._setDouble(feature, params[key])
        if work & {"frameRateMode", "frameRate"} and params["frameRateMode"] == "manual":
            session._setBool("AcquisitionFrameRateEnable", True)
            session._setDouble("AcquisitionFrameRate", params["frameRate"])
        if "triggerMode" in work:
            if params["triggerMode"] == "freeRun":
                session._setEnum("TriggerMode", "Off")
            else:
                session._setEnum("TriggerSelector", "FrameStart")
                session._setEnum("TriggerSource", "Software")
                session._setEnum("TriggerMode", "On")
        session.settings = settings
        session.actualExposureUs = session._getDouble("ExposureTime")
        session.offsetX = session._getInt("OffsetX")
        session.offsetY = session._getInt("OffsetY")
        session.startAcquisition()
        self.params = deepcopy(params)

    def connect(self, params, cancelled, deadline):
        if cancelled() or monotonic() >= deadline:
            raise DeviceError("E_DEVICE_CANCELLED", "camera connection cancelled before SDK admission")
        if self.quarantined:
            raise DeviceError("E_DEVICE_UNAVAILABLE", "camera requires explicit recovery", unhealthy=True)
        if self.session is None:
            self.session = CameraSession(self.apiFactory(), self._settings(params))
        try:
            self.session.connect()
        except Exception as error:
            self.quarantined = True
            raise DeviceError(getattr(error, "code", "E_CAMERA_IO"), str(error), unhealthy=True) from error
        return dict(connectionGeneration=self.connectionGeneration)

    def capture(self, params, cancelled, deadline, capacity):
        if self.quarantined:
            raise DeviceError("E_DEVICE_UNAVAILABLE", "camera stream requires explicit recovery", unhealthy=True)
        triggered = False
        acquiring = False

        def check():
            if cancelled() or monotonic() >= deadline:
                raise DeviceError("E_DEVICE_CANCELLED" if cancelled() else "E_DEVICE_OPERATION_TIMEOUT",
                                  "camera acquisition interrupted", outcome="UNKNOWN" if triggered else "NOT_EXECUTED")

        check()
        try:
            self._prepare(params)
            check()
            session = self.session
            width, height = session._getInt("Width"), session._getInt("Height")
            if width * height * (1 if params["outputColor"] == "gray" else 3) > capacity:
                raise DeviceError("E_FRAME_CAPACITY", "camera ROI exceeds reserved original capacity")
            if params["triggerMode"] == "software":
                # Mark uncertainty BEFORE entering the native call, including a
                # lost return or native crash. Never replay this trigger.
                triggered = True
                result = session.api.execute_command(session.handle, "TriggerSoftware")
                if result != IMV_OK:
                    raise DeviceError("E_CAMERA_IO", "software trigger receipt failed", outcome="UNKNOWN", unhealthy=True)
            acquiring = True
            timeout = params["captureTimeoutMs"] or None
            if deadline != float("inf"):
                remaining = max(1, int((deadline - monotonic()) * 1000))
                timeout = min(timeout, remaining) if timeout is not None else remaining
            captured = session.capture(timeout,
                                       params["outputColor"], params["demosaic"], check, sendSoftwareTrigger=False,
                                       maxOutputBytes=capacity)
            check()
            if params["sequencePolicy"] == "contiguous" and (captured.blockId <= 0 or
                    (self.lastBlockId is not None and captured.blockId != self.lastBlockId + 1)):
                raise DeviceError("E_CAMERA_SEQUENCE_UNCERTAIN", "stream frame sequence is not contiguous", unhealthy=True)
            self.lastBlockId = captured.blockId
            return captured.image, dict(blockId=captured.blockId, deviceTimestamp=captured.deviceTimestamp,
                actualExposureUs=float(session.actualExposureUs), exposureEvidence="configuration_readback_not_frame_metadata",
                frameExposureUs=None, requestedExposureUs=params["exposureUs"] if params["exposureMode"] == "manual" else None,
                sourceId=session.sourceId, offsetX=session.offsetX, offsetY=session.offsetY,
                connectionGeneration=self.connectionGeneration, streamGeneration=self.streamGeneration,
                selection="continuous_queue" if params["triggerMode"] == "hardware" else
                          "latest_complete" if params["triggerMode"] == "freeRun" else "single_software_trigger",
                retainedCurrentState=any(params[key] == "keep" for key in
                    ("exposureMode", "gainMode", "roiMode", "pixelFormat", "frameRateMode")))
        except Exception as error:
            # A clean hardware wait timeout consumed no frame. Other failures may
            # have lost a frame or left an in-flight exposure behind.
            clean = (isinstance(error, DeviceError) and error.code in {"E_CAMERA_SYNC_REQUIRED", "E_FRAME_CAPACITY"})
            clean |= isinstance(error, HuarayCameraError) and error.code == "E_CAMERA_TIMEOUT" and not triggered
            if not clean:
                self.quarantined = True
            raise DeviceError(getattr(error, "code", "E_CAMERA_IO"), str(error),
                              outcome="UNKNOWN" if triggered or acquiring and not clean else "NOT_EXECUTED",
                              unhealthy=not clean) from error

    def close(self):
        if self.session is not None:
            error = self.session.close()
            if error is not None:
                raise DeviceError(error.code, str(error), outcome="UNKNOWN", unhealthy=True)
            self.session = None
