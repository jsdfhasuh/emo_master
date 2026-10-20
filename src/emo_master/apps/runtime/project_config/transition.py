"""Explicit scoped software update transition; never called by validation/save."""
import time

from emo_master.apps.runtime.devices.replacement import requiresReplacement
from .store import ConfigurationError


class DeviceTransition:
    def __init__(self, runtime, payload, cancelled, policy):
        if policy not in {"stopAffected", "drainAffected"}:
            raise ConfigurationError("E_CONFIG_POLICY", "unknown replacement policy")
        self.runtime, self.cancelled = runtime, cancelled
        self.owners, self.previous = set(), {}
        self.communicationChanged = False
        self.communicationListening = False
        self.configuration = runtime.projectConfiguration
        self.projectChanged = payload["project"]["projectId"] != self.configuration.current["payload"]["project"]["projectId"]
        _document, prepared = runtime._validateProjectConfiguration(payload, checkAdmission=False)
        if runtime._supportsCycleChange(payload):
            return
        if runtime.operatorDebugManager.ownsResources() or runtime.livePreviewManager.hasConfigurationSession():
            raise ConfigurationError("E_CONFIG_REQUIRES_STOP", "close diagnostic sessions before automatic transition")
        manager = runtime.deviceManager
        definitions, _identities, _classes = manager._validatedDefinitions(prepared)
        changed = set(manager.definitions) ^ set(definitions)
        changed.update(key for key in definitions.keys() & manager.definitions.keys()
            if requiresReplacement(manager.definitions[key], definitions[key], manager.types[definitions[key].deviceTypeId]))
        if self.projectChanged:
            changed = set(manager.definitions) | set(definitions)
        with manager.lock:
            self.owners = {owner for owner, ids in manager.permissions.items() if changed.intersection(ids)}
            from .impact import affectedJobs
            self.owners.update(affectedJobs(runtime, payload))
            self.owners = {owner for owner in self.owners if runtime.jobSupervisor.ownsJobResources(owner)
                           or owner in manager.permissions}
            if self.owners & manager.maintenanceOwners:
                raise ConfigurationError("E_CONFIG_REQUIRES_STOP", "close affected diagnostic sessions before automatic transition")
            if any(runtime.jobRepository.get(owner) is None for owner in self.owners):
                raise ConfigurationError("E_CONFIG_REQUIRES_STOP", "affected non-Job owner must retire explicitly")
            for owner in tuple(self.owners):
                group = manager.dispatchGroups.get(owner)
                if group is not None:
                    self.owners.update(group.members)
            groups = {group for owner in self.owners if (group := manager.dispatchGroups.get(owner)) is not None}
            for key in changed & manager.lifecycles.keys():
                lifecycle = manager.lifecycles[key]
                if lifecycle.controlPending:
                    raise ConfigurationError("E_DEVICE_BUSY", "affected device control is pending")
                self.previous[key] = (lifecycle.connected, lifecycle.paused)
        try:
            deadline = time.monotonic() + 30
            if policy == "drainAffected":
                # Consumers must finish inherited deliveries, including their
                # own explicit iteration scopes. Only close source admission.
                consumers = {slot.jobId for group in groups for slot in group.slots.values()}
                with self.configuration.cycleLock:
                    self.configuration.drainingOwners.update(self.owners - consumers)
                while True:
                    self._check(deadline)
                    with self.configuration.cycleLock:
                        busy = any(owner in self.configuration.childCycles
                                   or owner in self.configuration.cycles and owner not in self.configuration.parkedOwners
                                   for owner in self.owners)
                    # Queued deliveries retain their cycle under delivery IDs,
                    # not Job IDs. Do not stop consumers while those are pending.
                    for group in groups:
                        with group.lock:
                            busy = busy or any(slot.queue or slot.active for slot in group.slots.values())
                    if not busy:
                        break
                    time.sleep(.02)
            for owner in self.owners:
                self._check(deadline)
                record = runtime.jobRepository.get(owner)
                if record is not None and not record.isTerminal:
                    outcome = runtime.jobSupervisor.stopJobOutcome(owner)
                    if not outcome.ok:
                        raise ConfigurationError("E_CONFIG_STOP_FAILED", outcome.message)
            while any(runtime.jobSupervisor.ownsJobResources(owner) for owner in self.owners):
                self._check(deadline)
                runtime.jobSupervisor.finishRetirements()
                time.sleep(.02)
            for key in self.previous:
                self._check(deadline)
                lifecycle, executor = manager.lifecycles[key], manager.executors[key]
                manager.drivers[key].requestStop("configuration_replace")
                manager.services[key].cancelOwner()
                manager.services[key].wait(deadline=deadline)
                operation = executor.submit("__configuration_drain__",
                    lambda _cancel, _deadline, _generation, lifecycle=lifecycle: lifecycle.disconnect(),
                    waitTimeout=max(.01, deadline - time.monotonic()), operationTimeout=5)
                executor.wait(operation, cancelled.is_set)
        except BaseException:
            with self.configuration.cycleLock:
                self.configuration.drainingOwners.difference_update(self.owners)
                self.configuration.parkedOwners.difference_update(self.owners)
            raise

    def _check(self, deadline):
        if self.cancelled.is_set() or self.runtime._closing:
            raise ConfigurationError("E_CONFIG_CANCELLED", "transition cancelled; stopped Jobs and connection effects are not rolled back")
        if time.monotonic() >= deadline:
            raise ConfigurationError("E_CONFIG_DRAIN_TIMEOUT", "affected execution did not retire within the transition budget")

    def finish(self, status):
        with self.configuration.cycleLock:
            self.configuration.drainingOwners.difference_update(self.owners)
            self.configuration.parkedOwners.difference_update(self.owners)
        status["affectedJobs"] = sorted(self.owners)
        if status["state"] != "COMMITTED":
            status["transitionMessage"] = "stopped Jobs are not restarted; disconnected devices remain disconnected"
            return
        status.setdefault('connections', {})
        if getattr(self, 'projectChanged', False):
            status['transitionMessage'] = 'project replaced; old Jobs are not restarted'
        manager = self.runtime.deviceManager
        operations = dict(manager.activate())
        if not self.projectChanged:
            with manager.lock:
                for key, (connected, paused) in self.previous.items():
                    if connected and not paused and key in manager.lifecycles and manager.definitions[key].enabled:
                        operations[key] = manager._connectEvent(key, '__configuration_connect__')
        observeConnections(self.configuration, status, operations)


def observeConnections(configuration, status, operations):
    """Attach actual future outcomes to the original durable committed receipt."""
    connections = status.setdefault('connections', {})
    for key, operation in operations.items():
        connections[key] = 'PENDING' if operation is not None else 'SUPERSEDED'
    configuration.store.recordTransition(status)
    for key, operation in operations.items():
        if operation is None:
            continue
        def completed(future, key=key):
            try:
                result = future.result() or 'CONNECTED'
            except Exception as error:
                result = 'FAILED: ' + str(error)
            with configuration.lock:
                connections[key] = result
                try:
                    configuration.store.recordTransition(status)
                except Exception as error:
                    status['connectionError'] = 'connection observation persistence failed: ' + str(error)
        operation.future.add_done_callback(completed)
