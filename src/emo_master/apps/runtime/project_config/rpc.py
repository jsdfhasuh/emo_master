"""Online authority; hardware transitions require an explicit replacement policy."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _pb
from emo_master.core.project.configuration import executionDigest, projectChanges
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from .coordinator import ConfigurationCoordinator
from .store import ConfigurationStore, ConfigurationError

pb: Any = _pb


@dataclass
class PreparedConfiguration:
    document: ProjectDocument
    devices: Any
    resources: list = field(default_factory=list)
    resourceRoot: Any = None
    installed: bool = False
    communicationToken: str = ""


class ProjectConfigurationRpcMixin:
    def _initializeOnlineConfiguration(self):
        runtime: Any = self
        runtime._ownedConfigurationResources = set()
        from .resource_journal import ResourceJournal
        runtime.configurationResourceJournal = ResourceJournal(runtime.sqliteStore,
            runtime.workspaceRoot.parent / "configuration-resources", runtime.runtimeInstanceId, consumerLeases=True)
        runtime.deviceManager.resourceLeasePath = runtime.configurationResourceJournal.leasePath
        runtime.operatorDebugManager.resourceLeasePath = runtime.configurationResourceJournal.leasePath
        from emo_master.apps.runtime.devices.network import DeviceNetwork
        runtime.deviceNetwork = DeviceNetwork(runtime)
        if runtime._runtimeDataLockIsPrimary:
            runtime.configurationResourceJournal.recover()
        from .transfer import ResourceTransfers
        runtime.projectResourceTransfers = ResourceTransfers(runtime.sqliteStore, runtime.workspaceRoot.parent / "resource-uploads",
                                                             runtime.runtimeInstanceId)
        if runtime._runtimeDataLockIsPrimary:
            runtime.projectResourceTransfers.recover()
        runtime.projectConfiguration = ConfigurationCoordinator(ConfigurationStore(runtime.sqliteStore,
            commitState=self._commitProjectState),
            runtime.runtimeInstanceId, lock=runtime._projectStateLock,
            prepare=self._prepareProjectConfiguration, publish=self._publishProjectConfiguration,
            beforeCommit=self._checkConfigurationAdmission, discard=self._discardProjectConfiguration,
            transition=self._transitionProjectConfiguration)
        runtime.onlineRecoveryError = ""
        runtime.deviceManager.configuration = runtime.projectConfiguration
        current = runtime.projectConfiguration.current
        if current:
            try:
                document = ProjectDocument.model_validate(current["payload"])
                from emo_master.core.project.runtime_directory import prepareRuntimeProject
                document = prepareRuntimeProject(document, Path(current["sourcePath"]).parent,
                    runtime.pluginScanResult.activeOperators, deviceTypes=runtime.pluginScanResult.deviceTypes,
                    resourcePaths=runtime.projectResourceTransfers.resolve(document),
                    protectedPaths=(*runtime.sqliteProtectedPaths(), runtime.operationalLogWriter.directory))
                from threading import Event
                from .resources import prepareResources
                document, _resources = prepareResources(document, runtime.pluginScanResult.activeOperators,
                    runtime.pluginScanResult.deviceTypes, runtime.workspaceRoot.parent / "configuration-resources", Event(),
                    journal=runtime.configurationResourceJournal)
                runtime._ownedConfigurationResources.update(_resources)
                WorkflowCompiler(runtime.pluginScanResult.activeOperators,
                    deviceTypes=runtime.pluginScanResult.deviceTypes).compile(document, pluginRootPaths=runtime.pluginRootPaths)
                runtime.deviceManager.load(document)
                runtime.communicationManager.load(document, current["sourcePath"])
                runtime.loadedProjectFile = current["sourcePath"]
                runtime.loadedProjectPath = str(Path(current["sourcePath"]).parent)
                self._publishProjectConfiguration(document, current)
                runtime.deviceManager.activate()
                runtime._loadedProjectPreviewKey = runtime.previewAssetStore.projectKey(
                    runtime.loadedProjectFile, document.project.projectId)
            except Exception as error:
                runtime.onlineRecoveryError = str(error)

    def _transitionProjectConfiguration(self, payload, cancelled, policy):
        from .transition import DeviceTransition
        return DeviceTransition(self, payload, cancelled, policy)

    def _validateProjectConfiguration(self, payload, *, checkAdmission=True):
        runtime: Any = self
        document = ProjectDocument.model_validate(payload)
        with runtime._projectStateLock:
            current = runtime.loadedDocument
            if current is None or runtime.onlineRecoveryError:
                raise ConfigurationError("E_CONFIG_RECOVERY", "online configuration is not execution-ready")
            if document.project.projectId != current.project.projectId or document.schemaVersion != "2.6":
                raise ConfigurationError("E_CONFIG_PROJECT", "online writes must retain project identity and schema 2.6")
            from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
            with runtime.sqliteStore.connection() as connection:
                ProjectGlobalVariables(runtime.sqliteStore, document.project.projectId,
                    document.globalVariables).validateDefinitions(connection)
            if checkAdmission:
                self._checkConfigurationAdmission(payload)
        from emo_master.core.project.runtime_directory import prepareRuntimeProject
        prepared = prepareRuntimeProject(document, Path(runtime.loadedProjectFile).parent,
            runtime.pluginScanResult.activeOperators, deviceTypes=runtime.pluginScanResult.deviceTypes,
            resourcePaths=runtime.projectResourceTransfers.resolve(document),
            protectedPaths=(*runtime.sqliteProtectedPaths(), runtime.operationalLogWriter.directory))
        from .resources import retainDeviceInputs
        prepared = retainDeviceInputs(prepared, current, runtime.pluginScanResult.deviceTypes,
                                      runtime.workspaceRoot.parent / "configuration-resources")
        WorkflowCompiler(runtime.pluginScanResult.activeOperators, deviceTypes=runtime.pluginScanResult.deviceTypes).compile(
            prepared, pluginRootPaths=runtime.pluginRootPaths)
        if document.presentation != current.presentation or document.resources != current.resources:
            from emo_master.core.presentation.validation import validateBindings
            issues = validateBindings(document, {key: value.manifest for key, value in runtime.pluginScanResult.activeOperators.items()},
                                      publish=document.presentation != current.presentation)
            if issues:
                raise ConfigurationError("E_CONFIG_PAGE_BINDING", "; ".join(issue.path + ": " + issue.message for issue in issues))
        # Validate unreferenced device definitions too; metadata only, no driver calls.
        for definition in document.devices.definitions.values():
            declaration = runtime.pluginScanResult.deviceTypes.get(definition.deviceTypeId)
            if declaration is None:
                raise ConfigurationError("E_CONFIG_DEVICE_TYPE", "device type is unavailable")
            declaration.validateDefinition(definition)
        runtime.deviceManager._validatedDefinitions(prepared)
        return document, prepared

    def _commitProjectState(self, connection, before, after):
        if before.get("globalVariables") != after.get("globalVariables"):
            from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
            runtime: Any = self
            ProjectGlobalVariables(runtime.sqliteStore, after["project"]["projectId"],
                after["globalVariables"]).synchronizeInTransaction(connection)

    def _prepareProjectConfiguration(self, payload, cancelled):
        runtime: Any = self
        document, prepared = self._validateProjectConfiguration(payload)
        if cancelled.is_set():
            raise ConfigurationError("E_CONFIG_CANCELLED", "configuration preparation cancelled")
        from .resources import prepareResources, discardResources
        root = runtime.workspaceRoot.parent / "configuration-resources"
        self._reclaimConfigurationResources()
        prepared, resources = prepareResources(prepared, runtime.pluginScanResult.activeOperators,
            runtime.pluginScanResult.deviceTypes, root, cancelled, previous=runtime.loadedDocument,
            raw=document, previousRaw=ProjectDocument.model_validate(runtime.projectConfiguration.current["payload"]),
            journal=runtime.configurationResourceJournal)
        token = ""
        try:
            if document.communication != runtime.loadedDocument.communication:
                from uuid import uuid4
                reserved = uuid4().hex
                runtime.communicationManager.prepareReplacement(reserved)
                token = reserved
            adapters = runtime.deviceManager.prepareReplacement(prepared)
        except BaseException:
            if token:
                runtime.communicationManager.abortDisableSave(token)
            discardResources(resources, root, journal=runtime.configurationResourceJournal)
            raise
        return PreparedConfiguration(prepared, adapters, resources, root, communicationToken=token)

    def _discardProjectConfiguration(self, prepared):
        runtime: Any = self
        if prepared.communicationToken:
            runtime.communicationManager.abortDisableSave(prepared.communicationToken)
        prepared.devices.close()
        if not prepared.installed:
            from .resources import discardResources
            discardResources(prepared.resources, prepared.resourceRoot, journal=runtime.configurationResourceJournal)
        else:
            self._reclaimConfigurationResources()

    def _reclaimConfigurationResources(self):
        """Reclaim only this Runtime's copies at a proven quiescent boundary.

        A persistent worker may still hold an old materialized model even between
        cycles. Terminal status alone is insufficient; actual retirement is required.
        """
        runtime: Any = self
        with runtime._projectStateLock:
            if (runtime.deviceManager.permissions or runtime.deviceManager.workers
                    or runtime.operatorDebugManager.ownsResources()
                    or runtime.livePreviewManager.hasConfigurationSession()
                    or any(not r.isTerminal or runtime.jobSupervisor.ownsJobResources(r.jobId)
                           for r in runtime.jobRepository.all())):
                return
            with runtime.projectConfiguration.cycleLock:
                if runtime.projectConfiguration.cycles or runtime.projectConfiguration.childCycles:
                    return
            used = set()
            def visit(value):
                if isinstance(value, str):
                    used.add(value)
                elif isinstance(value, dict):
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)
            if runtime.loadedDocument is not None:
                visit(runtime.loadedDocument.model_dump(mode="json"))
                used.update(runtime.loadedDocument._pageResourcePaths.values())
            for definition in runtime.deviceManager.definitions.values():
                visit(definition.model_dump(mode="json"))
            from .resources import discardResources
            for path in tuple(runtime._ownedConfigurationResources):
                if str(path) not in used:
                    discardResources([path], runtime.workspaceRoot.parent / "configuration-resources",
                                     journal=runtime.configurationResourceJournal)
                    runtime._ownedConfigurationResources.remove(path)

    def _checkConfigurationAdmission(self, payload):
        runtime: Any = self
        if runtime._closing:
            raise ConfigurationError("E_RUNTIME_CLOSING", "Runtime is closing")
        executionChanged = executionDigest(payload) != executionDigest(runtime.projectConfiguration.current["payload"])
        scopedDevices = self._supportsDeviceReplacement(payload)
        from .impact import affectedJobs
        affected = affectedJobs(runtime, payload)
        if executionChanged and not self._supportsCycleChange(payload) and (any((not record.isTerminal or runtime.jobSupervisor.ownsJobResources(record.jobId)) and record.jobId in affected
                for record in runtime.jobRepository.all()) or runtime.operatorDebugManager.ownsResources()
                or runtime.livePreviewManager.hasConfigurationSession()
                or runtime.deviceManager.maintenanceOwners and not scopedDevices):
            raise ConfigurationError("E_CONFIG_REQUIRES_STOP", "this change has no declared cycle-safe handoff; stop and retire execution first")

    def _supportsDeviceReplacement(self, payload):
        runtime: Any = self
        from copy import deepcopy
        from emo_master.apps.runtime.devices.replacement import ADAPTER_FIELDS
        candidate = deepcopy(payload)
        before = runtime.projectConfiguration.current["payload"]["devices"]["definitions"]
        after = candidate["devices"]["definitions"]
        for key in set(after) - set(before):
            del after[key]
        for key, definition in before.items():
            if key not in after:
                after[key] = definition
            else:
                for field in ADAPTER_FIELDS:
                    after[key][field] = definition[field]
        # DeviceManager reserves and checks only changed adapters. All other
        # changes must retain the existing cycle-safe admission rules.
        return self._supportsCycleChange(candidate)

    def _supportsCycleChange(self, payload):
        from emo_master.core.project.configuration import executionPayload
        runtime: Any = self
        current = runtime.projectConfiguration.current["payload"]
        for change in projectChanges(executionPayload(current), executionPayload(payload)):
            parts = change["parts"]
            if (parts[0] in {"project", "workflowOrder"}
                    or len(parts) >= 3 and parts[0] == "workflows" and parts[2] in {"layout", "name"}
                    or len(parts) >= 5 and parts[0] == "workflows" and parts[2] == "nodes" and parts[4] == "displayName"):
                continue
            if len(parts) >= 5 and parts[:2] == ("devices", "definitions") and parts[3] == "defaults":
                definition = runtime.loadedDocument.devices.definitions[parts[2]]
                declaration = runtime.pluginScanResult.deviceTypes[definition.deviceTypeId]
                if ".".join(parts[4:]) in declaration.nextCyclePaths:
                    continue
            if len(parts) >= 6 and parts[0] == "workflows" and parts[2] == "nodes" and parts[4] == "params":
                workflow = runtime.loadedDocument.workflows[parts[1]]
                node = next((node for node in workflow.nodes if node.nodeId == parts[3]), None)
                descriptor = runtime.pluginScanResult.activeOperators.get(node.operatorId) if node else None
                if descriptor and ".".join(parts[5:]) in descriptor.manifest.nextCycleParameterPaths:
                    continue
            return False
        return True

    def _publishProjectConfiguration(self, document, receipt):
        runtime: Any = self
        if isinstance(document, PreparedConfiguration):
            if document.communicationToken:
                runtime.communicationManager.configure(document.document.project.projectId,
                    runtime.communicationManager.projectKey, document.document.communication,
                    saveToken=document.communicationToken)
                document.communicationToken = ""
            document.devices.install()
            document.installed = True
            runtime._ownedConfigurationResources.update(document.resources)
            document = document.document
        runtime.loadedDocument = document
        from copy import deepcopy
        runtime.loadedPayload = deepcopy(runtime.projectConfiguration.current["payload"])
        runtime.loadedProjectId = document.project.projectId
        with runtime.deviceManager.lock:
            runtime.deviceManager.definitions = {
                key: runtime.pluginScanResult.deviceTypes[value.deviceTypeId].validateDefinition(value)
                for key, value in document.devices.definitions.items()}
            for key, definition in runtime.deviceManager.definitions.items():
                runtime.deviceManager.lifecycles[key].definition = definition
        runtime.eventStore.retentionPerJob = document.runtime.eventRetentionPerJob
        runtime.sqliteStore.jobEventRetention = document.runtime.eventRetentionPerJob
        runtime.jobSupervisor.maxConcurrentJobs = document.runtime.maxConcurrentJobs
        runtime.jobSupervisor.gracefulStopTimeoutMs = document.runtime.gracefulStopTimeoutMs
        runtime.jobSupervisor.heartbeatTimeoutMs = document.runtime.heartbeatTimeoutMs

    @staticmethod
    def _configurationReply(action):
        try:
            return pb.ProjectConfigurationReply(ok=True, result_json=json.dumps(action(), ensure_ascii=True))
        except Exception as error:
            return pb.ProjectConfigurationReply(ok=False, code=getattr(error, "code", "E_CONFIG_INVALID"), message=str(error))

    def GetOnlineProjectSnapshot(self, request, context):
        def snapshot():
            runtime: Any = self
            result = runtime.projectConfiguration.snapshot()
            if request.project_id and request.project_id != result["payload"]["project"]["projectId"]:
                raise ConfigurationError("E_CONFIG_PROJECT", "Runtime contains a different project")
            result["recoveryError"] = runtime.onlineRecoveryError or runtime.projectConfiguration.publicationError
            result["retainedResourceCount"] = len(runtime.configurationResourceJournal.retainedAfterCrash())
            return result
        return self._configurationReply(snapshot)

    def GetOnlineGraphSnapshot(self, request, context):
        def read():
            runtime: Any = self
            current = runtime.projectConfiguration.snapshot()
            projectId = current["payload"]["project"]["projectId"]
            if request.project_id and request.project_id != projectId:
                raise ConfigurationError("E_CONFIG_PROJECT", "Runtime contains a different project")
            store = runtime.projectConfiguration.store
            snapshot = store.historicalSnapshot(request.document_revision) if request.document_revision else current
            jobs = [record for record in runtime.jobRepository.all() if record.projectId == projectId]
            selected = next((record for record in jobs if record.jobId == request.job_id), None)
            if request.job_id and selected is None:
                raise ConfigurationError("E_CONFIG_JOB", "Job does not belong to this online project")
            events = []
            if selected is not None:
                for event in runtime._eventsAfter(selected.jobId, 0):
                    payload = json.loads(event.payloadJson or "{}")
                    if (payload.get("documentRevision") != snapshot["documentRevision"]
                            or payload.get("planRevision") != snapshot["executionRevision"]):
                        continue
                    events.append(dict(sequence=event.sequence, eventType=event.eventType,
                        workflowId=event.workflowId, nodeId=event.nodeId, workflowRunId=event.workflowRunId,
                        nodeRunId=event.nodeRunId, payload=payload, message=event.message))
            document = ProjectDocument.model_validate(snapshot["payload"])
            compiler = WorkflowCompiler(runtime.pluginScanResult.activeOperators,
                deviceTypes=runtime.pluginScanResult.deviceTypes)
            ports = {}
            for key, workflow in document.workflows.items():
                ports[key] = {}
                for node in workflow.nodes:
                    inputs, outputs = compiler.nodePortDefinitions(document, key, node)
                    ports[key][node.nodeId] = dict(inputs=inputs, outputs=outputs)
            return dict(snapshot=snapshot, history=store.history(), runtimeInstanceId=runtime.runtimeInstanceId,
                jobs=[dict(jobId=r.jobId, workflowId=r.workflowId, status=r.status) for r in jobs[-100:]],
                events=events[-200:], ports=ports, readOnly=True)
        return self._configurationReply(read)

    @staticmethod
    def _writeRequest(request):
        if request.ByteSize() > 3 * 1024 * 1024 or len(request.selected_paths) > 4096:
            raise ConfigurationError("E_CONFIG_LIMIT", "configuration request exceeds budget")
        payload = json.loads(request.local_payload_json)
        if not isinstance(payload, dict):
            raise ConfigurationError("E_CONFIG_INVALID", "local configuration must be an object")
        policy = request.replacement_policy or "reject"
        if policy not in {"reject", "stopAffected", "drainAffected"}:
            raise ConfigurationError("E_CONFIG_POLICY", "unknown replacement policy")
        return dict(requestId=request.request_id, expectedRuntimeInstanceId=request.expected_runtime_instance_id,
                    expectedRevision=request.expected_revision, localPayload=payload, selectedPaths=list(request.selected_paths),
                    replacementPolicy=policy)

    def ValidateProjectWrite(self, request, context):
        runtime: Any = self
        def validate():
            write = self._writeRequest(request)
            with runtime._projectStateLock:
                candidate = runtime.projectConfiguration._candidate(write)
                _document, prepared = self._validateProjectConfiguration(candidate,
                    checkAdmission=write["replacementPolicy"] == "reject")
                from .impact import affectedJobs
                from emo_master.apps.runtime.devices.replacement import requiresReplacement
                affected = affectedJobs(runtime, candidate)
                active = [r.jobId for r in runtime.jobRepository.all() if r.jobId in affected
                          and (not r.isTerminal or runtime.jobSupervisor.ownsJobResources(r.jobId))]
                before, after = runtime.deviceManager.definitions, prepared.devices.definitions
                disconnect = [key for key, lifecycle in runtime.deviceManager.lifecycles.items() if lifecycle.connected
                    and (key not in after or requiresReplacement(before[key], after[key],
                        runtime.pluginScanResult.deviceTypes[after[key].deviceTypeId]))]
                return dict(candidate=candidate, cycleSafe=self._supportsCycleChange(candidate),
                    affectedJobs=sorted(active), disconnectDevices=sorted(disconnect),
                    communicationRestart=prepared.communication != runtime.loadedDocument.communication
                        and runtime.communicationManager.state == "LISTENING")
        return self._configurationReply(validate)

    def WriteProjectConfiguration(self, request, context):
        runtime: Any = self
        def start():
            runtime.jobSupervisor.assertMutationAllowed()
            return runtime.projectConfiguration.start(self._writeRequest(request))
        return self._configurationReply(start)

    def GetProjectWriteStatus(self, request, context):
        runtime: Any = self
        return self._configurationReply(lambda: runtime.projectConfiguration.status(request.request_id))

    def CancelProjectWrite(self, request, context):
        runtime: Any = self
        return self._configurationReply(lambda: runtime.projectConfiguration.cancel(request.request_id))

    def DeviceNetworkCommand(self, request, context):
        runtime: Any = self
        try:
            result = runtime.deviceNetwork.execute(request)
            return pb.ProjectConfigurationReply(ok=True, result_json=json.dumps(result))
        except Exception as error:
            return pb.ProjectConfigurationReply(ok=False, code=getattr(error, 'code', 'E_NETWORK_REQUEST'), message=str(error))

    def TransferProjectResource(self, request, context):
        runtime: Any = self
        def transfer():
            with runtime._projectStateLock:
                if runtime._closing or request.runtime_instance_id != runtime.runtimeInstanceId:
                    raise ConfigurationError("E_CONFIG_INSTANCE", "Runtime changed or is closing")
                current = runtime.projectConfiguration.current
                if current is None or request.project_id != current["payload"]["project"]["projectId"]:
                    raise ConfigurationError("E_CONFIG_PROJECT", "resource upload requires the current online project")
                if context is not None and not context.is_active():
                    raise ConfigurationError("E_CONFIG_CANCELLED", "resource transfer cancelled")
            return runtime.projectResourceTransfers.execute(request)
        return self._configurationReply(transfer)
