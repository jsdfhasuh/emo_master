"""Explicit built-in import edges for frozen Runtime dependency discovery."""
import importlib

from .imv_host import ManagedImv
from .simulated import SimulatedCamera
from .slmp import ManagedSlmp
from .tcp import ManagedTcpServer, ManagedTcpClientOnce, ManagedTcpReceiveOnce


BUILTINS = {f"{driver.__module__}:{driver.__name__}": driver
            for driver in (ManagedImv, SimulatedCamera, ManagedSlmp,
                           ManagedTcpServer, ManagedTcpClientOnce, ManagedTcpReceiveOnce)}


def resolveDriver(entry):
    if entry in BUILTINS:
        return BUILTINS[entry]
    module, name = entry.split(":")
    return getattr(importlib.import_module(module), name)


def validateDriver(declaration):
    from emo_master.core.contracts.device_driver import DeviceError
    driver = resolveDriver(declaration.driverEntry)
    methods = ['physicalIdentity', 'bindRuntime', 'validateAction', 'execute', 'revokeOwner',
               'requestStop', 'close', 'dispose', 'snapshot']
    if declaration.connectionLifetime == 'persistent':
        methods.append('connect')
    if 'test_connection' in declaration.controlCapabilities:
        methods.append('testConnection')
    if 'capture' in declaration.capabilities:
        methods.append('frameBytes')
    if declaration.policiesSchema.get('properties', {}).get('connectionRetryCount'):
        methods.append('resetConnection')
        if not isinstance(getattr(driver, 'connectionRetryCodes', None), (set, frozenset)):
            raise DeviceError('E_DEVICE_DRIVER_CONTRACT', 'connectionRetryCodes is required')
    missing = [name for name in methods if not callable(getattr(driver, name, None))]
    if missing:
        raise DeviceError('E_DEVICE_DRIVER_CONTRACT', f'{declaration.deviceTypeId}: missing {missing}')
    return driver
