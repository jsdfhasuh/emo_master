from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.recovery import connectWithRecovery
from tests.runtime.test_managed_slmp import managerFor, plcProject, execute
from tests.runtime.plc_debug_server import PlcDebugServer


class Driver:
    connectionRetryCodes = {'IO'}

    def __init__(self, *, outcome='NOT_EXECUTED', code='IO'):
        self.calls = self.resets = 0
        self.outcome, self.code = outcome, code

    def connect(self, params, cancel, deadline):
        self.calls += 1
        raise DeviceError(self.code, 'connection failed', outcome=self.outcome, unhealthy=True)

    def resetConnection(self):
        self.resets += 1


def definition(**changes):
    return SimpleNamespace(defaults={}, policies=dict(connectionRetryCount=3,
        recoveryTimeoutMs=100, recoveryBackoffMs=1, operationTimeoutMs=100, **changes))


@pytest.mark.parametrize('outcome,code,count', [('NOT_EXECUTED', 'IO', 4), ('UNKNOWN', 'IO', 1),
    ('REJECTED', 'IO', 1), ('NOT_EXECUTED', 'UNSUPPORTED', 1)])
def testRetryRequiresProvenUnsentAndExplicitDriverCode(outcome, code, count):
    driver = Driver(outcome=outcome, code=code)
    with pytest.raises(DeviceError):
        connectWithRecovery(driver, definition(), lambda: False, monotonic() + 1)
    assert driver.calls == count and driver.resets == count - 1


def testRecoveryDeadlineDoesNotRefreshBetweenRetries():
    driver = Driver()
    config = definition()
    config.policies.update(recoveryTimeoutMs=20, recoveryBackoffMs=50)
    start = monotonic()
    with pytest.raises(DeviceError, match='total budget') as error:
        connectWithRecovery(driver, config, lambda: False, start + 1)
    assert error.value.outcome == 'NOT_EXECUTED'
    assert driver.calls == 1 and monotonic() - start < .5


def testRecoveryBackoffCanCancelWithoutAnotherAttempt():
    driver, cancel = Driver(), Event()
    def progress(state):
        if state['state'] == 'BACKOFF':
            cancel.set()
    with pytest.raises(DeviceError, match='cancelled'):
        connectWithRecovery(driver, definition(), cancel.is_set, monotonic() + 1, progress=progress)
    assert driver.calls == 1


def testFiniteConnectRetryThenOneBusinessWrite(monkeypatch):
    with PlcDebugServer() as peer:
        payload = plcProject(peer.port)
        payload['devices']['definitions']['plc']['policies'] = dict(connectionRetryCount=2,
            recoveryTimeoutMs=1000, recoveryBackoffMs=1)
        manager = managerFor(payload)
        driver = manager.drivers['plc']
        original = driver.session.open
        attempts = []
        def flakyOpen():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError('synthetic connect failure before sending')
            return original()
        monkeypatch.setattr(driver.session, 'open', flakyOpen)
        try:
            result = execute(manager, 'write', dict(device='D', startAddress=800, words=[432]))
            assert result['outcome'] == 'COMPLETED'
            assert len(attempts) == 2 and len(peer.requests) == 1 and peer.words[800] == 432
            assert manager.lifecycles['plc'].snapshot()['recovery']['attempt'] == 2
        finally:
            manager.close()


def testOtherDeviceAndQueuedWaitBudgetRemainIndependent():
    from emo_master.apps.runtime.devices.executor import DeviceExecutor
    first, other = DeviceExecutor('recovering'), DeviceExecutor('unrelated')
    backingOff = Event()
    config = definition()
    config.policies.update(recoveryTimeoutMs=200, recoveryBackoffMs=150)
    driver = Driver()
    try:
        recovery = first.submit('recover', lambda cancel, deadline, generation:
            connectWithRecovery(driver, config, cancel, deadline,
                progress=lambda state: backingOff.set() if state['state'] == 'BACKOFF' else None))
        assert backingOff.wait(2)
        queued = first.submit('waiter', lambda *_: pytest.fail('expired queued action executed'), waitTimeout=.01)
        independent = other.submit('other', lambda *_: 'ok')
        assert other.wait(independent) == 'ok'
        with pytest.raises(DeviceError) as expired:
            first.wait(queued)
        assert expired.value.code == 'E_DEVICE_WAIT_TIMEOUT' and expired.value.outcome == 'NOT_EXECUTED'
        first.cancel(recovery)
        with pytest.raises(DeviceError, match='cancelled'):
            recovery.future.result(2)
    finally:
        first.close()
        other.close()
