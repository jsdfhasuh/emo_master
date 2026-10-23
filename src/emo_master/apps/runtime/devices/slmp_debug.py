"""PLC diagnostic lease over DeviceManager, never a second socket owner."""
from threading import Event
from uuid import uuid4

from .executor import DeviceError


class ManagedSlmpDebug:
    managed = True

    def __init__(self, manager, deviceId):
        self.manager, self.deviceId = manager, deviceId
        self.owner = "plc-debug-" + uuid4().hex
        self.closed = Event()
        manager.authorizeMaintenance(self.owner, deviceId)

    def _call(self, action, arguments):
        if self.closed.is_set():
            raise DeviceError("E_DEVICE_CANCELLED", "managed PLC diagnostic lease closed")
        config = self.manager.configuration
        cycle = config.beginCycle(self.owner, -1, self.closed.is_set) if config and config.current else {"cycleId": self.owner}
        try:
            return self.manager.execute(self.owner, self.deviceId, action, {}, arguments=arguments,
                                        cycleId=cycle["cycleId"], cancelled=self.closed.is_set)
        finally:
            executor = self.manager.executors[self.deviceId]
            with executor.condition:
                retired = executor.condition.wait_for(
                    lambda: executor.active is None or executor.active.owner != self.owner, timeout=2)
            if not retired:
                raise DeviceError("E_DEVICE_RELEASE_PENDING", "diagnostic operation still owns device", outcome="UNKNOWN")
            if config and config.current:
                config.endCycle(self.owner, cycle["cycleId"])

    def open(self):
        self.manager.connectDiagnostic(self.owner, self.deviceId, self.closed.is_set)

    def readWords(self, device, start, count):
        return self._call("read", dict(device=device, startAddress=start, count=count))["words"]

    def writeWords(self, device, start, values):
        self._call("write", dict(device=device, startAddress=start, words=values))

    def readBits(self, start, count):
        return self._call("read", dict(unit="bit", device="M", startAddress=start, count=count))["words"]

    def writeBits(self, start, values):
        self._call("write", dict(unit="bit", device="M", startAddress=start, words=[int(v) for v in values]))

    def close(self):
        self.closed.set()
        self.manager.revoke(self.owner)

    def retire(self):
        self.manager.retire(self.owner)
