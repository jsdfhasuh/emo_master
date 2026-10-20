"""Explicit software-only IMV support; never loads the native camera SDK."""
from emo_master.apps.runtime.devices.imv_host import ManagedImv
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.plugins.test_camera_configuration import ConfigurationApi


IMV_TYPE = "device.camera.huaray_imv"


class SimulatedManagedImv(ManagedImv):
    def __init__(self, definition):
        # Inject before project activation can connect, including on_project_open.
        super().__init__(definition, apiFactory=ConfigurationApi)


def useSimulatedImv(manager):
    """Opt one test manager into the real Host/IPC contract with a fake SDK.

    Keep the built-in registry and its Windows-only declaration unchanged. The
    copied declaration supports Linux only with this test-only driver entry.
    """
    declaration = manager.types[IMV_TYPE].model_copy(deep=True, update={
        "platforms": ["windows", "linux"],
        "driverEntry": "tests.runtime.imv_test_support:SimulatedManagedImv",
        "displayName": "Simulated IMV (test only)",
    })
    manager.types = dict(manager.types, **{IMV_TYPE: declaration})


class SimulatedImvRuntimeService(RuntimeService):
    def _initializeOnlineConfiguration(self):
        # Startup recovery loads devices inside __init__, before tests can inject.
        useSimulatedImv(self.deviceManager)
        super()._initializeOnlineConfiguration()
