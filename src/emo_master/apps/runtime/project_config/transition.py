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
        self.communicationChanged = self.projectChanged or runtime.loadedDocument is None or prepared.communication != runtime.loadedDocument.communication
        if self.communicationChanged:
            state = runtime.communicationManager.snapshot()
            if state.get("savePending"):
                raise ConfigurationError("E_CONFIG_WRITER_BUSY", "communication save is pending")
            self.communicationListening = not self.projectChanged and state["state"] == "LISTENING"
            self.communicationControlRevision = runtime.communicationManager.controlRevision
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
            if self.communicationChanged:
                self._check(deadline)
                runtime.communicationManager.stop()
            for key in self.previous:
                self._check(deadline)
                lifecycle, executor = manager.lifecycles[key], manager.executors[key]
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
        status["connections"] = {}
        if getattr(self, "projectChanged", False):
            status["transitionMessage"] = "project replaced; old connection intents and Jobs are not restarted"
            self.configuration.store.recordTransition(status)
            return
        if self.communicationChanged and self.communicationListening and self.runtime.loadedDocument.communication.enabled:
            try:
                if self.runtime.communicationManager.controlRevision != self.communicationControlRevision:
                    status["connections"]["communication"] = "SUPERSEDED"
                else:
                    self.runtime.communicationManager.start()
                    status["connections"]["communication"] = "LISTENING"
            except Exception as error:
                status["connections"]["communication"] = "FAILED: " + str(error)
        manager = self.runtime.deviceManager
        for key, (connected, paused) in self.previous.items():
            if not connected or paused or key not in manager.lifecycles or not manager.definitions[key].enabled:
                continue
            lifecycle, executor = manager.lifecycles[key], manager.executors[key]
            expectedControl = lifecycle.controlRevision
            revision = status["executionRevision"]
            status["connections"][key] = "PENDING"
            def reconnect(cancel, deadline, _generation, key=key, lifecycle=lifecycle,
                          expectedControl=expectedControl, revision=revision):
                # Check on the FIFO thread, not merely when enqueuing R11. R12
                # supersedes an unexecuted connection intent, never an old capture.
                current = self.configuration.current
                if (current is None or current["executionRevision"] != revision
                        or manager.lifecycles.get(key) is not lifecycle
                        or lifecycle.controlRevision != expectedControl or lifecycle.paused):
                    return "SUPERSEDED"
                lifecycle.connect(cancel, deadline)
                return "CONNECTED"
            operation = executor.submit("__configuration_connect__", reconnect, waitTimeout=30, operationTimeout=5)
            def completed(future, key=key):
                try:
                    result = future.result()
                except Exception as error:
                    result = "FAILED: " + str(error)
                with self.configuration.lock:
                    status["connections"][key] = result
                    try:
                        self.configuration.store.recordTransition(status)
                    except Exception as error:
                        status["connectionError"] = "connection observation persistence failed: " + str(error)
            operation.future.add_done_callback(completed)
        self.configuration.store.recordTransition(status)
