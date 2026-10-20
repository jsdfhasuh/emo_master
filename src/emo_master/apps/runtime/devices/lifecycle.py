"""Session controls; driver calls are made only by the device's FIFO thread."""
from time import monotonic
from uuid import uuid4

from .executor import DeviceError


class DeviceLifecycle:
    def __init__(self, definition, driver, declaration=None):
        self.definition, self.driver = definition, driver
        self.declaration = declaration
        self.perOperation = declaration is not None and declaration.connectionLifetime == "per_operation"
        self.connected = False
        self.paused = False
        self.controlPending = False
        self.controlRevision = uuid4().hex
        self.lastUsed = monotonic()
        self.keepAlive = set()
        self.lastError = ""
        self.recoveryState = dict(state="IDLE", attempt=0)

    def admit(self):
        if self.paused or self.controlPending:
            raise DeviceError("E_DEVICE_PAUSED", "device admission is paused; explicitly resume the session")
        if not self.perOperation and self.definition.policies.get("connect") == "manual" and not self.connected:
            raise DeviceError("E_DEVICE_MANUAL_CONNECT", "device requires an explicit connection")

    def connect(self, cancel, deadline):
        if self.perOperation or self.connected:
            return
        from .recovery import connectWithRecovery
        try:
            connectWithRecovery(self.driver, self.definition, cancel, deadline,
                                progress=lambda state: self.recoveryState.update(state))
        except BaseException as error:
            self.recoveryState.update(state="FAILED", lastError=str(error))
            raise
        self.recoveryState.update(state="CONNECTED", lastError="")
        self.connected = True
        self.lastUsed = monotonic()

    def touched(self):
        self.lastUsed = monotonic()

    def disconnect(self, deadline=None):
        try:
            self.driver.close(deadline)
        except BaseException as error:
            self.lastError = str(error)
            raise DeviceError("E_DEVICE_RELEASE_PENDING", str(error), outcome="UNKNOWN", unhealthy=True) from error
        self.connected = False

    def idleAction(self):
        # Called under the executor condition, only when its FIFO is empty.
        if self.perOperation or self.declaration is not None and self.declaration.operationScheduling == "managed_service":
            return None
        if not self.connected or self.paused or self.controlPending or self.keepAlive:
            return None
        held = getattr(self.driver, "holdsConnection", None)
        if callable(held) and held():
            return None
        policy = self.definition.policies.get("release", "on_project_close")
        if policy == "when_unused" or (policy == "idle_timeout" and
                monotonic() - self.lastUsed >= self.definition.policies["idleTimeoutMs"] / 1000):
            return lambda _cancel, _deadline, _generation: self.disconnect()
        return None

    def snapshot(self):
        held = getattr(self.driver, "holdsConnection", None)
        return dict(admissionState="CONTROL_PENDING" if self.controlPending else "PAUSED" if self.paused else "READY",
                    controlRevision=self.controlRevision,
                    lifecycleConnected=self.connected, explicitKeepAlive=len(self.keepAlive),
                    recovery=dict(self.recoveryState),
                    streamKeepAlive=bool(callable(held) and held()), lifecycleError=self.lastError)
