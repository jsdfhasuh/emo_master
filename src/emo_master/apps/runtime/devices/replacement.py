"""Prepared, hardware-free adapter replacement fenced to affected devices."""
from .executor import DeviceError, DeviceExecutor
from .lifecycle import DeviceLifecycle


ADAPTER_FIELDS = ("deviceTypeId", "driverVersion", "connection", "policies", "enabled")


def requiresReplacement(before, after, declaration):
    if any(getattr(before, field) != getattr(after, field) for field in ADAPTER_FIELDS):
        return True
    from emo_master.core.project.configuration import projectChanges
    return any(".".join(change["parts"]) not in declaration.nextCyclePaths
               for change in projectChanges(before.defaults, after.defaults))


class PreparedDevices:
    def __init__(self, manager, definitions, identities, classes, *, force=False):
        self.manager = manager
        self.definitions, self.identities = definitions, identities
        self.drivers, self.executors, self.lifecycles = {}, {}, {}
        self.installed = False
        self.closed = False
        self.changed = set(manager.definitions) ^ set(definitions)
        self.changed.update(key for key in definitions.keys() & manager.definitions.keys()
                            if requiresReplacement(manager.definitions[key], definitions[key],
                                                   manager.types[definitions[key].deviceTypeId]))
        if force:
            self.changed = set(manager.definitions) | set(definitions)
        if manager.replacement is not None:
            raise DeviceError("E_DEVICE_BUSY", "another device replacement is pending")
        for key in self.changed:
            executor = manager.executors.get(key)
            if executor is None:
                continue
            with executor.condition:
                lifecycle = manager.lifecycles[key]
                if (executor.active or executor.queue or lifecycle.connected or lifecycle.controlPending
                        or any(key in ids for ids in manager.permissions.values())):
                    raise DeviceError("E_CONFIG_REQUIRES_STOP",
                                      "retire affected device users and disconnect before replacement: " + key)
        manager.replacement = self
        try:
            for key in self.changed & definitions.keys():
                driver = classes[key](definitions[key])
                if hasattr(driver, 'resourceLeasePath'):
                    driver.resourceLeasePath = manager.resourceLeasePath
                self.drivers[key] = driver
                lifecycle = DeviceLifecycle(definitions[key], driver)
                # Explicit maintenance remains paused across a configuration write.
                previous = None if force else manager.lifecycles.get(key)
                lifecycle.paused = bool(previous and previous.paused)
                self.lifecycles[key] = lifecycle
                self.executors[key] = DeviceExecutor(identities[key], idleAction=lifecycle.idleAction)
        except BaseException:
            self.close()
            raise

    def install(self):
        manager = self.manager
        with manager.lock:
            if self.closed or manager.replacement is not self or self.installed:
                raise DeviceError("E_DEVICE_GENERATION", "prepared device replacement is no longer current")
            retiredDrivers, retiredExecutors = {}, {}
            for key in self.changed:
                if key in manager.drivers:
                    retiredDrivers[key] = manager.drivers.pop(key)
                    retiredExecutors[key] = manager.executors.pop(key)
                    manager.lifecycles.pop(key)
            manager.drivers.update(self.drivers)
            manager.executors.update(self.executors)
            manager.lifecycles.update(self.lifecycles)
            manager.definitions, manager.identities = self.definitions, self.identities
            for key, definition in self.definitions.items():
                manager.lifecycles[key].definition = definition
            self.drivers, self.executors = retiredDrivers, retiredExecutors
            self.installed = True

    def close(self):
        with self.manager.lock:
            if self.closed:
                return
            errors = []
            for key, executor in list(self.executors.items()):
                try:
                    executor.close()
                    del self.executors[key]
                except Exception as error:
                    errors.append(str(error))
            for key, driver in list(self.drivers.items()):
                if key in self.executors:
                    continue
                try:
                    driver.close()
                    del self.drivers[key]
                except Exception as error:
                    errors.append(str(error))
            if errors:
                raise DeviceError("E_DEVICE_RELEASE_PENDING", "; ".join(errors), outcome="UNKNOWN")
            if self.manager.replacement is self:
                self.manager.replacement = None
            self.closed = True
