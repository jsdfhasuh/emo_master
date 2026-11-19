"""Managed Huaray IMV camera and its isolated SDK process."""
from .camera_host import ManagedCameraHost, runCameraHost


def runImvHost(pipe, cancellation, connection, generation, apiFactory=None):
    from .imv_capture import ImvCapture
    runCameraHost(pipe, cancellation, generation, ImvCapture(connection, apiFactory))


class ManagedImv(ManagedCameraHost):
    hostTarget = staticmethod(runImvHost)
    hostName = "imv-host-"
    actions = ManagedCameraHost.actions | {"read_parameters", "trial_capture"}

    @staticmethod
    def physicalIdentity(definition):
        # The SDK device key is stable across IP, user-name and enumeration changes.
        key = definition.connection["cameraKey"]
        if not key.strip() or key != key.strip() or len(key.encode("utf-8")) > 255 or "\x00" in key:
            raise ValueError("cameraKey must be an exact nonempty SDK device key within 255 UTF-8 bytes")
        return "huaray-imv:" + key
