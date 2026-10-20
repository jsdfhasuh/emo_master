"""Reversible common-feature tuning, owned exclusively by a Runtime worker."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable

from emo_master.plugins.builtins._huaray_imv import CameraSession, HuarayCameraError, IMV_OK
from emo_master.plugins.builtins.huaray_camera.operator import DEFAULTS, HuarayCameraOperator


FEATURE_TYPES = {
    "AcquisitionMode": "enum",
    "TriggerSelector": "enum", "TriggerMode": "enum", "TriggerSource": "enum",
    "TriggerActivation": "enum", "PixelFormat": "enum", "ExposureAuto": "enum",
    "ExposureTime": "double", "GainAuto": "enum", "GainRaw": "double",
    "AcquisitionFrameRateEnable": "bool", "AcquisitionFrameRate": "double",
    "Width": "int", "Height": "int", "OffsetX": "int", "OffsetY": "int",
}
GROUPS = {
    "acquisition": ("AcquisitionMode",),
    "trigger": ("TriggerSelector", "TriggerMode", "TriggerSource", "TriggerActivation"),
    "exposure": ("ExposureAuto", "ExposureTime"),
    "gain": ("GainAuto", "GainRaw"),
    "rate": ("AcquisitionFrameRateEnable", "AcquisitionFrameRate"),
    "image": ("PixelFormat", "Width", "Height", "OffsetX", "OffsetY"),
}
PARAM_GROUPS = {
    "triggerMode": "trigger", "triggerSource": "trigger", "triggerActivation": "trigger",
    "exposureMode": "exposure", "exposureUs": "exposure",
    "gainMode": "gain", "gainRaw": "gain", "frameRateMode": "rate", "frameRate": "rate",
    "pixelFormat": "image", "roiMode": "image", "width": "image", "height": "image",
    "offsetX": "image", "offsetY": "image",
}
SOFTWARE_FIELDS = {"outputColor", "demosaic", "captureTimeoutMs", "retryCount", "retryDelayMs"}
SELECTORS = {"exposure": "ExposureSelector", "gain": "GainSelector", "trigger": "TriggerSelector"}


def equalValue(actual: object, target: object) -> bool:
    if isinstance(target, float) and isinstance(actual, (int, float)):
        return math.isclose(actual, target, rel_tol=1e-6, abs_tol=max(1e-9, abs(target) * 1e-6))
    return type(actual) is type(target) and actual == target


class CameraConfiguration:
    def __init__(self, api: Any, params: dict[str, object], persist: Callable[[dict], None], *, camera=None) -> None:
        validation = HuarayCameraOperator().validateParams({key: value for key, value in params.items()
            if key in {"selectionMode", "ipAddress", "cameraKey", "userId", "deviceIndex"} | SOFTWARE_FIELDS})
        if validation:
            raise HuarayCameraError("E_PARAM_INVALID", validation["message"])
        self.camera = camera if camera is not None else CameraSession(api, {**DEFAULTS, **params})
        self.persist = persist
        self.actual: dict[str, object] = {}
        self.properties: dict[str, dict] = {}
        self.original: dict[str, object] = {}
        self.expected: dict[str, object] = {}
        self.contexts: dict[str, dict] = {}
        self.activationContext: dict[str, object] = {}
        self.software = {key: self.camera.settings[key] for key in SOFTWARE_FIELDS}
        self.originalSoftware = dict(self.software)
        self.records: list[dict] = []
        self.failed = False
        self.pending = False
        self.identity = self.camera.sourceId if camera is not None else ""
        self.restoring = False

    def connect(self) -> None:
        self.camera.connect()
        if not self.camera.identityLeaseKey:
            raise HuarayCameraError("E_CAMERA_IDENTITY", "cannot read a stable device identity; tuning disabled")
        self.identity = self.camera.sourceId
        self.refresh()

    def checkIdentity(self) -> None:
        api = self.camera.api
        isOpen = getattr(api, "is_open", None)
        if callable(isOpen) and not isOpen(self.camera.handle):
            raise HuarayCameraError("E_CAMERA_DISCONNECTED", "camera is disconnected")
        code, serial = api.get_string(self.camera.handle, "DeviceSerialNumber")
        if code != IMV_OK or "huaray-imv:" + serial.strip() != self.identity:
            raise HuarayCameraError("E_CAMERA_IDENTITY", "camera identity changed or device is offline")
        # A cached open flag does not establish transport liveness.
        self.camera._getDouble("ExposureTime")

    def _access(self, name: str) -> tuple[bool, bool, bool]:
        access = getattr(self.camera.api, "feature_access", None)
        if not callable(access):
            raise HuarayCameraError("E_CAMERA_UNRESTORABLE", "SDK cannot query feature permissions")
        return access(self.camera.handle, name)

    def _read(self, name: str) -> object:
        available, readable, _ = self._access(name)
        if not available or not readable:
            raise HuarayCameraError("E_CAMERA_UNRESTORABLE", f"{name} is not readable")
        kind = FEATURE_TYPES.get(name, "enum")
        result, value = getattr(self.camera.api, "get_" + kind)(self.camera.handle, name)
        if result != IMV_OK or (isinstance(value, float) and not math.isfinite(value)):
            raise HuarayCameraError("E_CAMERA_CONFIG_FAILED", f"failed to read {name} ({result})")
        return value

    def refresh(self) -> dict:
        self.actual, self.properties = {}, {}
        self.checkIdentity()
        properties = {}
        actual = {}
        for name, kind in FEATURE_TYPES.items():
            entry: dict = {"type": kind, "readable": False, "writable": False}
            try:
                value = self._read(name)
                actual[name] = value
                entry.update(value=value, readable=True, writable=self._access(name)[2])
                if kind == "double":
                    entry["minimum"], entry["maximum"] = self.camera._doubleRange(name)
                elif kind == "int":
                    entry["minimum"], entry["maximum"], entry["step"] = self.camera._intRange(name)
                elif kind == "enum":
                    options = getattr(self.camera.api, "get_enum_values", None)
                    if callable(options):
                        entry["options"] = list(options(self.camera.handle, name))
            except Exception as err:
                entry["reason"] = str(err)
            properties[name] = entry
        self.actual, self.properties = actual, properties
        return self.snapshot()

    def snapshot(self) -> dict:
        return deepcopy({
            "identity": self.identity, "actual": self.actual, "properties": self.properties,
            "original": self.original, "contexts": self.contexts, "software": self.software,
            "activationContext": self.activationContext,
            "originalSoftware": self.originalSoftware, "records": self.records,
            "pending": self.pending, "failed": self.failed,
            "params": self.actualParams(),
        })

    def actualParams(self) -> dict:
        a = self.actual
        values = dict(self.software)
        mapping = {"exposureUs": "ExposureTime", "gainRaw": "GainRaw",
                   "frameRate": "AcquisitionFrameRate", "pixelFormat": "PixelFormat",
                   "width": "Width", "height": "Height", "offsetX": "OffsetX", "offsetY": "OffsetY",
                   "triggerSource": "TriggerSource", "triggerActivation": "TriggerActivation"}
        values.update({key: a[name] for key, name in mapping.items() if a.get(name) is not None})
        if values.get("triggerSource") == "Software":
            values["triggerSource"] = self.camera.settings.get("triggerSource", "Line1")
        if "TriggerMode" in a:
            values["triggerMode"] = ("freeRun" if a["TriggerMode"] == "Off" else
                                     "software" if a.get("TriggerSource") == "Software" else "hardware")
        for param, feature in (("exposureMode", "ExposureAuto"), ("gainMode", "GainAuto")):
            if feature in a:
                values[param] = "manual" if a[feature] == "Off" else "keep"
        if "AcquisitionFrameRateEnable" in a:
            values["frameRateMode"] = "manual" if a["AcquisitionFrameRateEnable"] else "keep"
        if all(name in a for name in GROUPS["image"]):
            values["roiMode"] = "custom"
        return values

    def _context(self, group: str) -> dict:
        selector = SELECTORS.get(group)
        if not selector:
            return {}
        available, readable, _ = self._access(selector)
        if not available:
            return {}
        if not readable:
            raise HuarayCameraError("E_CAMERA_UNRESTORABLE", f"cannot read selector {selector}")
        return {selector: self._read(selector)}

    def _remember(self, group: str, features: tuple[str, ...] | None = None) -> None:
        context = self._context(group)
        if group in self.contexts and context != self.contexts[group]:
            raise HuarayCameraError("E_CAMERA_EXTERNAL_CHANGE", f"{group} selector changed externally")
        if group == "trigger" and context.get("TriggerSelector", "FrameStart") != "FrameStart":
            raise HuarayCameraError("E_CAMERA_UNRESTORABLE", "trigger tuning requires the FrameStart selector; other contexts are read-only")
        values = {name: self._read(name) for name in (features if features is not None else GROUPS[group])}
        if any(values.get(name) == "Once" for name in ("ExposureAuto", "GainAuto")):
            raise HuarayCameraError("E_CAMERA_UNRESTORABLE", "one-shot automatic adjustment has no stable restoration baseline")
        self.contexts.setdefault(group, context)
        for name, value in values.items():
            if name not in self.original:
                self.original[name] = value
                self.expected[name] = value

    def _dynamic(self, name: str, values: dict) -> bool:
        modes = {"ExposureTime": "ExposureAuto", "GainRaw": "GainAuto"}
        return name in modes and values.get(modes[name]) in {"Continuous", "Once"}

    def _checkExternal(self, *, deferTriggerDependent: bool = False) -> set[str]:
        deferred: set[str] = set()
        for group, context in self.contexts.items():
            if self._context(group) != context:
                raise HuarayCameraError("E_CAMERA_EXTERNAL_CHANGE", f"{group} selector changed externally")
        for name, expected in self.expected.items():
            # Some cameras hide ExposureAuto while triggering is enabled.
            # Defer only until our recorded trigger mode has been disabled;
            # the caller must check it again before restoring exposure.
            if name == "ExposureAuto" and deferTriggerDependent and not self._access(name)[1]:
                deferred.add(name)
                continue
            if name == "TriggerActivation" and self.activationContext and not self._access(name)[1]:
                currentContext = {key: self._read(key) for key in self.activationContext}
                if currentContext != self.activationContext:
                    continue
            if not self._dynamic(name, self.actual):
                value = self._read(name)
                if expected is not None and not equalValue(value, expected):
                    raise HuarayCameraError("E_CAMERA_EXTERNAL_CHANGE", f"{name} changed outside this session")
        return deferred

    def _write(self, name: str, value: object) -> None:
        self.checkIdentity()
        actual = self._read(name)
        if equalValue(actual, value):
            return
        if not self._access(name)[2]:
            raise HuarayCameraError("E_CAMERA_CONFIG_FAILED", f"{name} is not writable")
        kind = FEATURE_TYPES[name]
        if kind == "enum":
            options = getattr(self.camera.api, "get_enum_values", None)
            if callable(options) and value not in options(self.camera.handle, name):
                raise HuarayCameraError("E_CAMERA_CONFIG_FAILED", f"{name} does not support {value}")
        if kind == "int":
            lo, hi, step = self.camera._intRange(name)
            if type(value) is not int or not lo <= value <= hi or (step and (value - lo) % step):
                raise HuarayCameraError("E_CAMERA_CONFIG_FAILED", f"{name} violates device range or step")
        if kind == "double":
            low, high = self.camera._doubleRange(name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise HuarayCameraError("E_CAMERA_CONFIG_FAILED", f"{name} violates device range")
        record = {"property": name, "before": actual, "requested": value, "actual": None,
                  "original": self.original.get(name),
                  "status": "unknown", "restoring": self.restoring,
                  "context": next((ctx for group, ctx in self.contexts.items() if name in GROUPS[group]), {})}
        self.records.append(record)
        self.pending = True
        self.expected[name] = None
        self.actual[name] = None
        self.persist(self.snapshot())
        try:
            result = getattr(self.camera.api, "set_" + kind)(self.camera.handle, name, value)
            record["sdkCode"] = result
            readback = self._read(name)
            record["actual"] = readback
            self.actual[name] = readback
            self.expected[name] = readback
            dependent = {"ExposureAuto": "ExposureTime", "GainAuto": "GainRaw",
                         "AcquisitionFrameRateEnable": "AcquisitionFrameRate"}.get(name)
            if dependent:
                # Switching an automatic mode can freeze a newly measured value.
                self.expected[dependent] = None
                self.actual[dependent] = None
                current = self._read(dependent)
                self.expected[dependent] = current
                self.actual[dependent] = current
            if result != IMV_OK or not equalValue(readback, value):
                raise HuarayCameraError("E_CAMERA_CONFIG_FAILED", f"{name}: requested {value}, actual {readback}, SDK {result}")
            record["status"] = "confirmed"
        except Exception as err:
            record["error"] = str(err)
            if record["actual"] is None:
                try:
                    record["actual"] = self._read(name)
                    self.actual[name] = record["actual"]
                    self.expected[name] = record["actual"]
                except Exception:
                    pass
            if record["actual"] is not None:
                record["status"] = "failed"
            raise
        finally:
            self.persist(self.snapshot())

    def apply(self, changes: dict[str, object]) -> dict:
        if self.failed:
            raise HuarayCameraError("E_CAMERA_SESSION_FAULT", "stop and verify the failed session before continuing")
        unknown = set(changes) - set(PARAM_GROUPS) - SOFTWARE_FIELDS
        if unknown:
            raise HuarayCameraError("E_PARAM_INVALID", "not a tunable parameter: " + ", ".join(sorted(unknown)))
        values = {**self.camera.settings, **self.actualParams(), **changes}
        validation = HuarayCameraOperator().validateParams(values)
        if validation:
            raise HuarayCameraError("E_PARAM_INVALID", validation["message"])
        resume = self.camera.grabbing
        try:
            self.camera.stopAcquisition()
            self.refresh()
            deferred = self._checkExternal(deferTriggerDependent=changes.get("triggerMode") == "freeRun")
            groups = {PARAM_GROUPS[key] for key in changes if key in PARAM_GROUPS}
            triggerTargets: dict[str, object] = {}
            if "trigger" in groups:
                mode = values["triggerMode"]
                triggerTargets["TriggerMode"] = "Off" if mode == "freeRun" else "On"
                if mode != "freeRun":
                    triggerTargets["TriggerSource"] = "Software" if mode == "software" else values["triggerSource"]
                if mode == "hardware" and "triggerActivation" in changes:
                    context = {name: self._read(name) for name in ("TriggerSource", "TriggerMode")}
                    if context["TriggerSource"] != triggerTargets["TriggerSource"]:
                        raise HuarayCameraError("E_CAMERA_UNRESTORABLE", "请先切换触发输入线，读取实际边沿后再修改触发边沿")
                    if self.activationContext and self.activationContext != context:
                        raise HuarayCameraError("E_CAMERA_UNRESTORABLE", "触发边沿上下文已变化，请先结束当前试调会话")
                    self._read("TriggerActivation")
                    self.activationContext = context
                    triggerTargets["TriggerActivation"] = values["triggerActivation"]
            # Capture all dependencies before the first device write in a batch.
            for group in sorted(groups):
                self._remember(group, tuple(triggerTargets) if group == "trigger" else None)
            if deferred:
                self._write("TriggerMode", "Off")
                self.refresh()
                self._checkExternal()
            if "image" in groups:
                formatChanged = False
                if "pixelFormat" in changes and changes["pixelFormat"] != "keep":
                    if values["pixelFormat"] != self._read("PixelFormat"):
                        formatChanged = True
                        for name in ("OffsetX", "OffsetY"):
                            self._write(name, 0)
                        for name in ("Width", "Height"):
                            self._write(name, self.camera._intRange(name)[0])
                    self._write("PixelFormat", values["pixelFormat"])
                roiRequested = values["roiMode"] in {"custom", "full"} and any(
                    key in changes for key in ("roiMode", "width", "height", "offsetX", "offsetY"))
                if formatChanged or roiRequested:
                    self._write("OffsetX", 0)
                    self._write("OffsetY", 0)
                    full = roiRequested and values["roiMode"] == "full"
                    self._write("Width", self.camera._intRange("Width")[1] if full else values["width"])
                    self._write("Height", self.camera._intRange("Height")[1] if full else values["height"])
                    if not full:
                        self._write("OffsetX", values["offsetX"])
                        self._write("OffsetY", values["offsetY"])
            for group, mode, auto, param, feature in (
                ("exposure", "exposureMode", "ExposureAuto", "exposureUs", "ExposureTime"),
                ("gain", "gainMode", "GainAuto", "gainRaw", "GainRaw"),
                ("rate", "frameRateMode", "AcquisitionFrameRateEnable", "frameRate", "AcquisitionFrameRate"),
            ):
                if group in groups and values[mode] == "manual":
                    self._write(auto, True if group == "rate" else "Off")
                    self._write(feature, float(values[param]))
            if "trigger" in groups:
                if any(self._read(name) != target for name, target in triggerTargets.items()):
                    self._write("TriggerMode", "Off")
                    if "TriggerSource" in triggerTargets:
                        self._write("TriggerSource", triggerTargets["TriggerSource"])
                    self._write("TriggerMode", triggerTargets["TriggerMode"])
                    if "TriggerActivation" in triggerTargets:
                        self._write("TriggerActivation", triggerTargets["TriggerActivation"])
            for key in SOFTWARE_FIELDS & changes.keys():
                if self.software[key] != changes[key]:
                    self.pending = True
                    self.software[key] = changes[key]
            self.refresh()
            self.camera.settings.update(self.actualParams())
            self.persist(self.snapshot())
            if resume:
                self.camera.clearBuffer()
                self.camera.startAcquisition()
            return self.snapshot()
        except Exception:
            self.failed = True
            self.persist(self.snapshot())
            raise

    def prepareAcquisition(self) -> None:
        # A device left in SingleFrame must not silently stop a continuous preview.
        if self._access("AcquisitionMode")[0]:
            self._remember("acquisition")
            self._write("AcquisitionMode", "Continuous")
            self.refresh()

    def recover(self) -> dict:
        self.camera.stopAcquisition()
        self.refresh()
        self._checkExternal()
        for name, value in self.expected.items():
            if value is None:
                self.expected[name] = self._read(name)
        self.failed = False
        self.persist(self.snapshot())
        return self.snapshot()

    def restore(self) -> dict:
        self.camera.stopAcquisition()
        self.refresh()
        deferred = self._checkExternal(deferTriggerDependent="TriggerMode" in self.original)
        self.restoring = True
        original = self.original
        verified: dict[str, object] = {}
        try:
            if deferred:
                self._write("TriggerMode", "Off")
                self.refresh()
                self._checkExternal()
            if "AcquisitionMode" in original:
                self._write("AcquisitionMode", original["AcquisitionMode"])
            if "TriggerMode" in original:
                self._write("TriggerMode", "Off")
                if "TriggerActivation" in original:
                    self._write("TriggerSource", self.activationContext["TriggerSource"])
                    self._write("TriggerMode", self.activationContext["TriggerMode"])
                    expected = self.expected.get("TriggerActivation")
                    if expected is not None and not equalValue(self._read("TriggerActivation"), expected):
                        raise HuarayCameraError("E_CAMERA_EXTERNAL_CHANGE", "触发边沿已被外部修改，停止恢复")
                    self._write("TriggerActivation", original["TriggerActivation"])
                    verified["TriggerActivation"] = self._read("TriggerActivation")
                    self._write("TriggerMode", "Off")
                if "TriggerSource" in original:
                    self._write("TriggerSource", original["TriggerSource"])
            if "PixelFormat" in original:
                for name in ("OffsetX", "OffsetY"):
                    self._write(name, 0)
                if self._read("PixelFormat") != original["PixelFormat"]:
                    for name in ("Width", "Height"):
                        self._write(name, self.camera._intRange(name)[0])
                    self._write("PixelFormat", original["PixelFormat"])
                for name in ("Width", "Height", "OffsetX", "OffsetY"):
                    self._write(name, original[name])
            for mode, feature in (("ExposureAuto", "ExposureTime"), ("GainAuto", "GainRaw")):
                if mode in original:
                    self._write(mode, "Off")
                    if original[mode] == "Off":
                        self._write(feature, original[feature])
                    self._write(mode, original[mode])
                    verified[mode] = self._read(mode)
            if "AcquisitionFrameRateEnable" in original:
                self._write("AcquisitionFrameRateEnable", True)
                self._write("AcquisitionFrameRate", original["AcquisitionFrameRate"])
                self._write("AcquisitionFrameRateEnable", original["AcquisitionFrameRateEnable"])
            if "TriggerMode" in original:
                self._write("TriggerMode", original["TriggerMode"])
            self.refresh()
            missing = [name for name, value in original.items()
                       if not self._dynamic(name, original) and not equalValue(self.actual.get(name, verified.get(name)), value)]
            if missing:
                raise HuarayCameraError("E_CAMERA_RESTORE_FAILED", "not restored: " + ", ".join(missing))
            self.software = dict(self.originalSoftware)
            self.failed = False
            self.pending = False
            self.persist(self.snapshot())
            return self.snapshot()
        except Exception:
            self.failed = True
            self.persist(self.snapshot())
            raise
        finally:
            self.restoring = False

    def retain(self) -> None:
        self.camera.stopAcquisition()
        self.refresh()
        self._checkExternal()
        if "TriggerActivation" in self.original and not self._access("TriggerActivation")[1]:
            raise HuarayCameraError("E_CAMERA_RESULT_UNKNOWN", "触发边沿当前不可回读，请切回已修改的硬件触发方式后核查")
        if any(value is None for value in self.expected.values()):
            raise HuarayCameraError("E_CAMERA_RESULT_UNKNOWN", "verify unknown writes before retaining changes")
        self.pending = False
        self.persist(self.snapshot())

    def close(self) -> None:
        error = self.camera.close()
        if error:
            raise error
