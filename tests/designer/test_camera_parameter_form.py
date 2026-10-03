from __future__ import annotations

import pytest
from emo_master.plugins.builtins.huaray_camera.operator import DEFAULTS, PARAM_SCHEMA
from emo_master.plugins.builtins.huaray_camera.parameter_form import CameraParameterForm, FIELD_LABELS
from PySide2.QtWidgets import QComboBox, QLabel


def testCameraLabelsAreChineseAndValuesRemainCompatible() -> None:
    form = CameraParameterForm()
    values = dict(DEFAULTS, ipAddress="192.168.125.28", triggerMode="freeRun", retryCount=0)
    form.setSchema(PARAM_SCHEMA, values)
    assert form.getValues() == values
    assert set(FIELD_LABELS) == set(DEFAULTS)
    for name, control in form._controls.items():
        label = form._layout.labelForField(control)
        assert isinstance(label, QLabel)
        assert label.text() == FIELD_LABELS[name]
    selection = form._controls["selectionMode"]
    assert isinstance(selection, QComboBox)
    assert selection.currentText() == "按 IP 地址"
    assert selection.currentData() == "ip"
    assert form._controls["ipAddress"].isEnabled()
    for name in ("cameraKey", "userId", "deviceIndex", "triggerSource", "triggerActivation", "retryDelayMs", "exposureUs", "gainRaw", "frameRate", "width", "height", "offsetX", "offsetY"):
        assert not form._controls[name].isEnabled(), name
    form.close()


@pytest.mark.parametrize(("selector", "value", "active", "inactive"), [
    ("selectionMode", "cameraKey", ["cameraKey"], ["ipAddress", "userId", "deviceIndex"]),
    ("selectionMode", "userId", ["userId"], ["ipAddress", "cameraKey", "deviceIndex"]),
    ("selectionMode", "index", ["deviceIndex"], ["ipAddress", "cameraKey", "userId"]),
    ("triggerMode", "hardware", ["triggerSource", "triggerActivation"], []),
    ("triggerMode", "software", [], ["triggerSource", "triggerActivation"]),
    ("exposureMode", "manual", ["exposureUs"], []),
    ("gainMode", "manual", ["gainRaw"], []),
    ("frameRateMode", "manual", ["frameRate"], []),
    ("roiMode", "custom", ["width", "height", "offsetX", "offsetY"], []),
    ("roiMode", "full", [], ["width", "height", "offsetX", "offsetY"]),
    ("pixelFormat", "Mono8", [], ["demosaic"]),
    ("pixelFormat", "BayerRG8", ["demosaic"], []),
])
def testCameraFieldsFollowModeWithoutLosingValues(selector, value, active, inactive) -> None:
    form = CameraParameterForm()
    values = dict(DEFAULTS, ipAddress="192.168.125.28", triggerMode="freeRun", width=2448)
    form.setSchema(PARAM_SCHEMA, values)
    combo = form._controls[selector]
    combo.setCurrentIndex(combo.findData(value))
    for name in active:
        assert form._controls[name].isEnabled()
    for name in inactive:
        assert not form._controls[name].isEnabled()
    assert form.getValues() == dict(values, **{selector: value})
    form.close()


def testCameraRetryAndRepeatedLoadPreserveValues() -> None:
    form = CameraParameterForm()
    values = dict(DEFAULTS, retryCount=0, retryDelayMs=350)
    for _ in range(2):
        form.setSchema(PARAM_SCHEMA, values)
        assert not form._controls["retryDelayMs"].isEnabled()
        form._controls["retryCount"].setValue(2)
        assert form._controls["retryDelayMs"].isEnabled()
        assert form.getValues()["retryDelayMs"] == 350
        assert form._layout.rowCount() == len(DEFAULTS) + 5
    form.close()
