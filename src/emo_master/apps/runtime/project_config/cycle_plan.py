"""Install cycle-safe parameters only for the lifetime of an explicit plan lease."""
from contextlib import contextmanager
from dataclasses import replace

from emo_master.core.project.configuration import applyCycleParameters, applyDeviceCycleParameters
from emo_master.core.project.models import ProjectDocument


class CyclePlanSession:
    def __init__(self, runner, devices, document, compiler, registry, pluginRoots, revision, cancelled):
        self.runner, self.devices, self.document = runner, devices, document
        self.compiler, self.registry, self.pluginRoots = compiler, registry, pluginRoots
        self.revision, self.cancelled = revision, cancelled

    @contextmanager
    def scope(self, context, *, parentCycleId=None):
        cycle = self.devices.call("beginCycle", knownRevision=self.revision, parentCycleId=parentCycleId)
        previous = self.document, self.runner.compiledProject, self.revision
        try:
            self.install(cycle)
            yield replace(context, cycleId=cycle["cycleId"], planRevision=self.revision,
                          documentRevision=cycle.get("documentRevision", 0))
        finally:
            if parentCycleId is not None:
                # Code after the Loop still belongs to the outer cycle's plan.
                self.document, self.runner.compiledProject, self.revision = previous
            if cycle["ownsCycle"] and not self.cancelled():
                self.devices.call("endCycle", cycleId=cycle["cycleId"])

    def iteration(self, context):
        return self.scope(context, parentCycleId=context.cycleId)

    def install(self, cycle):
        self.runner.operatorExecutor.deviceScope = cycle.get('deviceScope', {})
        if "payload" in cycle:
            # Preserve materialized paths and retained operator instances.
            updated = self.document.model_dump(mode="json")
            applyDeviceCycleParameters(updated, cycle["payload"], self.compiler.deviceTypes)
            applyCycleParameters(updated, cycle["payload"], self.registry)
            self.document = ProjectDocument.model_validate(updated)
            self.runner.compiledProject = self.compiler.compile(self.document, pluginRootPaths=self.pluginRoots)
        self.revision = cycle["planRevision"]
