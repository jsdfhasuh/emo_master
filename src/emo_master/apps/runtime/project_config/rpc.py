"""Online authority; hardware transitions require an explicit replacement policy."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _pb
from emo_master.core.project.configuration import executionDigest
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
    resourcePin: str = ""
    sourcePath: str = ""
    projectChanged: bool = False
    initial: bool = False


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
            commitState=self._commitProjectState,
            exportPath=runtime.workspaceRoot.parent / 'online-project.json' if runtime._runtimeDataLockIsPrimary else None),
            runtime.runtimeInstanceId, lock=runtime._projectStateLock,
            prepare=self._prepareProjectConfiguration, publish=self._publishProjectConfiguration,
            beforeCommit=self._checkConfigurationAdmission, discard=self._discardProjectConfiguration,
            transition=self._transitionProjectConfiguration,
            cycleIdentity=runtime.deviceManager.cycleIdentitySnapshot)
        runtime.onlineRecoveryError = ""
        runtime.onlineRecoveryKind = ""
        runtime.deviceManager.configuration = runtime.projectConfiguration
        current = runtime.projectConfiguration.current
        if current:
            pin = ""
            resourcePhase = False
            try:
                document = ProjectDocument.model_validate(current["payload"])
                resourcePhase = True
                pin = runtime.projectResourceTransfers.pin(document, Path(current["sourcePath"]).parent)
                resourcePhase = False
                from emo_master.core.project.runtime_directory import prepareRuntimeProject
                document = prepareRuntimeProject(document, Path(current["sourcePath"]).parent,
                    runtime.pluginScanResult.activeOperators, deviceTypes=runtime.pluginScanResult.deviceTypes,
                    resourcePaths=runtime.projectResourceTransfers.resolve(document),
                    allowMissingRoot=True,
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
                runtime.loadedProjectFile = current["sourcePath"]
                runtime.loadedProjectPath = str(Path(current["sourcePath"]).parent)
                self._publishProjectConfiguration(document, current)
                runtime.deviceManager.activate()
                runtime._loadedProjectPreviewKey = runtime.previewAssetStore.projectKey(
                    runtime.loadedProjectFile, document.project.projectId)
            except Exception as error:
                runtime.onlineRecoveryError = str(error)
                runtime.onlineRecoveryKind = 'resource' if resourcePhase or getattr(error, 'code', '').startswith('E_RESOURCE_') else 'other'
            finally:
                if pin:
                    runtime.projectResourceTransfers.unpin(pin)

    def _transitionProjectConfiguration(self, payload, cancelled, policy, *, sourcePath=None):
        from .transition import DeviceTransition
        return DeviceTransition(self, payload, cancelled, policy, sourcePath=sourcePath)

    def _configurationSourcePath(self, payload):
        runtime: Any = self
        current = runtime.projectConfiguration.current
        if current["payload"]["project"]["projectId"] == payload["project"]["projectId"]:
            return current["sourcePath"]
        from hashlib import sha256
        identity = sha256(payload["project"]["projectId"].encode("utf-8")).hexdigest()
        return str(runtime.workspaceRoot.parent / "online-projects" / identity / "project.emoproj")

    def _configurationSourceChanged(self, sourcePath):
        current = self.projectConfiguration.current
        return bool(sourcePath and current and Path(sourcePath).resolve() != Path(current['sourcePath']).resolve())

    def _validateProjectConfiguration(self, payload, *, checkAdmission=True, sourcePath=None, cancelled=lambda: False):
        runtime: Any = self
        document = ProjectDocument.model_validate(payload)
        with runtime._projectStateLock:
            current = runtime.loadedDocument
            recovering = bool(runtime.onlineRecoveryError or current is None)
            if recovering:
                snapshot = runtime.projectConfiguration.current
                if snapshot is None:
                    raise ConfigurationError("E_CONFIG_RECOVERY", "no online configuration to repair")
                current = ProjectDocument.model_validate(snapshot["payload"])
                if (runtime.deviceManager.workers or runtime.deviceManager.permissions
                        or runtime.operatorDebugManager.ownsResources()
                        or runtime.livePreviewManager.hasConfigurationSession()
                        or any(not r.isTerminal or runtime.jobSupervisor.ownsJobResources(r.jobId)
                               for r in runtime.jobRepository.all())):
                    raise ConfigurationError("E_CONFIG_REQUIRES_STOP", "retire execution before repairing online recovery")
            projectChanged = document.project.projectId != current.project.projectId
            if document.schemaVersion not in {"2.6", "2.7", "2.8", "2.9"}:
                raise ConfigurationError("E_CONFIG_PROJECT", "online writes require current schema 2.9")
            from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
            with runtime.sqliteStore.connection() as connection:
                ProjectGlobalVariables(runtime.sqliteStore, document.project.projectId,
                    document.globalVariables).validateDefinitions(connection)
            if checkAdmission:
                self._checkConfigurationAdmission(payload, sourcePath=sourcePath)
        from emo_master.core.project.runtime_directory import prepareRuntimeProject
        prepared = prepareRuntimeProject(document, Path(sourcePath or self._configurationSourcePath(payload)).parent,
            runtime.pluginScanResult.activeOperators, deviceTypes=runtime.pluginScanResult.deviceTypes,
            resourcePaths=runtime.projectResourceTransfers.resolve(document),
            allowMissingRoot=True,
            protectedPaths=(*runtime.sqliteProtectedPaths(), runtime.operationalLogWriter.directory))
        if self._configurationSourceChanged(sourcePath) and any(
                node.operatorId == 'vision.io.sqlite_writer' for flow in prepared.workflows.values() for node in flow.nodes):
            from emo_master.apps.runtime.business_sqlite.backend import freezeTargets
            # Validate the new target; publication never creates or copies business databases.
            prepared = runtime.sqliteManagement.run(lambda stopped: freezeTargets(
                prepared, Path(sourcePath).parent, runtime.sqliteProtectedPaths(),
                cancelled=lambda: cancelled() or stopped()))
        from .resources import retainDeviceInputs
        if not recovering and not projectChanged:
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
        if before is None or before["project"]["projectId"] != after["project"]["projectId"] or before.get("globalVariables") != after.get("globalVariables"):
            from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
            runtime: Any = self
            ProjectGlobalVariables(runtime.sqliteStore, after["project"]["projectId"],
                after["globalVariables"]).synchronizeInTransaction(connection)

    def _checkInitialConfigurationAdmission(self):
        runtime: Any = self
        if runtime._closing or runtime.projectConfiguration.closed:
            raise ConfigurationError('E_RUNTIME_CLOSING', 'Runtime is closing')
        if runtime.projectConfiguration.current is not None:
            raise ConfigurationError('E_CONFIG_ACTIVE', 'Runtime already has an online project; compare before writing')
        if (runtime.operatorDebugManager.ownsResources() or runtime.livePreviewManager.hasConfigurationSession()
                or any(not record.isTerminal or runtime.jobSupervisor.ownsJobResources(record.jobId)
                       for record in runtime.jobRepository.all())):
            raise ConfigurationError('E_RESOURCE_BUSY', 'stop and release running Jobs and resource-owning debug sessions before loading')

    def _validateInitialProjectConfiguration(self, payload, sourcePath):
        runtime: Any = self
        document = ProjectDocument.model_validate(payload)
        with runtime._projectStateLock:
            self._checkInitialConfigurationAdmission()
            from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
            with runtime.sqliteStore.connection() as connection:
                ProjectGlobalVariables(runtime.sqliteStore, document.project.projectId,
                    document.globalVariables).validateDefinitions(connection)
        from emo_master.core.project.runtime_directory import prepareRuntimeProject
        prepared = prepareRuntimeProject(document, Path(sourcePath).parent,
            runtime.pluginScanResult.activeOperators, deviceTypes=runtime.pluginScanResult.deviceTypes,
            resourcePaths=runtime.projectResourceTransfers.resolve(document), allowMissingRoot=True,
            protectedPaths=(*runtime.sqliteProtectedPaths(), runtime.operationalLogWriter.directory))
        WorkflowCompiler(runtime.pluginScanResult.activeOperators,
            deviceTypes=runtime.pluginScanResult.deviceTypes).compile(prepared, pluginRootPaths=runtime.pluginRootPaths)
        runtime.deviceManager._validatedDefinitions(prepared)
        return prepared

    def _prepareInitialProjectConfiguration(self, payload, cancelled, sourcePath):
        runtime: Any = self
        document = ProjectDocument.model_validate(payload)
        pin = runtime.projectResourceTransfers.pin(document, Path(sourcePath).parent, cancelled=cancelled)
        resources = []
        root = runtime.workspaceRoot.parent / 'configuration-resources'
        from .resources import prepareResources, discardResources
        try:
            prepared = self._validateInitialProjectConfiguration(payload, sourcePath)
            prepared, resources = prepareResources(prepared, runtime.pluginScanResult.activeOperators,
                runtime.pluginScanResult.deviceTypes, root, cancelled, journal=runtime.configurationResourceJournal)
            if cancelled.is_set():
                raise ConfigurationError('E_CONFIG_CANCELLED', 'initial configuration preparation cancelled')
            adapters = runtime.deviceManager.prepareReplacement(prepared, force=True)
            return PreparedConfiguration(prepared, adapters, resources, root, resourcePin=pin, sourcePath=sourcePath, projectChanged=True, initial=True)
        except BaseException:
            discardResources(resources, root, journal=runtime.configurationResourceJournal)
            runtime.projectResourceTransfers.unpin(pin)
            raise

    def _prepareProjectConfiguration(self, payload, cancelled, *, sourcePath=None):
        runtime: Any = self
        if sourcePath is not None and runtime.projectConfiguration.current is None:
            return self._prepareInitialProjectConfiguration(payload, cancelled, sourcePath)
        document = ProjectDocument.model_validate(payload)
        selectedSource = sourcePath or self._configurationSourcePath(payload)
        if selectedSource != runtime.projectConfiguration.current["sourcePath"]:
            Path(selectedSource).parent.mkdir(parents=True, exist_ok=True)
        pin = runtime.projectResourceTransfers.pin(document,
            Path(selectedSource).parent, cancelled=cancelled)
        try:
            prepared = self._preparePinnedProjectConfiguration(payload, cancelled,
                **({'sourcePath': sourcePath} if sourcePath is not None else {}))
            prepared.resourcePin = pin
            prepared.sourcePath = selectedSource
            return prepared
        except BaseException:
            runtime.projectResourceTransfers.unpin(pin)
            raise

    def _preparePinnedProjectConfiguration(self, payload, cancelled, *, sourcePath=None):
        runtime: Any = self
        document, prepared = self._validateProjectConfiguration(payload, sourcePath=sourcePath, cancelled=cancelled.is_set)
        if cancelled.is_set():
            raise ConfigurationError("E_CONFIG_CANCELLED", "configuration preparation cancelled")
        from .resources import prepareResources, discardResources
        root = runtime.workspaceRoot.parent / "configuration-resources"
        projectChanged = document.project.projectId != runtime.projectConfiguration.current["payload"]["project"]["projectId"]
        self._reclaimConfigurationResources()
        prepared, resources = prepareResources(prepared, runtime.pluginScanResult.activeOperators,
            runtime.pluginScanResult.deviceTypes, root, cancelled, previous=None if projectChanged else runtime.loadedDocument,
            raw=document, previousRaw=(ProjectDocument.model_validate(runtime.projectConfiguration.current["payload"])
                                       if sourcePath is None else None),
            journal=runtime.configurationResourceJournal)
        try:
            adapters = runtime.deviceManager.prepareReplacement(prepared, force=True) if projectChanged else runtime.deviceManager.prepareReplacement(prepared)
        except BaseException:
            discardResources(resources, root, journal=runtime.configurationResourceJournal)
            raise
        return PreparedConfiguration(prepared, adapters, resources, root, projectChanged=projectChanged)

    def _discardProjectConfiguration(self, prepared):
        runtime: Any = self
        try:
            self._discardPreparedProjectConfiguration(prepared)
        finally:
            runtime.projectResourceTransfers.unpin(prepared.resourcePin)

    def _discardPreparedProjectConfiguration(self, prepared):
        runtime: Any = self
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

    def _checkConfigurationAdmission(self, payload, *, sourcePath=None):
        runtime: Any = self
        if runtime._closing:
            raise ConfigurationError("E_RUNTIME_CLOSING", "Runtime is closing")
        if runtime.projectConfiguration.current is None:
            self._checkInitialConfigurationAdmission()
            with runtime._previewJobLock:
                errors = runtime.livePreviewManager.closeAll(timeoutSeconds=3.0)
                errors.extend(runtime.plcDebugManager.closeSessions())
            if errors:
                raise ConfigurationError('E_PREVIEW_RELEASE_FAILED', '; '.join(errors))
            runtime.pureDraftPreviewSessions.closeAll()
            return
        sourceChanged = self._configurationSourceChanged(sourcePath)
        if sourceChanged:
            from .impact import sourceCycleOwners
            with runtime.deviceManager.lock:
                maintenance = bool(runtime.deviceManager.maintenanceOwners)
            if maintenance or sourceCycleOwners(runtime):
                raise ConfigurationError('E_CONFIG_REQUIRES_STOP', '工程目录切换需等待当前任务、检测周期和设备操作结束并释放资源')
        executionChanged = sourceChanged or executionDigest(payload) != executionDigest(runtime.projectConfiguration.current["payload"])
        scopedDevices = self._supportsDeviceReplacement(payload)
        from .impact import affectedJobs
        affected = affectedJobs(runtime, payload, sourceChanged=sourceChanged)
        if executionChanged and (sourceChanged or not self._supportsCycleChange(payload)) and (any((not record.isTerminal or runtime.jobSupervisor.ownsJobResources(record.jobId)) and record.jobId in affected
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
        from copy import deepcopy
        from emo_master.core.project.configuration import applyCycleParameters, applyDeviceCycleParameters, executionDigest
        runtime: Any = self
        current = runtime.projectConfiguration.current["payload"]
        projected = deepcopy(current)
        applyDeviceCycleParameters(projected, payload, runtime.pluginScanResult.deviceTypes)
        applyCycleParameters(projected, payload, runtime.pluginScanResult.activeOperators)
        return executionDigest(projected) == executionDigest(payload)

    def _publishProjectConfiguration(self, document, receipt):
        runtime: Any = self
        initial = isinstance(document, PreparedConfiguration) and document.initial
        if isinstance(document, PreparedConfiguration):
            document.devices.install()
            document.installed = True
            runtime._ownedConfigurationResources.update(document.resources)
            document.devices.close()
            document = document.document
        runtime.loadedDocument = document
        from copy import deepcopy
        runtime.loadedPayload = deepcopy(runtime.projectConfiguration.current["payload"])
        runtime.loadedProjectId = document.project.projectId
        runtime.loadedProjectFile = runtime.projectConfiguration.current["sourcePath"]
        runtime.loadedProjectPath = str(Path(runtime.loadedProjectFile).parent)
        runtime._loadedProjectPreviewKey = runtime.previewAssetStore.projectKey(
            runtime.loadedProjectFile, document.project.projectId)
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
        runtime.onlineRecoveryError = ""
        runtime.onlineRecoveryKind = ""
        if initial:
            runtime.runInspectionStore.maxSessions = (None if document.runtime.maxConcurrentJobs is None
                else max(2, len(document.workflows), document.runtime.maxConcurrentJobs))
        if receipt.get('requestId'):
            from .transition import observeConnections
            observeConnections(runtime.projectConfiguration, receipt, runtime.deviceManager.activate())

    @staticmethod
    def _configurationReply(action):
        try:
            return pb.ProjectConfigurationReply(ok=True, result_json=json.dumps(action(), ensure_ascii=True))
        except Exception as error:
            from dataclasses import asdict
            from pydantic import ValidationError
            from emo_master.core.workflow.errors import WorkflowCompileError, locatedValidationError
            located = locatedValidationError(error, 'E_CONFIG_INVALID') if isinstance(error, ValidationError) else error
            diagnostics = dict(issues=[asdict(issue) for issue in located.issues]) if isinstance(located, WorkflowCompileError) else {}
            return pb.ProjectConfigurationReply(ok=False, code=getattr(error, "code", "E_CONFIG_INVALID"), message=str(located),
                result_json=json.dumps(diagnostics, ensure_ascii=True) if diagnostics else '')

    def GetOnlineProjectSnapshot(self, request, context):
        def snapshot():
            runtime: Any = self
            result = runtime.projectConfiguration.snapshot()
            if request.project_id and request.project_id != result["payload"]["project"]["projectId"]:
                raise ConfigurationError("E_CONFIG_PROJECT", "Runtime contains a different project")
            result["recoveryError"] = runtime.onlineRecoveryError or runtime.projectConfiguration.publicationError
            result["retainedResourceCount"] = len(runtime.configurationResourceJournal.retainedAfterCrash())
            result["versionedStart"] = True
            result['resourceRepair'] = True
            result['projectSourceWrite'] = 1 if context is None else 0
            result['exportCopyError'] = runtime.projectConfiguration.store.exportError
            result['resourceRecoveryAvailable'] = runtime.onlineRecoveryKind == 'resource' and not runtime.projectConfiguration.publicationError
            return result
        return self._configurationReply(snapshot)

    def GetOnlineGraphSnapshot(self, request, context):
        def read():
            runtime: Any = self
            current = runtime.projectConfiguration.snapshot()
            projectId = current["payload"]["project"]["projectId"]
            store = runtime.projectConfiguration.store
            snapshot = store.historicalSnapshot(request.document_revision) if request.document_revision else current
            projectId = snapshot["payload"]["project"]["projectId"]
            if request.project_id and request.project_id != projectId:
                raise ConfigurationError("E_CONFIG_PROJECT", "snapshot belongs to a different project")
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
            return dict(snapshot=snapshot, history=store.history(projectId), runtimeInstanceId=runtime.runtimeInstanceId,
                jobs=[dict(jobId=r.jobId, workflowId=r.workflowId, status=r.status) for r in jobs[-100:]],
                events=events[-200:], ports=ports, readOnly=True)
        return self._configurationReply(read)

    @staticmethod
    def _writeRequest(request, context=None):
        if request.ByteSize() > 3 * 1024 * 1024 or len(request.selected_paths) > 4096:
            raise ConfigurationError("E_CONFIG_LIMIT", "configuration request exceeds budget")
        payload = json.loads(request.local_payload_json)
        if not isinstance(payload, dict):
            raise ConfigurationError("E_CONFIG_INVALID", "local configuration must be an object")
        policy = request.replacement_policy or "reject"
        if policy not in {"reject", "stopAffected", "drainAffected"}:
            raise ConfigurationError("E_CONFIG_POLICY", "unknown replacement policy")
        source = {}
        if request.local_project_path:
            if context is not None:
                raise ConfigurationError('E_CONFIG_SOURCE_REMOTE', '工程目录切换仅适用于本机内嵌 Runtime；远程目录由执行端管理')
            from emo_master.core.project.files import resolveProjectFile
            path = Path(request.local_project_path)
            if not path.is_absolute() or not path.is_file():
                raise ConfigurationError('E_CONFIG_SOURCE', '请选择已保存的本机工程文件：' + str(path))
            source['sourcePath'] = str(resolveProjectFile(path).resolve())
        return dict(requestId=request.request_id, expectedRuntimeInstanceId=request.expected_runtime_instance_id,
                    expectedRevision=request.expected_revision, localPayload=payload, selectedPaths=list(request.selected_paths),
                    replacementPolicy=policy, **source)

    def ValidateProjectWrite(self, request, context):
        runtime: Any = self
        def validate():
            write = self._writeRequest(request, context)
            with runtime._projectStateLock:
                candidate = runtime.projectConfiguration._candidate(write)
                _document, prepared = self._validateProjectConfiguration(candidate,
                    checkAdmission=write["replacementPolicy"] == "reject", sourcePath=write.get('sourcePath'),
                    cancelled=lambda: context is not None and not context.is_active())
                from .impact import affectedJobs, changeEffects
                from emo_master.apps.runtime.devices.replacement import requiresReplacement
                sourceChanged = self._configurationSourceChanged(write.get('sourcePath'))
                affected = affectedJobs(runtime, candidate, sourceChanged=sourceChanged)
                active = [r.jobId for r in runtime.jobRepository.all() if r.jobId in affected
                          and (not r.isTerminal or runtime.jobSupervisor.ownsJobResources(r.jobId))]
                before, after = runtime.deviceManager.definitions, prepared.devices.definitions
                projectChanged = candidate["project"]["projectId"] != runtime.projectConfiguration.current["payload"]["project"]["projectId"]
                disconnect = [key for key, lifecycle in runtime.deviceManager.lifecycles.items() if lifecycle.connected
                    and (projectChanged or key not in after or requiresReplacement(before[key], after[key],
                        runtime.pluginScanResult.deviceTypes[after[key].deviceTypeId]))]
                return dict(candidate=candidate, cycleSafe=not sourceChanged and self._supportsCycleChange(candidate),
                    changeEffects=changeEffects(runtime, candidate),
                    affectedJobs=sorted(active), disconnectDevices=sorted(disconnect),
                    projectChanged=projectChanged, sourcePath=write.get('sourcePath', runtime.projectConfiguration.current['sourcePath']),
                    sourceChanged=sourceChanged,
                    restartDevices=sorted(key for key in disconnect if key in after and after[key].enabled
                        and runtime.pluginScanResult.deviceTypes[after[key].deviceTypeId].connectionLifetime == 'persistent'))
        return self._configurationReply(validate)

    def WriteProjectConfiguration(self, request, context):
        runtime: Any = self
        def start():
            runtime.jobSupervisor.assertMutationAllowed()
            return runtime.projectConfiguration.start(self._writeRequest(request, context))
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
            if request.action == 'info':
                return pb.ProjectConfigurationReply(ok=True, result_json=json.dumps(dict(
                    runtimeInstanceId=runtime.runtimeInstanceId, projectId=runtime.loadedProjectId,
                    deviceSetupVersion=1)))
            if request.action == 'test_connection':
                def admission():
                    if (not runtime._runtimeDataLockIsPrimary or runtime._closing or request.runtime_instance_id != runtime.runtimeInstanceId
                            or request.project_id != runtime.loadedProjectId or runtime.projectConfiguration.active):
                        raise ValueError('Runtime/project changed or configuration is being written')
                    runtime.jobSupervisor.assertMutationAllowed()
                payload = json.loads(request.payload_json)
                if payload.get('confirmed') is not True:
                    raise ValueError('connection test requires explicit confirmation')
                result = runtime.deviceManager.testConnection(payload['definition'],
                    lambda: context is not None and not context.is_active(),
                    admissionLock=runtime._projectStateLock, admission=admission)
                return pb.ProjectConfigurationReply(ok=True, result_json=json.dumps(result))
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
                if request.initial_import_id:
                    if context is not None and not context.is_active():
                        raise ConfigurationError("E_CONFIG_CANCELLED", "resource transfer cancelled")
                    from .imports import transferImportResource
                    return transferImportResource(runtime, request)
                current = runtime.projectConfiguration.current
                if current is None or not request.project_id or len(request.project_id) > 160:
                    raise ConfigurationError("E_CONFIG_PROJECT", "resource upload requires an online authority and a bounded candidate project ID")
                if context is not None and not context.is_active():
                    raise ConfigurationError("E_CONFIG_CANCELLED", "resource transfer cancelled")
            return runtime.projectResourceTransfers.execute(request)
        return self._configurationReply(transfer)

    def ReadProjectResource(self, request, context):
        """Download a declared input/asset in bounded chunks, never an arbitrary path."""
        runtime: Any = self
        try:
            with runtime._projectStateLock:
                if request.runtime_instance_id != runtime.runtimeInstanceId:
                    raise ConfigurationError('E_CONFIG_INSTANCE', 'Runtime instance changed')
                current = runtime.projectConfiguration.current
                if current is None or current['payload']['project']['projectId'] != request.project_id:
                    raise ConfigurationError('E_CONFIG_PROJECT', 'online project does not match')
                if request.expected_document_revision != current['documentRevision']:
                    raise ConfigurationError('E_CONFIG_CONFLICT', 'online document changed')
                document = ProjectDocument.model_validate(current['payload'])
                resource = document.resources.items.get(request.resource_id) if document.resources else None
                if resource is None or request.sha256 != resource.sha256:
                    raise ConfigurationError('E_RESOURCE_DECLARATION', 'resource ID or digest does not match')
                paths = runtime.projectResourceTransfers.resolve(document)
                path = paths.get(request.resource_id)
                if path is None:
                    root = Path(current['sourcePath']).parent.resolve()
                    path = (root / resource.path).resolve()
                    if not path.is_relative_to(root):
                        raise ConfigurationError('E_RESOURCE_PATH', 'resource escapes its declared project root')
                if not path.is_file() or path.stat().st_size != resource.size:
                    raise ConfigurationError('E_RESOURCE_CONTENT', 'declared resource is unavailable or changed')
                if request.offset > resource.size or request.max_bytes > 512 * 1024:
                    raise ConfigurationError('E_RESOURCE_RANGE', 'invalid resource download range')
                # Retain the configuration lock for this single bounded read so
                # normal configuration retirement cannot reclaim an owned file.
                with path.open('rb') as stream:
                    stream.seek(request.offset)
                    data = stream.read(request.max_bytes or 512 * 1024)
                return pb.ReadProjectResourceReply(ok=True, data=data, offset=request.offset,
                    total_size=resource.size, sha256=resource.sha256, eof=request.offset + len(data) >= resource.size)
        except (ConfigurationError, OSError, ValueError) as error:
            return pb.ReadProjectResourceReply(ok=False, code=getattr(error, 'code', 'E_RESOURCE_READ'),
                message=str(error))

    def RetryProjectResourceRecovery(self, request, context):
        runtime: Any = self
        def admission():
            current = runtime.projectConfiguration.current
            if current is None or request.project_id != current['payload']['project']['projectId']:
                raise ConfigurationError('E_CONFIG_PROJECT', 'recovery belongs to another project')
            if runtime.onlineRecoveryKind != 'resource' or not runtime.onlineRecoveryError:
                raise ConfigurationError('E_CONFIG_RECOVERY', 'only resource recovery failures can be retried here')
            with runtime.deviceManager.lock:
                for executor in runtime.deviceManager.executors.values():
                    with executor.condition:
                        if executor.quarantined or executor.closed or executor.active or executor.queue:
                            raise ConfigurationError('E_CONFIG_REQUIRES_STOP', 'device recovery or retirement is still required')
            if (runtime.deviceManager.workers or runtime.deviceManager.permissions or runtime.deviceManager.maintenanceOwners
                    or runtime.operatorDebugManager.ownsResources() or runtime.livePreviewManager.hasConfigurationSession()
                    or any(not r.isTerminal or runtime.jobSupervisor.ownsJobResources(r.jobId) for r in runtime.jobRepository.all())
                    or runtime.projectConfiguration.cycles or runtime.projectConfiguration.childCycles
                    or any(v.connected or v.controlPending for v in runtime.deviceManager.lifecycles.values())
                    or runtime.communicationManager.snapshot()['state'] not in {'STOPPED', 'DISABLED'}):
                raise ConfigurationError('E_CONFIG_REQUIRES_STOP', 'retire old users and connections before resource recovery')
        def start():
            runtime.jobSupervisor.assertMutationAllowed()
            return runtime.projectConfiguration.startRecovery(dict(requestId=request.request_id,
                expectedRuntimeInstanceId=request.expected_runtime_instance_id,
                expectedRevision=request.expected_document_revision, projectId=request.project_id), admission)
        return self._configurationReply(start)
