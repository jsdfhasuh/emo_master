from __future__ import annotations

import os

import pytest

from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator


@pytest.mark.skipif(
    os.environ.get("HUARAY_CAMERA_SMOKE") != "1",
    reason="set HUARAY_CAMERA_SMOKE=1 to run against a real IMV camera",
)
def testRealHuarayCameraSmoke() -> None:
    selectionMode = os.environ.get("HUARAY_CAMERA_SELECTION_MODE", "ip")
    params: dict[str, object] = {
        "selectionMode": selectionMode,
        "ipAddress": os.environ.get("HUARAY_CAMERA_IP", ""),
        "cameraKey": os.environ.get("HUARAY_CAMERA_KEY", ""),
        "userId": os.environ.get("HUARAY_CAMERA_USER_ID", ""),
        "deviceIndex": int(os.environ.get("HUARAY_CAMERA_INDEX", "0")),
        "triggerMode": os.environ.get("HUARAY_CAMERA_TRIGGER_MODE", "freeRun"),
        "captureTimeoutMs": int(os.environ.get("HUARAY_CAMERA_TIMEOUT_MS", "5000")),
        "retryCount": 0,
    }
    operator = HuarayCameraOperator()
    try:
        result = operator.executeNode({}, params, {"nodeId": "camera-smoke"})
    finally:
        operator.disposeOperator()

    assert result["status"] == "ok", result
    assert result["outputs"]["image"].dtype.name == "uint8"
