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
        _document, prepared = runtime._validateProjectConfiguration(payload, checkAdmission=False)
        if runtime._supportsCycleChange(payload):
            return
        if runtime.operatorDebugManager.ownsResources() or runtime.livePreviewManager.hasConfigurationSession():
            raise ConfigurationError("E_CONFIG_REQUIRES_STOP", "close diagnostic sessions before automatic transition")
        self.communicationChanged = prepared.communication != runtime.loadedDocument.communication
        if self.communicationChanged:
            state = runtime.communicationManager.snapshot()
            if state.get("savePending"):
                raise ConfigurationError("E_CONFIG_WRITER_BUSY", "communication save is pending")
            self.communicationListening = state["state"] == "LISTENING"
            self.communicationControlRevision = runtime.communicationManager.controlRevision
        manager = runtime.deviceManager
        definitions, _identities, _classes = manager._validatedDefinitions(prepared)
        changed = set(manager.definitions) ^ set(definitions)
        changed.update(key for key in definitions.keys() & manager.definitions.keys()
            if requiresReplacement(manager.definitions[key], definitions[key], manager.types[definitions[key].deviceTypeId]))
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
            for key in changed & manager.lifecycles.keys():
                lifecycle = manager.lifecycles[key]
                if lifecycle.controlPending:
                    raise ConfigurationError("E_DEVICE_BUSY", "affected device control is pending")
                self.previous[key] = (lifecycle.connected, lifecycle.paused)
        try:
            deadline = time.monotonic() + 30
            if policy == "drainAffected":
                with self.configuration.cycleLock:
                    self.configuration.drainingOwners.update(self.owners)
                while True:
                    self._check(deadline)
                    with self.configuration.cycleLock:
                        busy = any(owner in self.configuration.cycles or owner in self.configuration.childCycles for owner in self.owners)
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
            raise

    def _check(self, deadline):
        if self.cancelled.is_set() or self.runtime._closing:
            raise ConfigurationError("E_CONFIG_CANCELLED", "transition cancelled; stopped Jobs and connection effects are not rolled back")
        if time.monotonic() >= deadline:
            raise ConfigurationError("E_CONFIG_DRAIN_TIMEOUT", "affected execution did not retire within the transition budget")

    def finish(self, status):
        with self.configuration.cycleLock:
            self.configuration.drainingOwners.difference_update(self.owners)
        status["affectedJobs"] = sorted(self.owners)
        if status["state"] != "COMMITTED":
            status["transitionMessage"] = "stopped Jobs are not restarted; disconnected devices remain disconnected"
            return
        status["connections"] = {}
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
