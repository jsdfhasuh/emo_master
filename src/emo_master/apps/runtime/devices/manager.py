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
        self.services = {}
        self.tcpWorkers = {}
        self.runtimeInstanceId = uuid4().hex
        self.scope = dict(runtimeInstanceId=self.runtimeInstanceId, projectId='', projectLoadGeneration=uuid4().hex)
        from .diagnostics import DeviceDiagnostics
        self.diagnostics = DeviceDiagnostics()

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
            from .drivers import validateDriver
            driver = validateDriver(declaration)
            identity = driver.physicalIdentity(definition)
            if definition.enabled and identity is not None:
                for other, otherIdentity in identities.items():
                    if not definitions[other].enabled or otherIdentity is None:
                        continue
                    conflict = identity == otherIdentity
                    if identity.startswith('listener://') and otherIdentity.startswith('listener://'):
                        host, port = identity[11:].rsplit(':', 1)
                        oldHost, oldPort = otherIdentity[11:].rsplit(':', 1)
                        conflict |= port == oldPort and ('0.0.0.0' in {host, oldHost} or host == oldHost)
                    if conflict:
                        raise DeviceError('E_DEVICE_DUPLICATE_IDENTITY', f'{key} conflicts with {other}: {identity}')
            identities[key], definitions[key], classes[key] = identity, definition, driver
        return definitions, identities, classes

    def prepareReplacement(self, document, *, force=False):
        from .replacement import PreparedDevices
        definitions, identities, classes = self._validatedDefinitions(document)
        with self.lock:
            return PreparedDevices(self, definitions, identities, classes,
                                   scope=self.prepareScope(document, new=force), force=force)

    def _checkReplacement(self, deviceIds):
        if self.replacement is not None and self.replacement.changed.intersection(deviceIds):
            raise DeviceError("E_DEVICE_REPLACING", "affected device admission is paused for configuration replacement")

    def prepareScope(self, document, *, new=False):
        if not new and self.scope['projectId'] == document.project.projectId:
            return dict(self.scope)
        return dict(runtimeInstanceId=self.runtimeInstanceId, projectId=document.project.projectId,
                    projectLoadGeneration=uuid4().hex)

    def makeBackend(self, key, definition, driverClass, scope):
        from emo_master.core.contracts.device_driver import DeviceRuntimeContext
        driver = driverClass(definition)
        if hasattr(driver, 'resourceLeasePath'):
            driver.resourceLeasePath = self.resourceLeasePath
        lifecycle = DeviceLifecycle(definition, driver, self.types[definition.deviceTypeId])
        executor = DeviceExecutor(key, idleAction=lifecycle.idleAction)
        try:
            driver.bindRuntime(DeviceRuntimeContext(**scope, deviceId=key, generation=executor.generation,
                events=lambda eventType, message, **fields: self.diagnostics.append(scope, key, eventType, message,
                    **dict(fields, deviceGeneration=executor.generation))))
        except BaseException:
            executor.close()
            driver.dispose(monotonic()+5)
            raise
        return driver, executor, lifecycle

    def load(self, document):
        definitions, identities, classes = self._validatedDefinitions(document)
        scope = self.prepareScope(document, new=True)
        with self.lock:
            if self.replacement is not None or self.workers or self.maintenanceOwners or self.busy():
                raise DeviceError('E_DEVICE_BUSY', 'retire device owners before project replacement')
        prepared = {}
        try:
            for key, value in definitions.items():
                prepared[key] = self.makeBackend(key, value, classes[key], scope)
            self._closeDevices()
            self.frames.close()
        except BaseException:
            for driver, executor, _ in prepared.values():
                executor.close()
                driver.dispose(monotonic()+5)
            raise
        from .service_executor import ServiceExecutor
        with self.lock:
            self.scope = scope
            self.frames = FrameStore()
            self.definitions, self.identities = definitions, identities
            self.permissions.clear()
            self.drivers = {key: item[0] for key, item in prepared.items()}
            self.executors = {key: item[1] for key, item in prepared.items()}
            self.lifecycles = {key: item[2] for key, item in prepared.items()}
            self.services = {key: ServiceExecutor(self.executors[key]) for key in definitions}

    def busy(self, deviceId=None, owner=None, cycleId=None):
        keys = [deviceId] if deviceId else list(self.executors)
        return any((self.executors[key].active is not None and
                    (owner is None or self.executors[key].active.owner == owner))
                   or any(owner is None or op.owner == owner for op in self.executors[key].queue)
                   or self.services.get(key) is not None and self.services[key].busy(owner, cycleId)
                   for key in keys)

    def waitOwner(self, owner, cycleId=None):
        deadline = monotonic()+5
        for key, executor in list(self.executors.items()):
            with executor.condition:
                if not executor.condition.wait_for(lambda: executor.active is None or executor.active.owner != owner,
                                                    timeout=max(0, deadline-monotonic())):
                    raise DeviceError('E_DEVICE_RELEASE_PENDING', 'owner operation still exiting')
            self.services[key].wait(owner, cycleId, deadline)

    def productionOwners(self, deviceId):
        with self.lock:
            return sorted(owner for owner, ids in self.permissions.items()
                          if deviceId in ids and owner not in self.maintenanceOwners)

    def availableControls(self, deviceId):
        lifecycle, executor = self.lifecycles[deviceId], self.executors[deviceId]
        controls = set(self.types[self.definitions[deviceId].deviceTypeId].controlCapabilities)
        if lifecycle.controlPending or self.replacement is not None or executor.closed:
            return []
        if executor.quarantined:
            return sorted(controls & {'disconnect', 'maintenance', 'recover'})
        if lifecycle.connected:
            controls.discard('connect')
        else:
            controls.discard('disconnect')
        if not lifecycle.paused:
            controls.discard('resume')
        if not self.definitions[deviceId].enabled:
            controls &= {'disconnect', 'maintenance', 'recover', 'test_connection'}
        return sorted(controls)

    def availableManagementActions(self, deviceId):
        lifecycle, executor = self.lifecycles[deviceId], self.executors[deviceId]
        definition = self.definitions[deviceId]
        declaration = self.types[definition.deviceTypeId]
        if (not definition.enabled or lifecycle.paused or lifecycle.controlPending
                or self.replacement is not None or executor.closed or executor.quarantined
                or declaration.operationScheduling == 'managed_service' and not lifecycle.connected
                or definition.policies.get('connect') == 'manual' and not lifecycle.connected):
            return []
        return list(declaration.managementActions)

    def execute(self, owner, deviceId, action, params, *, cycleId, arguments=None, cancelled=lambda: False,
                previewOnly=False, source=None, selectors=None, requestDeadline=None, requestId=None):
        if cancelled():
            raise DeviceError("E_DEVICE_CANCELLED", "request cancelled before device admission")
        planRevision = (self.configuration.requireCycle(owner, cycleId)
                        if self.configuration is not None and self.configuration.current is not None else 0)
        from emo_master.core.contracts.device_driver import DeviceOperationContext
        source = deepcopy(source or {})
        selectors = deepcopy(selectors or {})
        if self.configuration is not None and self.configuration.current is not None and owner not in self.maintenanceOwners:
            frozen = self.configuration.cyclePayload(owner, cycleId)
            try:
                node = next(node for node in frozen['workflows'][source['workflowId']]['nodes'] if node['nodeId'] == source['nodeId'])
                binding = node['deviceBindings'][source['slot']]
                if binding['deviceId'] != deviceId or binding.get('selectors', {}) != selectors:
                    raise ValueError('device binding differs from frozen cycle')
            except (KeyError, StopIteration, ValueError) as error:
                raise DeviceError('E_DEVICE_BINDING', 'request does not match frozen node/slot/selectors') from error
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
            requestId = requestId or uuid4().hex
            operationContext = DeviceOperationContext(ownerId=owner, cycleId=cycleId, planRevision=planRevision,
                slot=source.get('slot', ''), selectors=selectors, workflowId=source.get('workflowId', ''),
                nodeId=source.get('nodeId', ''), jobId='' if owner in self.maintenanceOwners else owner,
                requestId=requestId, source=source)
            tcp = definition.deviceTypeId.startswith('device.tcp.')
            budget = None
            if tcp:
                from emo_master.core.contracts.tcp_service import tcpActionBudget
                budget = tcpActionBudget(definition.deviceTypeId, action, params, policies)
                operationTimeout = budget['actionBudgetMs']/1000
            service = declaration.operationScheduling == 'managed_service'
            if service and not lifecycle.connected:
                raise DeviceError('E_TCP_NOT_RUNNING', f'{deviceId}: 服务尚未启动，请按策略启动或在设备页启动服务')
            def perform(cancel, deadline, generation):
                try:
                    def stopped():
                        return cancel() or cancelled()
                    if stopped():
                        raise DeviceError("E_DEVICE_CANCELLED", "request cancelled before connection")
                    if declaration.connectionLifetime == "persistent" and lifecycle.definition.policies.get("connect") == "manual" and not lifecycle.connected:
                        raise DeviceError("E_DEVICE_MANUAL_CONNECT", "manual connection was released before operation admission")
                    if declaration.connectionLifetime == "persistent" and not service:
                        lifecycle.connect(stopped, min(deadline, monotonic() + connectTimeout))
                    if stopped() or monotonic() >= deadline:
                        raise DeviceError("E_DEVICE_CANCELLED" if stopped() else "E_DEVICE_OPERATION_TIMEOUT",
                                          "device action abandoned after connection, before execution")
                    deadline = min(deadline, requestDeadline or float("inf"))
                    actionDeadline = deadline if unlimited else min(deadline, monotonic() + operationTimeout)
                    result = driver.execute(action, params, stopped, actionDeadline,
                        arguments=arguments, operationContext=operationContext)
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
            if service:
                with executor.condition:
                    lifecycle.admit()
                    operation = self.services[deviceId].admit(owner, cycleId)
            else:
                operation = None
            try:
                if not service:
                    operation = executor.submit(owner, perform,
                    requestId=requestId,
                    waitTimeout=policies.get("deviceWaitTimeoutMs", 5000) / 1000,
                    operationTimeout=float("inf") if unlimited else operationTimeout if tcp else connectTimeout, cancellableWait=unlimited)
            except BaseException:
                if reservation:
                    self.frames.discardReservation(reservation)
                raise
            if reservation:
                operation.future.add_done_callback(lambda _future: self.frames.discardReservation(reservation))
            events = driver.runtimeContext.events
            def completed(future):
                error = future.exception()
                fields = dict(ownerId=owner, cycleId=cycleId, requestId=requestId,
                    source=source, selectors=selectors)
                if error is not None:
                    events('device.action.failed', str(error), **fields,
                        code=getattr(error, 'code', 'E_DEVICE_ERROR'), diagnostics=getattr(error, 'diagnostics', {}))
                else:
                    from .diagnostics import compactResult
                    events('device.action.completed', action, **fields,
                        result={'frame': True} if reservation else compactResult(future.result()))
            # The native result is recorded even after the RPC caller stops waiting.
            operation.future.add_done_callback(completed)
        if service:
            return self.services[deviceId].run(operation, perform, cancelled,
                min(requestDeadline or float('inf'), monotonic()+operationTimeout), executor.generation)
        return executor.wait(operation, cancelled)

    def testConnection(self, value, cancelled=lambda: False, *, admissionLock=None, admission=None, deviceId=None):
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
            explicit = deviceId
            existing = explicit if explicit in self.drivers else next((k for k, identity in self.identities.items()
                if identities[key] is not None and identity == identities[key]
                and self.drivers[k].snapshot().get('ownsResources')), None)
            if existing is not None:
                key = existing
                if self.definitions[key].connection != definition.connection:
                    raise DeviceError('E_DEVICE_IDENTITY', 'existing physical device has different connection settings')
            else:
                driver, executor, lifecycle = self.makeBackend(key, definitions[key], classes[key], self.scope)
                self.definitions[key], self.identities[key], self.drivers[key] = definitions[key], identities[key], driver
                self.lifecycles[key] = lifecycle
                self.executors[key] = executor
                from .service_executor import ServiceExecutor
                self.services[key] = ServiceExecutor(executor)
                temporary = True
            self.authorizeMaintenance(owner, key)
            executor, lifecycle = self.executors[key], self.lifecycles[key]
            lifecycle.keepAlive.add(owner)
            def perform(cancel, deadline, _generation):
                wasConnected = lifecycle.connected
                try:
                    return dict(self.drivers[key].testConnection(definition.defaults, lambda: cancel() or cancelled(), deadline),
                                reused=not temporary, deviceId="" if temporary else key)
                finally:
                    if not wasConnected:
                        lifecycle.disconnect()
        try:
            operation = executor.submit(owner, perform, waitTimeout=5,
                operationTimeout=connectionBudget(definition.policies))
            return executor.wait(operation, cancelled)
        finally:
            # The native owner, not RPC abandonment, determines when it is safe
            # to remove temporary identity and release the physical connection.
            with executor.condition:
                executor.condition.wait_for(lambda: executor.active is None or executor.active.owner != owner)
            lifecycle.keepAlive.discard(owner)
            self.retire(owner)
            if temporary:
                driver = self.drivers[key]
                driver.requestStop('test_complete')
                cleanup = executor.submit('__dispose__', lambda _c, deadline, _g: driver.dispose(deadline), cleanup=True)
                executor.wait(cleanup)
                executor.close()
                with self.lock:
                    for mapping in (self.definitions, self.identities, self.drivers, self.lifecycles, self.executors, self.services):
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
                if self.definitions[key].enabled and self.types[self.definitions[key].deviceTypeId].operationScheduling == "serial" and self.types[self.definitions[key].deviceTypeId].connectionLifetime == "persistent" and self.definitions[key].policies.get("connect") == "on_project_run":
                    self._connectEvent(key, owner)
            parent, child = context.Pipe()
            bridge = DeviceBridge(self, owner, parent, cancellation)
            self.workers[owner] = bridge
            bridge.thread.start()
            return child

    def openTcpWorker(self, owner, cancellation, context):
        from emo_master.apps.runtime.communication.ipc import WorkerBridge
        with self.lock:
            if owner in self.tcpWorkers:
                raise DeviceError('E_DEVICE_OWNER', 'duplicate TCP worker')
            parent, child = context.Pipe()
            bridge = WorkerBridge(self, owner, parent, cancellation)
            self.tcpWorkers[owner] = bridge
            bridge.start()
            return child

    def revokeTcpWorker(self, owner):
        bridge = self.tcpWorkers.get(owner)
        if bridge is not None:
            bridge.close()
            self.tcpWorkers.pop(owner, None)

    def revoke(self, owner):
        with self.lock:
            self.permissions.pop(owner, None)
            drivers = list(self.drivers.values())
            for key, executor in self.executors.items():
                executor.cancelOwner(owner)
                self.services[key].cancelOwner(owner)
            for lifecycle in self.lifecycles.values():
                lifecycle.keepAlive.discard(owner)
        for driver in drivers:
            driver.revokeOwner(owner)

    def retire(self, owner):
        self.revoke(owner)
        bridge = self.workers.get(owner)
        if bridge:
            bridge.close()
        tcpBridge = self.tcpWorkers.pop(owner, None)
        if tcpBridge is not None:
            tcpBridge.close()
        self.waitOwner(owner)
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
        with self.lock:
            entries = [(key, self.drivers[key], executor) for key, executor in self.executors.items()]
            for key, driver, _ in entries:
                self.lifecycles[key].paused = True
                self.services[key].cancelOwner()
        for _key, driver, _executor in entries:
            driver.requestStop('project_close')
        for key, driver, executor in entries:
            self.services[key].wait(deadline=monotonic()+5)
            operation = executor.submit('__dispose__', lambda _c, deadline, _g, driver=driver: driver.dispose(deadline),
                operationTimeout=5, cleanup=True)
            executor.wait(operation)
            executor.close()
        with self.lock:
            self.executors.clear()
            self.drivers.clear()
            self.lifecycles.clear()
            self.services.clear()

    def _connectEvent(self, deviceId, owner):
        self._checkReplacement([deviceId])
        lifecycle, executor = self.lifecycles[deviceId], self.executors[deviceId]
        if self.types[self.definitions[deviceId].deviceTypeId].connectionLifetime != 'persistent':
            return None
        with executor.condition:
            if lifecycle.paused or lifecycle.controlPending or executor.quarantined:
                return None
            pending = getattr(lifecycle, 'connectOperation', None)
            configurationRevision = self.configuration.current['executionRevision'] if self.configuration and self.configuration.current else None
            if (pending is not None and not pending.future.done()
                    and getattr(lifecycle, 'connectRevision', None) == configurationRevision):
                return pending
            revision = lifecycle.controlRevision
            def connect(cancel, deadline, generation):
                currentRevision = self.configuration.current['executionRevision'] if self.configuration and self.configuration.current else None
                if (self.lifecycles.get(deviceId) is not lifecycle or revision != lifecycle.controlRevision or lifecycle.paused
                        or currentRevision != configurationRevision):
                    return 'SUPERSEDED'
                try:
                    lifecycle.connect(cancel, deadline)
                except Exception as error:
                    lifecycle.driver.runtimeContext.events('device.connect.failed', str(error), code=getattr(error, 'code', 'E_DEVICE_CONNECT'))
                    raise
                lifecycle.driver.runtimeContext.events('device.connect.completed', '设备连接已就绪')
                return 'LISTENING' if lifecycle.driver.snapshot().get('state') == 'LISTENING' else 'CONNECTED'
            operation = executor.submit(owner, connect,
                operationTimeout=connectionBudget(self.definitions[deviceId].policies))
            lifecycle.connectOperation = operation
            lifecycle.connectRevision = configurationRevision
            return operation

    def activate(self):
        with self.lock:
            return {key: self._connectEvent(key, '__project_open__') for key, definition in self.definitions.items()
                if definition.enabled and definition.policies.get('connect') == 'on_project_open'
                and not self.lifecycles[key].connected}

    def preflight(self, document, workflowId):
        from emo_master.core.project.devices import workflowDeviceIds
        from emo_master.core.workflow.dispatch_contracts import groupSlots
        required = set(workflowDeviceIds(document, workflowId))
        for target, _capacity in groupSlots(document, workflowId).values():
            required.update(workflowDeviceIds(document, target))
        pending = {}
        with self.lock:
            for key, definition in self.definitions.items():
                declaration = self.types[definition.deviceTypeId]
                if not definition.enabled or declaration.operationScheduling != 'managed_service':
                    continue
                if definition.policies.get('connect') == 'on_project_run':
                    pending[key] = self._connectEvent(key, '__project_run__')
                elif key in required:
                    pending[key] = getattr(self.lifecycles[key], 'connectOperation', None)
        for key in required:
            if key not in self.definitions:
                raise DeviceError('E_DEVICE_REFERENCE', key)
            definition = self.definitions[key]
            if not definition.enabled:
                raise DeviceError('E_DEVICE_DISABLED', key + ': device is disabled')
            if self.types[definition.deviceTypeId].operationScheduling == 'managed_service':
                operation = pending.get(key)
                if operation is not None and not self.lifecycles[key].connected:
                    self.executors[key].wait(operation)
                self.lifecycles[key].admit()
                if not self.lifecycles[key].connected:
                    raise DeviceError('E_TCP_NOT_RUNNING', key + ': 请从设备页启动服务')

    def beginControl(self, deviceId, expectedGeneration, action, *, expectedControlRevision=None):
        with self.lock:
            self._checkReplacement([deviceId])
            executor = self.executors.get(deviceId)
            if executor is None or executor.generation != expectedGeneration:
                raise DeviceError('E_DEVICE_GENERATION', 'device generation changed; refresh state')
            lifecycle, driver = self.lifecycles[deviceId], self.drivers[deviceId]
            declaration = self.types[self.definitions[deviceId].deviceTypeId]
            if action not in declaration.controlCapabilities or action in {'test_connection', 'recover'}:
                raise DeviceError('E_DEVICE_ACTION', 'control not declared: ' + action)
            if expectedControlRevision is not None and lifecycle.controlRevision != expectedControlRevision:
                raise DeviceError('E_DEVICE_CONTROL_STALE', 'device control changed; refresh state')
            if lifecycle.controlPending:
                raise DeviceError('E_DEVICE_BUSY', 'another device control is pending')
            if not self.definitions[deviceId].enabled and action in {'connect', 'resume'}:
                raise DeviceError('E_DEVICE_DISABLED', deviceId)
            if action == 'connect' and self.busy(deviceId):
                raise DeviceError('E_DEVICE_BUSY', 'device is in use')
            oldPaused = lifecycle.paused
            lifecycle.controlRevision = uuid4().hex
            lifecycle.controlPending = True
            lifecycle.paused = True
            if action in {'disconnect', 'maintenance'}:
                for operation in list(executor.queue):
                    executor.cancel(operation)
                if executor.active:
                    executor.active.cancellation.set()
                self.services[deviceId].cancelOwner()
            budget = connectionBudget(self.definitions[deviceId].policies)
            service = self.services[deviceId]
        def perform(cancel, deadline, _generation):
            if action == 'connect':
                lifecycle.connect(cancel, deadline)
                if cancel() or monotonic() >= deadline:
                    lifecycle.disconnect(deadline)
                    raise DeviceError('E_DEVICE_CANCELLED' if cancel() else 'E_DEVICE_OPERATION_TIMEOUT',
                        'connection control abandoned; admission remains paused')
                lifecycle.paused = False
            elif action == 'resume':
                lifecycle.paused = False
            else:
                service.wait(deadline=deadline)
                lifecycle.disconnect(deadline)
            driver.runtimeContext.events('device.control', action)
        try:
            if action in {'disconnect', 'maintenance'}:
                driver.requestStop(action)
            operation = executor.submit('__device_control__', perform, waitTimeout=30,
                operationTimeout=budget, cleanup=action != 'connect')
        except BaseException:
            with self.lock:
                lifecycle.controlPending = False
                lifecycle.paused = oldPaused
            raise
        def completed(future):
            with executor.condition:
                lifecycle.controlPending = False
                error = future.exception()
                lifecycle.lastError = str(error) if error else ''
        operation.future.add_done_callback(completed)
        with self.lock:
            if action == 'connect':
                lifecycle.connectOperation = operation
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
        from .drivers import validateDriver
        with self.lock:
            self._checkReplacement([deviceId])
            definition = self.definitions[deviceId]
            if 'recover' not in self.types[definition.deviceTypeId].controlCapabilities:
                raise DeviceError('E_DEVICE_ACTION', 'recovery not declared')
            previous = self.executors[deviceId]
            if previous.generation != expectedGeneration:
                raise DeviceError('E_DEVICE_GENERATION', 'device generation changed')
            if self.busy(deviceId) or any(deviceId in ids for ids in self.permissions.values()):
                raise DeviceError('E_DEVICE_BUSY', 'retire device users before recovery')
            lifecycle = self.lifecycles[deviceId]
            lifecycle.controlPending = True
            oldDriver = self.drivers[deviceId]
        try:
            oldDriver.requestStop('recover')
            operation = previous.submit('__recover__', lambda _c, deadline, _g: oldDriver.dispose(deadline), cleanup=True)
            previous.wait(operation)
            previous.close()
            driver, executor, replacement = self.makeBackend(deviceId, definition,
                validateDriver(self.types[definition.deviceTypeId]), self.scope)
            replacement.paused = lifecycle.paused
            with self.lock:
                self.drivers[deviceId], self.executors[deviceId], self.lifecycles[deviceId] = driver, executor, replacement
                from .service_executor import ServiceExecutor
                self.services[deviceId] = ServiceExecutor(executor)
        except BaseException:
            lifecycle.controlPending = False
            raise

    def close(self):
        if self.replacement is not None:
            self.replacement.close()
        if self.workers or self.maintenanceOwners:
            raise DeviceError("E_DEVICE_RELEASE_PENDING", "device IPC workers still exist")
        self._closeDevices()
        self.frames.close()
