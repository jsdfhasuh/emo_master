from __future__ import annotations

from time import perf_counter

from emo_master.plugins.builtins._network_camera import (
    CREDENTIAL_PROPERTIES, cameraUrl, credentials, errorResult, integerParam, validateCredentials,
)
from emo_master.plugins.builtins.hikvision_audio.client import AudioError, HikvisionAudioClient
from emo_master.plugins.builtins.image_loader.operator import OperatorMeta


class HikvisionAudioOperator:
    meta = OperatorMeta(
        operatorId="communication.camera.hikvision_audio",
        displayName="\u6444\u50cf\u5934\u53d1\u58f0\uff08\u6d77\u5eb7\uff09",
        version="1.0.0",
        inputPorts={"trigger": {"type": "boolean", "required": False, "nullable": False}},
        outputPorts={
            "triggered": {"type": "boolean", "required": True, "nullable": False},
            "audioId": {"type": "integer", "required": True, "nullable": False},
            "volume": {"type": "integer", "required": True, "nullable": False},
            "alarmTimes": {"type": "integer", "required": True, "nullable": False},
        },
        paramSchema={
            "type": "object",
            "properties": {
                "cameraUrl": {"title": "\u6444\u50cf\u5934HTTP\u5730\u5740", "type": "string", "default": ""},
                **CREDENTIAL_PROPERTIES,
                "audioSource": {"title": "\u97f3\u9891\u6765\u6e90", "type": "string", "default": "builtin",
                                "enum": ["builtin", "custom"],
                                "xOptionLabels": {"builtin": "\u5185\u7f6e\u97f3\u9891", "custom": "\u5df2\u4e0a\u4f20\u97f3\u9891"}},
                "audioId": {"title": "\u5185\u7f6e\u97f3\u9891ID", "type": "integer", "default": 12,
                            "minimum": 1, "maximum": 12, "xVisibleWhen": {"audioSource": ["builtin"]}},
                "customAudioId": {"title": "\u81ea\u5b9a\u4e49\u97f3\u9891\u69fd\u4f4d", "type": "integer", "default": 1,
                                  "minimum": 1, "maximum": 3, "xVisibleWhen": {"audioSource": ["custom"]}},
                "maxVolume": {"title": "\u5141\u8bb8\u7684\u6700\u5927\u97f3\u91cf", "type": "integer", "default": 30,
                              "minimum": 1, "maximum": 100},
                "maxAlarmTimes": {"title": "\u5141\u8bb8\u7684\u6700\u5927\u64ad\u653e\u6b21\u6570", "type": "integer", "default": 1,
                                  "minimum": 1, "maximum": 50},
                "timeoutMs": {"title": "\u8bf7\u6c42\u8d85\u65f6\uff08\u6beb\u79d2\uff09", "type": "integer", "default": 5000,
                              "minimum": 100, "maximum": 60000},
            },
            "required": ["cameraUrl"],
        },
    )

    def validateParams(self, params):
        try:
            cameraUrl(params.get("cameraUrl"))
            validateCredentials(params)
            if not params.get("username", "admin"):
                raise ValueError("username must not be empty")
            source = params.get("audioSource", "builtin")
            if source not in ("builtin", "custom"):
                raise ValueError("audioSource must be builtin or custom")
            if source == "builtin":
                integerParam(params, "audioId", 12, 1, 12)
            else:
                integerParam(params, "customAudioId", 1, 1, 3)
            integerParam(params, "maxVolume", 30, 1, 100)
            integerParam(params, "maxAlarmTimes", 1, 1, 50)
            integerParam(params, "timeoutMs", 5000, 100, 60000)
        except ValueError as error:
            return {"code": "E_PARAM_INVALID", "message": str(error)}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        started = perf_counter()
        if type(inputs.get("trigger", True)) is not bool:
            return errorResult("E_INPUT_TYPE", "trigger must be a boolean")
        check = runtimeContext.get("raiseIfCancellationRequested", lambda: None)
        check()
        if inputs.get("trigger", True) is False:
            return {"status": "ok", "outputs": {"triggered": False, "audioId": 0, "volume": 0, "alarmTimes": 0},
                    "diagnostics": {"text": "Audio trigger is false; no camera request sent"}}
        invalid = self.validateParams(params)
        if invalid:
            return {"status": "error", "error": invalid}
        try:
            username, password = credentials(params)
            if not password:
                raise ValueError("password or passwordEnv is required for camera audio")
            client = HikvisionAudioClient(params["cameraUrl"], username, password,
                                          params.get("timeoutMs", 5000) / 1000, check)
            outputs = client.play(params.get("audioSource", "builtin"), params.get("audioId", 12),
                                  params.get("customAudioId", 1), params.get("maxVolume", 30),
                                  params.get("maxAlarmTimes", 1))
        except ValueError as error:
            return errorResult("E_PARAM_INVALID", str(error))
        except AudioError as error:
            result = errorResult(error.code, str(error))
            result["diagnostics"] = {"automaticRetry": False}
            if error.code == "E_CAMERA_AUDIO_UNKNOWN":
                result["diagnostics"]["outcome"] = "UNKNOWN"
            return result
        return {
            "status": "ok", "outputs": outputs,
            "metrics": {"latencyMs": round((perf_counter() - started) * 1000, 3)},
            "diagnostics": {"text": "Camera acknowledged playback trigger; audible completion is not verified"},
        }
