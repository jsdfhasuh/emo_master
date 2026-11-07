"""The final exposure wait must still honour cancellation and its deadline."""
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.devices import simulated
from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.core.project.models import ProjectDocument
from tests.runtime.test_device_runtime import simulatedProject


@pytest.mark.parametrize(
    "resumed_at,cancel_on_resume,expected_code",
    [(11.06, False, "E_DEVICE_OPERATION_TIMEOUT"),
     (1.001, True, "E_DEVICE_CANCELLED"),
     (1.001, False, None)],
    ids=["deadline-during-final-wait", "cancel-during-final-wait", "normal-exposure"],
)
def testFinalExposureWaitChecksOutcomeBeforeProducingFrame(
        monkeypatch, resumed_at, cancel_on_resume, expected_code):
    definition = ProjectDocument.model_validate(simulatedProject()).devices.definitions["camera"]
    camera = simulated.SimulatedCamera(definition)
    clock = SimpleNamespace(now=0.0, cancelled=False, waits=0)

    def finish_wait(_duration):
        clock.waits += 1
        clock.now = resumed_at
        clock.cancelled = cancel_on_resume

    # Replace only this driver's clock, not global time or the executor's clock.
    monkeypatch.setattr(simulated, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=finish_wait))
    params = dict(width=7, height=5, exposureUs=1_000_000, gain=0)
    if expected_code:
        with pytest.raises(DeviceError) as error:
            camera.execute("capture", params, lambda: clock.cancelled, deadline=5.0)
        assert error.value.code == expected_code
        assert error.value.outcome == "CANCELLED"
        assert camera.sequence == 0  # No acquisition side effect or late frame.
    else:
        frame = camera.execute("capture", params, lambda: clock.cancelled, deadline=5.0)
        assert frame.shape == (5, 7, 3)
        assert camera.sequence == 1
        assert (frame[:, :, 1] == 1).all()
    assert clock.waits == 1
