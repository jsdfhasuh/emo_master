"""Prepared, hardware-free adapter replacement fenced to affected devices."""
from .executor import DeviceError


ADAPTER_FIELDS = ("deviceTypeId", "driverVersion", "connection", "policies", "enabled")


def requiresReplacement(before, after, declaration):
    def transport(definition):
        payload = definition.model_dump()
        if definition.deviceTypeId == 'device.tcp.server':
            payload['connection']['channels'] = [{k: v for k, v in channel.items() if k != 'name'}
                                                 for channel in payload['connection']['channels']]
        return payload
    old, new = transport(before), transport(after)
    if any(old[field] != new[field] for field in ADAPTER_FIELDS):
        return True
    from emo_master.core.project.configuration import projectChanges
    return any(".".join(change["parts"]) not in declaration.nextCyclePaths
               for change in projectChanges(before.defaults, after.defaults))


class PreparedDevices:
    def __init__(self, manager, definitions, identities, classes, *, scope, force=False):
        self.manager = manager
        self.definitions, self.identities = definitions, identities
        self.drivers, self.executors, self.lifecycles = {}, {}, {}
        self.scope = scope
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
                if (manager.busy(key) or lifecycle.connected or lifecycle.controlPending
                        or any(key in ids for ids in manager.permissions.values())):
                    raise DeviceError("E_CONFIG_REQUIRES_STOP",
                                      "retire affected device users and disconnect before replacement: " + key)
        manager.replacement = self
        try:
            for key in self.changed & definitions.keys():
                driver, executor, lifecycle = manager.makeBackend(key, definitions[key], classes[key], self.scope)
                self.drivers[key] = driver
                # Explicit maintenance remains paused across a configuration write.
                previous = None if force else manager.lifecycles.get(key)
                lifecycle.paused = bool(previous and previous.paused)
                self.lifecycles[key] = lifecycle
                self.executors[key] = executor
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
            manager.scope = self.scope
            from .service_executor import ServiceExecutor
            for key in self.changed:
                manager.services.pop(key, None)
                if key in self.definitions:
                    manager.services[key] = ServiceExecutor()
            manager.definitions, manager.identities = self.definitions, self.identities
            for key, definition in self.definitions.items():
                manager.lifecycles[key].definition = definition
                manager.drivers[key].definition = definition
            self.drivers, self.executors = retiredDrivers, retiredExecutors
            self.installed = True

    def close(self):
        with self.manager.lock:
            if self.closed:
                return
            entries = list(self.drivers.items())
        errors = []
        from time import monotonic
        for key, driver in entries:
            try:
                executor = self.executors.get(key)
                driver.requestStop('replacement_dispose')
                if executor is not None:
                    operation = executor.submit('__dispose__', lambda _c, deadline, _g, driver=driver: driver.dispose(deadline), cleanup=True)
                    executor.wait(operation)
                    executor.close()
                    self.executors.pop(key, None)
                else:
                    driver.dispose(monotonic()+5)
                self.drivers.pop(key, None)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise DeviceError('E_DEVICE_RELEASE_PENDING', '; '.join(errors), outcome='UNKNOWN')
        with self.manager.lock:
            if self.manager.replacement is self:
                self.manager.replacement = None
            self.closed = True
