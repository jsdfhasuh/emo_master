"""Install cycle-safe parameters only for the lifetime of an explicit plan lease."""
from contextlib import contextmanager
from dataclasses import replace

from emo_master.core.contracts.device_driver import DeviceError
from emo_master.core.project.configuration import applyCycleParameters, applyDeviceCycleParameters
from emo_master.core.project.models import ProjectDocument
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested


class CyclePlanSession:
    def __init__(self, runner, devices, document, compiler, registry, pluginRoots, revision, cancelled):
        self.runner, self.devices, self.document = runner, devices, document
        self.compiler, self.registry, self.pluginRoots = compiler, registry, pluginRoots
        self.revision, self.cancelled = revision, cancelled

    @contextmanager
    def scope(self, context, *, parentCycleId=None):
        if self.cancelled():
            raise CancellationRequested("execution cancelled")
        cycle = self.devices.framework("beginCycle", knownRevision=self.revision, parentCycleId=parentCycleId)
        executor = self.runner.operatorExecutor
        previous = (self.document, self.runner.compiledProject, self.revision,
                    getattr(executor, 'deviceScope', {}), getattr(executor, 'deviceIdentities', {}))
        primaryError = None
        try:
            self.install(cycle)
            yield replace(context, cycleId=cycle["cycleId"], planRevision=self.revision,
                          documentRevision=cycle.get("documentRevision", 0))
        except BaseException as error:
            primaryError = error
            raise
        finally:
            if parentCycleId is not None:
                # Code after the Loop still belongs to the outer cycle's plan.
                (self.document, self.runner.compiledProject, self.revision,
                 executor.deviceScope, executor.deviceIdentities) = previous
            if cycle["ownsCycle"] and not self.cancelled():
                try:
                    self.devices.framework("endCycle", cycleId=cycle["cycleId"])
                except CancellationRequested:
                    # Preserve the existing cancellation receipt unchanged.
                    if primaryError is None:
                        raise
                except DeviceError as cleanupError:
                    # Keep the original device receipt, including UNKNOWN and
                    # its request identity, when an already closed proxy rejects
                    # cleanup. Owner retirement still releases the cycle lease.
                    if primaryError is None:
                        raise
                    diagnostics = getattr(primaryError, "diagnostics", None)
                    if isinstance(diagnostics, dict):
                        cleanup = diagnostics.setdefault("cycleCleanup", [])
                        if isinstance(cleanup, list):
                            cleanup.append({"cycleId": cycle["cycleId"], "code": cleanupError.code,
                                "message": str(cleanupError), "outcome": getattr(cleanupError, "outcome", None),
                                "diagnostics": getattr(cleanupError, "diagnostics", {})})

    def iteration(self, context):
        return self.scope(context, parentCycleId=context.cycleId)

    def install(self, cycle):
        self.runner.operatorExecutor.deviceScope = cycle.get('deviceScope', {})
        self.runner.operatorExecutor.deviceIdentities = cycle.get('deviceIdentities', {})
        if "payload" in cycle:
            # Preserve materialized paths and retained operator instances.
            updated = self.document.model_dump(mode="json")
            applyDeviceCycleParameters(updated, cycle["payload"], self.compiler.deviceTypes)
            applyCycleParameters(updated, cycle["payload"], self.registry)
            self.document = ProjectDocument.model_validate(updated)
            self.runner.compiledProject = self.compiler.compile(self.document, pluginRootPaths=self.pluginRoots)
        self.revision = cycle["planRevision"]
