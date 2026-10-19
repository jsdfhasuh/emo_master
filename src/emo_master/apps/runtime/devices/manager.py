"""Project-scoped authority for device identity, operations, and original frames."""
from copy import deepcopy
from contextlib import nullcontext
import platform
from threading import RLock
from time import monotonic
from uuid import uuid4

from .executor import DeviceError, DeviceExecutor
from .frame_store import FrameStore
from .lifecycle import DeviceLifecycle
from .recovery import connectionBudget


class DeviceManager:
    def __init__(self, types):
        self.types = types
        self.lock = RLock()
        self.definitions = {}
        self.executors = {}
        self.drivers = {}
        self.identities = {}
        self.lifecycles = {}
        self.frames = FrameStore()
        self.workers = {}
        self.permissions = {}
        self.maintenanceOwners = set()
        self.configuration = None
        self.replacement = None
        self.dispatchGroups = {}
        self.resourceLeasePath = None
        self.frameObserver = None

    def _validatedDefinitions(self, document):
        definitions = {}
        identities = {}
        classes = {}
        for key, definition in document.devices.definitions.items():
            declaration = self.types.get(definition.deviceTypeId)
            if declaration is None or not declaration.driverEntry:
                raise DeviceError("E_DEVICE_DRIVER_UNAVAILABLE", "device has no available managed driver: " + key)
            if platform.system().lower() not in declaration.platforms:
                raise DeviceError("E_DEVICE_PLATFORM", "device driver does not support this Runtime platform")
            definition = declaration.validateDefinition(definition)
            from .drivers import resolveDriver
            driver = resolveDriver(declaration.driverEntry)
            if not callable(getattr(driver, "connect", None)):
                raise DeviceError("E_DEVICE_DRIVER_CONTRACT", "managed drivers must provide a connect-only operation")
            identity = driver.physicalIdentity(definition)
            if identity in identities.values():
                raise DeviceError("E_DEVICE_DUPLICATE_IDENTITY", "reuse the existing device for identity: " + identity)
            identities[key], definitions[key], classes[key] = identity, definition, driver
        return definitions, identities, classes

    def prepareReplacement(self, document, *, force=False):
        from .replacement import PreparedDevices
        definitions, identities, classes = self._validatedDefinitions(document)
        with self.lock:
            return PreparedDevices(self, definitions, identities, classes, force=force)

    def _checkReplacement(self, deviceIds):
        if self.replacement is not None and self.replacement.changed.intersection(deviceIds):
            raise DeviceError("E_DEVICE_REPLACING", "affected device admission is paused for configuration replacement")

    def load(self, document):
        definitions, identities, classes = self._validatedDefinitions(document)
        with self.lock:
            if self.replacement is not None:
                raise DeviceError("E_DEVICE_BUSY", "device replacement is pending")
            if self.workers or self.maintenanceOwners:
                raise DeviceError("E_DEVICE_BUSY", "device workers must retire before project replacement")
            if any(executor.active or executor.queue for executor in self.executors.values()):
                raise DeviceError("E_DEVICE_BUSY", "device operations must retire before replacement")
            drivers, executors, lifecycles = {}, {}, {}
            try:
                # Constructors only prepare adapters. A malformed replacement must
                # not destroy the existing device authority before it is prepared.
                for key, value in definitions.items():
                    drivers[key] = classes[key](value)
                    if hasattr(drivers[key], 'resourceLeasePath'):
                        drivers[key].resourceLeasePath = self.resourceLeasePath
                    lifecycles[key] = DeviceLifecycle(value, drivers[key])
                    executors[key] = DeviceExecutor(identities[key], idleAction=lifecycles[key].idleAction)
                self._closeDevices()
                self.frames.close()
            except BaseException:
                for executor in executors.values():
                    executor.close()
                for driver in drivers.values():
                    driver.close()
                raise
            self.frames = FrameStore()
            self.definitions, self.identities = definitions, identities
            self.permissions.clear()
            self.drivers, self.executors = drivers, executors
            self.lifecycles = lifecycles

    def productionOwners(self, deviceId):
        with self.lock:
            return sorted(owner for owner, ids in self.permissions.items()
                          if deviceId in ids and owner not in self.maintenanceOwners)

    def execute(self, owner, deviceId, action, params, *, cycleId, arguments=None, cancelled=lambda: False,
                previewOnly=False):
        if cancelled():
            raise DeviceError("E_DEVICE_CANCELLED", "request cancelled before device admission")
        planRevision = (self.configuration.requireCycle(owner, cycleId)
                        if self.configuration is not None and self.configuration.current is not None else 0)
        with self.lock:
            self._checkReplacement([deviceId])
            if deviceId not in self.permissions.get(owner, set()):
                raise DeviceError("E_DEVICE_OWNER", "Job has no access to this device")
            definition = self.definitions[deviceId]
            if not definition.enabled:
                raise DeviceError("E_DEVICE_DISABLED", "device is disabled")
            declaration = self.types[definition.deviceTypeId]
            if action not in declaration.capabilities:
                raise DeviceError("E_DEVICE_ACTION", "action not declared by device type")
            # Validate again at the authority, before admission and any side effect.
            from emo_master.core.project.snapshots import _parameters
            params = _parameters(deepcopy(params), declaration.defaultsSchema, "device parameters")
            declaration.validateRules(params)
            production = self.productionOwners(deviceId)
            if previewOnly and production:
                raise DeviceError('E_CAMERA_PRODUCTION_ACTIVE', 'production started; watch existing results instead')
            if owner in self.maintenanceOwners and action == 'capture' and params.get('triggerMode') == 'hardware' and (previewOnly or production):
                raise DeviceError('E_CAMERA_PREVIEW_UNSAFE', 'hardware-triggered preview cannot consume production triggers')
            observe = action == 'capture' and owner not in self.maintenanceOwners
            executor, driver = self.executors[deviceId], self.drivers[deviceId]
            lifecycle = self.lifecycles[deviceId]
            with executor.condition:
                lifecycle.admit()
            arguments = deepcopy(arguments) if arguments is not None else {}
            actionSchema = declaration.actionSchemas.get(action)
            if actionSchema is not None:
                try:
                    arguments = _parameters(arguments, actionSchema, "device action")
                    driver.validateAction(action, arguments)
                except (ValueError, TypeError) as error:
                    raise DeviceError("E_DEVICE_PARAMETERS", str(error)) from error
            elif arguments:
                raise DeviceError("E_DEVICE_ACTION", "device action does not accept arguments")
            reservation = self.frames.reserve(owner, driver.frameBytes(params)) if action == "capture" else None
            policies = definition.policies
            unlimited = declaration.isUnlimitedWait(action, params)
            operationTimeout = policies.get("operationTimeoutMs", 5000) / 1000
            connectTimeout = connectionBudget(policies)
            requestId = uuid4().hex
            def perform(cancel, deadline, generation):
                try:
                    def stopped():
                        return cancel() or cancelled()
                    if stopped():
                        raise DeviceError("E_DEVICE_CANCELLED", "request cancelled before connection")
                    if lifecycle.definition.policies.get("connect") == "manual" and not lifecycle.connected:
                        raise DeviceError("E_DEVICE_MANUAL_CONNECT", "manual connection was released before operation admission")
                    lifecycle.connect(stopped, min(deadline, monotonic() + connectTimeout))
                    if stopped() or monotonic() >= deadline:
                        raise DeviceError("E_DEVICE_CANCELLED" if stopped() else "E_DEVICE_OPERATION_TIMEOUT",
                                          "device action abandoned after connection, before execution")
                    actionDeadline = deadline if unlimited else min(deadline, monotonic() + operationTimeout)
                    result = (driver.execute(action, params, stopped, actionDeadline, arguments=arguments)
                              if actionSchema is not None else driver.execute(action, params, stopped, actionDeadline))
                    if reservation:
                        metadata = None
                        if declaration.executionLocation == "camera_host":
                            result, metadata = result
                        descriptor = self.frames.publish(reservation, result, cycleId=cycleId,
                            generation=generation, requestId=requestId, planRevision=planRevision, metadata=metadata)
                        if observe and self.frameObserver is not None:
                            # Observation owns only a bounded JPEG copy, never the
                            # original lease or admission of another acquisition.
                            self.frameObserver(deviceId, owner, result, descriptor)
                        return descriptor
                    return result
                finally:
                    lifecycle.touched()
                    if reservation:
                        self.frames.discardReservation(reservation)
            try:
                operation = executor.submit(owner, perform,
                    requestId=requestId,
                    waitTimeout=policies.get("deviceWaitTimeoutMs", 5000) / 1000,
                    operationTimeout=float("inf") if unlimited else connectTimeout, cancellableWait=unlimited)
            except BaseException:
                if reservation:
                    self.frames.discardReservation(reservation)
                raise
            if reservation:
                operation.future.add_done_callback(lambda _future: self.frames.discardReservation(reservation))
        return executor.wait(operation, cancelled)

    def testConnection(self, value, cancelled=lambda: False, *, admissionLock=None, admission=None):
        """Borrow the physical owner, or register a temporary owner until cleanup."""
        from types import SimpleNamespace
        from emo_master.core.project.devices import DeviceDefinition
        key, owner = 'test-' + uuid4().hex, 'setup-' + uuid4().hex
        definition = DeviceDefinition.model_validate(value)
        definitions, identities, classes = self._validatedDefinitions(
            SimpleNamespace(devices=SimpleNamespace(definitions={key: definition})))
        temporary = False
        with (admissionLock or nullcontext()), self.lock:
            if admission is not None:
                admission()
            if self.replacement is not None:
                raise DeviceError('E_DEVICE_REPLACING', 'configuration replacement is pending')
            existing = next((k for k, identity in self.identities.items() if identity == identities[key]), None)
            if existing is not None:
                key = existing
                if self.definitions[key].connection != definition.connection:
                    raise DeviceError('E_DEVICE_IDENTITY', 'existing physical device has different connection settings')
            else:
                driver = classes[key](definitions[key])
                self.definitions[key], self.identities[key], self.drivers[key] = definitions[key], identities[key], driver
                self.lifecycles[key] = DeviceLifecycle(definitions[key], driver)
                self.executors[key] = DeviceExecutor(identities[key], idleAction=self.lifecycles[key].idleAction)
                temporary = True
            self.authorizeMaintenance(owner, key)
            executor, lifecycle = self.executors[key], self.lifecycles[key]
            lifecycle.keepAlive.add(owner)
            def perform(cancel, deadline, _generation):
                wasConnected = lifecycle.connected
                try:
                    lifecycle.connect(lambda: cancel() or cancelled(), deadline)
                    return dict(connected=True, reused=not temporary, deviceId='' if temporary else key)
                finally:
                    if not wasConnected:
                        lifecycle.disconnect()
            try:
                operation = executor.submit(owner, perform, waitTimeout=5,
                    operationTimeout=connectionBudget(definition.policies))
            except BaseException:
                lifecycle.keepAlive.discard(owner)
                self.retire(owner)
                raise
        try:
            return executor.wait(operation, cancelled)
        finally:
            # The native owner, not RPC abandonment, determines when it is safe
            # to remove temporary identity and release the physical connection.
            with executor.condition:
                executor.condition.wait_for(lambda: executor.active is None or executor.active.owner != owner)
            with self.lock:
                lifecycle.keepAlive.discard(owner)
                self.retire(owner)
                if temporary and not lifecycle.connected:
                    executor.close()
                    for mapping in (self.definitions, self.identities, self.drivers, self.lifecycles, self.executors):
                        mapping.pop(key, None)

    def cameraDiagnostic(self, owner, deviceId, action, changes, cancelled):
        if action not in {'read_parameters', 'trial_capture'}:
            raise DeviceError('E_DEVICE_ACTION', 'unsupported camera diagnostic')
        if not isinstance(changes, dict) or set(changes) - {'exposureMode', 'exposureUs', 'gainMode', 'gainRaw'}:
            raise DeviceError('E_DEVICE_PARAMETERS', 'trial accepts exposure and gain only')
        with self.lock:
            self._checkReplacement([deviceId])
            if deviceId not in self.permissions.get(owner, set()) or owner not in self.maintenanceOwners:
                raise DeviceError('E_DEVICE_OWNER', 'diagnostic owner is not authorized')
            definition = self.definitions[deviceId]
            if not definition.enabled:
                raise DeviceError('E_DEVICE_DISABLED', 'device is disabled')
            declaration = self.types[definition.deviceTypeId]
            if definition.deviceTypeId != 'device.camera.huaray_imv':
                raise DeviceError('E_DEVICE_ACTION', 'camera diagnostic requires IMV')
            checked = definition.model_copy(deep=True)
            checked.defaults.update(changes)
            declaration.validateDefinition(checked)
            executor, lifecycle, driver = self.executors[deviceId], self.lifecycles[deviceId], self.drivers[deviceId]
            with executor.condition:
                lifecycle.admit()
            def perform(cancel, deadline, _generation):
                def stopped():
                    return cancel() or cancelled()
                lifecycle.connect(stopped, deadline)
                try:
                    return driver.execute(action, definition.defaults, stopped, deadline, changes=changes)
                finally:
                    lifecycle.touched()
            operation = executor.submit(owner, perform, waitTimeout=definition.policies.get('deviceWaitTimeoutMs', 5000) / 1000,
                operationTimeout=connectionBudget(definition.policies) + definition.policies.get('operationTimeoutMs', 5000) / 1000)
        return executor.wait(operation, cancelled)

    def authorize(self, owner, deviceIds):
        with self.lock:
            self._checkReplacement(deviceIds)
            if not set(deviceIds) <= self.definitions.keys():
                raise DeviceError("E_DEVICE_REFERENCE", "unknown device in Job plan")
            self.permissions[owner] = set(deviceIds)

    def authorizeMaintenance(self, owner, deviceId):
        with self.lock:
            self.authorize(owner, [deviceId])
            self.maintenanceOwners.add(owner)

    def openWorker(self, owner, deviceIds, cancellation, context):
        from .ipc import DeviceBridge
        with self.lock:
            if owner in self.workers:
                raise DeviceError("E_DEVICE_OWNER", "duplicate device worker")
            self.authorize(owner, deviceIds)
            for key in deviceIds:
                if self.definitions[key].enabled and self.definitions[key].policies.get("connect") == "on_project_run":
                    self._connectEvent(key, owner)
            parent, child = context.Pipe()
            bridge = DeviceBridge(self, owner, parent, cancellation)
            self.workers[owner] = bridge
            bridge.thread.start()
            return child

    def revoke(self, owner):
        with self.lock:
            self.permissions.pop(owner, None)
            for executor in self.executors.values():
                executor.cancelOwner(owner)
            for lifecycle in self.lifecycles.values():
                lifecycle.keepAlive.discard(owner)

    def retire(self, owner):
        self.revoke(owner)
        bridge = self.workers.get(owner)
        if bridge:
            bridge.close()
        with self.lock:
            # A terminated Job is not proof that its in-flight native call returned.
            if any(executor.active is not None and executor.active.owner == owner
                   for executor in self.executors.values()):
                raise DeviceError("E_DEVICE_RELEASE_PENDING", "device operation still exiting")
            self.frames.retireOwner(owner)
            self.workers.pop(owner, None)
            self.maintenanceOwners.discard(owner)
        if self.configuration is not None:
            self.configuration.retireOwner(owner)

    def _closeDevices(self):
        for executor in self.executors.values():
            executor.close()
        for driver in self.drivers.values():
            driver.close()
        self.executors.clear()
        self.drivers.clear()
        self.lifecycles.clear()

    def _connectEvent(self, deviceId, owner):
        self._checkReplacement([deviceId])
        lifecycle, executor = self.lifecycles[deviceId], self.executors[deviceId]
        with executor.condition:
            if lifecycle.paused or lifecycle.controlPending or executor.quarantined:
                return
            return executor.submit(owner, lambda cancel, deadline, _generation: lifecycle.connect(cancel, deadline),
                operationTimeout=connectionBudget(self.definitions[deviceId].policies))

    def activate(self):
        """One committed online activation event, never a status-refresh action."""
        with self.lock:
            for key, definition in self.definitions.items():
                if definition.enabled and definition.policies.get("connect") == "on_project_open":
                    self._connectEvent(key, "__project_open__")

    def beginControl(self, deviceId, expectedGeneration, action, *, expectedControlRevision=None):
        if action not in {"connect", "disconnect", "maintenance"}:
            raise DeviceError("E_DEVICE_ACTION", "unsupported lifecycle control")
        with self.lock:
            self._checkReplacement([deviceId])
            executor = self.executors.get(deviceId)
            if executor is None or executor.generation != expectedGeneration:
                raise DeviceError("E_DEVICE_GENERATION", "device generation changed; read state again")
            lifecycle = self.lifecycles[deviceId]
            with executor.condition:
                if expectedControlRevision is not None and expectedControlRevision != lifecycle.controlRevision:
                    raise DeviceError("E_DEVICE_CONTROL_STALE", "device control intent changed; read state again")
                if lifecycle.controlPending:
                    raise DeviceError("E_DEVICE_BUSY", "another lifecycle control is pending")
                if not self.definitions[deviceId].enabled and action == "connect":
                    raise DeviceError("E_DEVICE_DISABLED", "device is disabled")
                if action != "maintenance" and (executor.active or executor.queue):
                    raise DeviceError("E_DEVICE_BUSY", "device is in use; explicitly request maintenance to cancel its operations")
                if action == "maintenance":
                    for operation in list(executor.queue):
                        executor.cancel(operation)
                    if executor.active:
                        executor.active.cancellation.set()
                oldPaused = lifecycle.paused
                oldRevision = lifecycle.controlRevision
                lifecycle.controlRevision = uuid4().hex
                lifecycle.controlPending = True
                # A disconnected or maintained session cannot be auto-reopened.
                lifecycle.paused = True

                def perform(cancel, deadline, _generation):
                    if action == "connect":
                        lifecycle.connect(cancel, deadline)
                        if cancel() or monotonic() >= deadline:
                            lifecycle.disconnect()
                            raise DeviceError("E_DEVICE_CANCELLED" if cancel() else "E_DEVICE_OPERATION_TIMEOUT",
                                              "connection control abandoned; admission remains paused")
                        lifecycle.paused = False
                    else:
                        lifecycle.disconnect()

                try:
                    operation = executor.submit("__device_control__", perform,
                        waitTimeout=600, operationTimeout=connectionBudget(self.definitions[deviceId].policies))
                except BaseException:
                    lifecycle.controlPending = False
                    lifecycle.paused = oldPaused
                    lifecycle.controlRevision = oldRevision
                    raise
                def completed(future):
                    with executor.condition:
                        lifecycle.controlPending = False
                        error = future.exception()
                        lifecycle.lastError = str(error) if error else ""
                operation.future.add_done_callback(completed)
                return executor, operation

    def setKeepAlive(self, owner, deviceId, enabled):
        with self.lock:
            if deviceId not in self.permissions.get(owner, set()):
                raise DeviceError("E_DEVICE_OWNER", "owner has no access to this device")
            with self.executors[deviceId].condition:
                if enabled:
                    self.lifecycles[deviceId].keepAlive.add(owner)
                else:
                    self.lifecycles[deviceId].keepAlive.discard(owner)

    def recover(self, deviceId, expectedGeneration):
        with self.lock:
            self._checkReplacement([deviceId])
            definition = self.definitions.get(deviceId)
            if definition is None or definition.deviceTypeId not in {"device.plc.mitsubishi_slmp", "device.camera.huaray_imv"}:
                raise DeviceError("E_DEVICE_ACTION", "device does not support explicit session recovery")
            previous = self.executors[deviceId]
            if previous.generation != expectedGeneration:
                raise DeviceError("E_DEVICE_GENERATION", "device generation changed; read state again")
            if (previous.active or previous.queue
                    or any(deviceId in ids for ids in self.permissions.values())):
                raise DeviceError("E_DEVICE_BUSY", "retire device Jobs and diagnostic leases before recovery")
            driver = type(self.drivers[deviceId])(definition)
            if hasattr(driver, 'resourceLeasePath'):
                driver.resourceLeasePath = self.resourceLeasePath
            lifecycle = DeviceLifecycle(definition, driver)
            lifecycle.paused = self.lifecycles[deviceId].paused
            executor = DeviceExecutor(self.identities[deviceId], idleAction=lifecycle.idleAction)
            try:
                previous.close()
                self.drivers[deviceId].close()
            except BaseException:
                executor.close()
                driver.close()
                raise
            self.drivers[deviceId], self.executors[deviceId] = driver, executor
            self.lifecycles[deviceId] = lifecycle

    def close(self):
        with self.lock:
            if self.replacement is not None:
                self.replacement.close()
            if self.workers or self.maintenanceOwners:
                raise DeviceError("E_DEVICE_RELEASE_PENDING", "device IPC workers still exist")
            self._closeDevices()
            self.frames.close()
