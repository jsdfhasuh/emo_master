from copy import deepcopy

import pytest

from emo_master.plugins.builtins._huaray_imv import HuarayCameraError, resetImvApiForTests
from emo_master.plugins.builtins.huaray_camera.configuration import CameraConfiguration
from tests.plugins.test_huaray_camera_operator import _FakeImvApi, _params


class ConfigurationApi(_FakeImvApi):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.online = True
        self.denied = set()
        self.mismatch = ""
        self.bufferClears = 0

    def feature_access(self, handle, name):
        available = name in {**self.enums, **self.doubles, **self.ints, **self.bools}
        return available, available, available and name not in self.denied

    def is_open(self, handle):
        return self.online

    def clear_buffer(self, handle):
        self.bufferClears += 1
        return 0

    def set_enum(self, handle, name, value):
        self.writes.append((name, value))
        return super().set_enum(handle, name, value)

    def set_double(self, handle, name, value):
        self.writes.append((name, value))
        return super().set_double(handle, name, value + 1 if name == self.mismatch else value)

    def set_int(self, handle, name, value):
        self.writes.append((name, value))
        return super().set_int(handle, name, value)

    def set_bool(self, handle, name, value):
        self.writes.append((name, value))
        return super().set_bool(handle, name, value)


@pytest.fixture
def configuration():
    resetImvApiForTests()
    api = ConfigurationApi()
    records = []
    config = CameraConfiguration(api, _params(), lambda state: records.append(deepcopy(state)))
    config.connect()
    yield config, api, records
    config.close()
    resetImvApiForTests()


def testConnectAndRefreshNeverWriteOrStart(configuration):
    config, api, _ = configuration
    config.refresh()
    assert api.writes == []
    assert api.startStrategies == []
    assert config.identity.endswith(api.serialNumber)
    assert not config.pending


def testLiveWriteReadbackAndAutoModeRestore(configuration):
    config, api, records = configuration
    config.apply({"exposureMode": "manual", "exposureUs": 12000.0})
    assert api.enums["ExposureAuto"] == "Off"
    assert api.doubles["ExposureTime"] == 12000
    assert config.original["ExposureAuto"] == "Continuous"
    assert any(record["records"] and record["records"][-1]["status"] == "unknown" for record in records)
    count = len(api.writes)
    config.apply({"exposureUs": 12000.0})
    assert len(api.writes) == count
    config.restore()
    assert api.enums["ExposureAuto"] == "Continuous"
    assert not config.pending


def hideTriggeredExposureAuto(api, monkeypatch):
    access = api.feature_access

    def featureAccess(handle, name):
        if name == "ExposureAuto" and api.enums["TriggerMode"] == "On":
            return False, False, False
        return access(handle, name)

    monkeypatch.setattr(api, "feature_access", featureAccess)


@pytest.mark.parametrize("originalTrigger", ["Off", "On"])
def testRestoreChecksExposureBeforeReenablingOriginalTrigger(configuration, monkeypatch, originalTrigger):
    config, api, _ = configuration
    api.enums.update(TriggerMode=originalTrigger, ExposureAuto="Off")
    originalExposure = api.doubles["ExposureTime"]
    hideTriggeredExposureAuto(api, monkeypatch)
    config.apply({"triggerMode": "freeRun"})
    config.apply({"exposureMode": "manual", "exposureUs": originalExposure + 50})
    config.apply({"triggerMode": "software"})
    assert not config.properties["ExposureAuto"]["readable"]
    config.restore()
    assert api.enums["ExposureAuto"] == "Off"
    assert api.doubles["ExposureTime"] == originalExposure
    assert api.enums["TriggerMode"] == originalTrigger
    assert not config.pending


@pytest.mark.parametrize("externalFeature", ["ExposureAuto", "ExposureTime"])
def testHiddenExposureStillRejectsExternalChanges(configuration, monkeypatch, externalFeature):
    config, api, _ = configuration
    api.enums["ExposureAuto"] = "Off"
    hideTriggeredExposureAuto(api, monkeypatch)
    config.apply({"exposureMode": "manual", "exposureUs": 8050.0})
    config.apply({"triggerMode": "software"})
    if externalFeature == "ExposureAuto":
        api.enums[externalFeature] = "Continuous"
    else:
        api.doubles[externalFeature] = 9000.0
    count = len(api.writes)
    with pytest.raises(HuarayCameraError, match="changed outside"):
        config.restore()
    assert all(name == "TriggerMode" for name, _ in api.writes[count:])
    assert config.pending


def testSwitchBackToFreeRunChecksPreviouslyHiddenExposure(configuration, monkeypatch):
    config, api, _ = configuration
    api.enums["ExposureAuto"] = "Off"
    hideTriggeredExposureAuto(api, monkeypatch)
    config.apply({"exposureMode": "manual", "exposureUs": 8050.0})
    config.apply({"triggerMode": "software"})
    config.apply({"triggerMode": "freeRun"})
    assert config.properties["ExposureAuto"]["readable"]
    config.restore()
    assert not config.pending


def testRoiRestoreAndPreviewRestartPreserveBaseline(configuration):
    config, api, _ = configuration
    original = dict(api.ints)
    config.apply({"roiMode": "custom", "width": 6, "height": 4, "offsetX": 4, "offsetY": 2})
    baseline = dict(config.original)
    config.camera.startAcquisition()
    config.camera.stopAcquisition()
    config.apply({"width": 8})
    assert config.original == baseline
    config.restore()
    assert api.ints == original


def testPixelFormatAlonePreservesCurrentRoi(configuration):
    config, api, _ = configuration
    original = dict(api.ints)
    config.apply({"pixelFormat": "BayerRG8"})
    assert api.enums["PixelFormat"] == "BayerRG8"
    assert api.ints == original
    config.restore()
    assert api.ints == original


def testFailedManualWriteRestoresAutoModeDespiteFrozenMeasurement(configuration, monkeypatch):
    config, api, _ = configuration
    originalSet = api.set_enum

    def freezeMeasurement(handle, name, value):
        result = originalSet(handle, name, value)
        if name == "ExposureAuto" and value == "Off":
            api.doubles["ExposureTime"] += 100
        return result

    monkeypatch.setattr(api, "set_enum", freezeMeasurement)
    api.denied.add("ExposureTime")
    with pytest.raises(HuarayCameraError, match="writable"):
        config.apply({"exposureMode": "manual", "exposureUs": 15000.0})
    assert api.enums["ExposureAuto"] == "Off"
    config.restore()
    assert api.enums["ExposureAuto"] == "Continuous" and not config.pending


def testMismatchStopsLaterWritesAndDoesNotRollback(configuration):
    config, api, _ = configuration
    api.mismatch = "ExposureTime"
    with pytest.raises(HuarayCameraError, match="actual"):
        config.apply({"exposureMode": "manual", "exposureUs": 11000.0, "gainMode": "manual", "gainRaw": 2.0})
    assert config.failed and config.pending
    assert api.doubles["ExposureTime"] == 11001
    assert api.enums["GainAuto"] == "Continuous"
    assert not config.camera.grabbing
    with pytest.raises(HuarayCameraError):
        config.apply({"gainRaw": 3.0})
    api.mismatch = ""
    config.restore()
    assert not config.pending


def testPermissionsRangeAndStepCheckedBeforeWriting(configuration):
    config, api, _ = configuration
    api.denied.add("ExposureAuto")
    with pytest.raises(HuarayCameraError, match="writable"):
        config.apply({"exposureMode": "manual"})
    assert api.writes == []
    api.denied.clear()
    config.recover()
    with pytest.raises(HuarayCameraError, match="step"):
        config.apply({"roiMode": "custom", "width": 5})
    assert ("Width", 5) not in api.writes


def testDisconnectedOrExternalChangesCannotBeRestoredSilently(configuration):
    config, api, _ = configuration
    config.apply({"gainMode": "manual", "gainRaw": 3.0})
    api.doubles["GainRaw"] = 6.0
    with pytest.raises(HuarayCameraError, match="outside"):
        config.restore()
    assert api.doubles["GainRaw"] == 6
    api.online = False
    with pytest.raises(HuarayCameraError, match="disconnected"):
        config.refresh()


def testAliasSelectionCannotTakeSameDevice(configuration):
    config, api, _ = configuration
    other = CameraConfiguration(api, _params(selectionMode="cameraKey", cameraKey="different-alias"), lambda _: None)
    with pytest.raises(HuarayCameraError):
        other.connect()
    assert api.writes == []
    other.close()
    assert config.camera.opened


def testRetentionDoesNotWriteAndSoftwareSettingsRestore(configuration):
    config, api, _ = configuration
    config.apply({"outputColor": "gray", "triggerMode": "software"})
    config.apply({"gainMode": "manual", "gainRaw": 1.0})
    assert api.enums["TriggerSource"] == "Software"
    config.restore()
    assert config.software["outputColor"] == "bgr"
    config.apply({"exposureMode": "manual", "exposureUs": 13000.0})
    count = len(api.writes)
    config.retain()
    assert len(api.writes) == count
    assert api.doubles["ExposureTime"] == 13000


def hideInactiveTriggerEdge(api, monkeypatch):
    access = api.feature_access

    def featureAccess(handle, name):
        if name == "TriggerActivation" and (api.enums["TriggerSource"] == "Software" or api.enums["TriggerMode"] == "Off"):
            return False, False, False
        return access(handle, name)

    monkeypatch.setattr(api, "feature_access", featureAccess)


@pytest.mark.parametrize("mode", ["freeRun", "software", "hardware"])
def testUnreadableInactiveEdgeDoesNotBlockModeSelection(configuration, monkeypatch, mode):
    config, api, _ = configuration
    api.enums["TriggerSource"] = "Software"
    hideInactiveTriggerEdge(api, monkeypatch)
    config.refresh()
    assert not config.properties["TriggerActivation"]["readable"]
    config.apply({"triggerMode": mode})
    assert "TriggerActivation" not in config.original
    assert not any(name == "TriggerActivation" for name, value in api.writes)
    config.restore()
    assert api.enums["TriggerMode"] == "Off"
    assert api.enums["TriggerSource"] == "Software"


def testUnreadableExplicitEdgeWriteStillFailsBeforeAnyWrite(configuration, monkeypatch):
    config, api, _ = configuration
    hideInactiveTriggerEdge(api, monkeypatch)
    with pytest.raises(HuarayCameraError, match="TriggerActivation"):
        config.apply({"triggerMode": "hardware", "triggerActivation": "FallingEdge"})
    assert api.writes == []


def testEdgeRestoreUsesHardwareContextAfterSwitchingToSoftware(configuration, monkeypatch):
    config, api, _ = configuration
    api.enums["TriggerSource"] = "Software"
    hideInactiveTriggerEdge(api, monkeypatch)
    config.refresh()
    config.apply({"triggerMode": "hardware", "triggerSource": "Line1"})
    config.apply({"triggerActivation": "FallingEdge"})
    config.apply({"triggerMode": "software"})
    assert "TriggerActivation" not in config.actual
    config.restore()
    assert api.enums["TriggerActivation"] == "RisingEdge"
    assert api.enums["TriggerSource"] == "Software" and api.enums["TriggerMode"] == "Off"
    assert not config.pending
