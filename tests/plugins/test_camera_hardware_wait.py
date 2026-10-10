from threading import Event, Thread
from time import monotonic, sleep

import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken, CancellationRequested
from emo_master.plugins.builtins._huaray_imv import IMV_TIMEOUT
from emo_master.plugins.builtins.huaray_camera.operator import HuarayCameraOperator
from tests.plugins.test_huaray_camera_operator import _FakeImvApi


PARAMS = {"selectionMode": "ip", "ipAddress": "127.0.0.1", "triggerMode": "hardware",
          "waitMode": "hardware", "sequencePolicy": "contiguous", "captureTimeoutMs": 1, "retryCount": 0}


class TriggerApi(_FakeImvApi):
    def __init__(self):
        super().__init__()
        self.trigger = Event()
        self.waited = Event()
        self.timeSlices = []

    def get_frame(self, handle, timeoutMs):
        self.timeSlices.append(timeoutMs)
        self.waited.set()
        if not self.trigger.wait(timeoutMs / 1000):
            return IMV_TIMEOUT, None
        self.trigger.clear()
        return super().get_frame(handle, timeoutMs)


def testWaitPastOldTimeoutAndBoundedCancellation():
    api = TriggerApi()
    operator = HuarayCameraOperator(lambda: api)
    token = CancellationToken()
    result, exceptions = [], []
    def capture():
        try:
            result.append(operator.executeNode({}, PARAMS, {"raiseIfCancellationRequested": token.raise_if_cancelled}))
        except Exception as exc:
            exceptions.append(exc)
    thread = Thread(target=capture)
    thread.start()
    try:
        assert api.waited.wait(1)
        sleep(.13)
        assert thread.is_alive() and not result
        start = monotonic()
        token.cancel()
        thread.join(1)
        assert not thread.is_alive()
        assert monotonic() - start < .3
        assert len(exceptions) == 1 and isinstance(exceptions[0], CancellationRequested)
        assert not api.commands and api.openCount == 1
        assert all(0 < n <= 100 for n in api.timeSlices)
    finally:
        token.cancel()
        thread.join(1)
        operator.disposeOperator()
    assert (api.stopCount, api.closeCount, api.destroyCount) == (1, 1, 1)


@pytest.mark.parametrize("gap", [0, 2])
def testDuplicateOrMissingFrameStopsWithoutReconnect(gap):
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)
    context = {"raiseIfCancellationRequested": CancellationToken().raise_if_cancelled}
    first = operator.executeNode({}, PARAMS, context)
    assert first["status"] == "ok"
    api.blockId += gap - 1  # fake SDK increments at acquisition
    second = operator.executeNode({}, PARAMS, context)
    assert second["error"]["code"] == "E_CAMERA_SEQUENCE_UNCERTAIN"
    assert api.openCount == 1
    assert (api.stopCount, api.closeCount, api.destroyCount) == (1, 1, 1)


def testHardwareWaitConfigurationAndPreviewRemainSafe():
    operator = HuarayCameraOperator(lambda: _FakeImvApi())
    assert operator.validateParams(dict(PARAMS, retryCount=1))
    assert operator.validateParams(dict(PARAMS, triggerMode="software"))
    assert operator.executeNode({}, PARAMS, {})["status"] == "error"
    result = operator.executeNode({}, dict(PARAMS, triggerMode="freeRun"), {"isPreview": True})
    assert result["status"] == "ok"
    operator.disposeOperator()


def testProductionConfigurationChangeStopsInsteadOfReopening():
    api = _FakeImvApi()
    operator = HuarayCameraOperator(lambda: api)
    context = {"raiseIfCancellationRequested": CancellationToken().raise_if_cancelled}
    assert operator.executeNode({}, PARAMS, context)["status"] == "ok"
    changed = operator.executeNode({}, dict(PARAMS, triggerSource="Line2"), context)
    assert changed["error"]["code"] == "E_CAMERA_SESSION_CONFIG_CHANGED"
    assert api.openCount == 1 and api.releaseCount == 1
    assert api.destroyCount == 1
