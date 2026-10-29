from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import Iterable as IterableABC
import inspect
import threading
from typing import Any, Iterable, Protocol, cast
from uuid import uuid4

from emo_master.apps.designer.services.display_calls import DisplayCallContext, DisplayCallError

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as _runtime_pb2
from emo_master.apps.runtime.context.global_counters import MAX_GLOBAL_COUNTER_VALUE
from emo_master.core.contracts.execution import RuntimeEventDTO
from emo_master.core.contracts.legacy_snapshots import normalizeLegacySnapshotPolicy
from emo_master.core.contracts.port_types import (
    PortSpecValidationError,
    validatePortSpec,
)

runtime_pb2: Any = _runtime_pb2


def runtimeConnectionDescription(client):
    """Describe the existing transport without provisioning any display endpoint."""
    from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceServicer, RuntimeServiceStub
    service = getattr(client, 'runtimeService', None)
    if getattr(client, '_closed', False) or service is None:
        return dict(kind='unknown', label='未连接 / 连接类型未知', target='')
    if getattr(client, '_ownedRuntimeService', None) is not None or isinstance(service, RuntimeServiceServicer):
        root = getattr(service, 'workspaceRoot', None)
        directory = str(root.parent) if root is not None else ''
        return dict(kind='embedded', label='本机内嵌 Runtime（随程序启动）' + (' · ' + directory if directory else ''), target='')
    if getattr(client, '_ownedChannel', None) is not None or isinstance(service, RuntimeServiceStub):
        target = getattr(client, '_runtimeTarget', '')
        return dict(kind='grpc', label='独立 Runtime（gRPC）' + (' · ' + target if target else ' · 地址不可用'), target=target)
    return dict(kind='unknown', label='连接类型未知', target='')


def localProjectSourceChange(client, snapshot, projectFile):
    """An embedded Runtime shares the Designer filesystem; gRPC never assumes it."""
    from pathlib import Path
    if not projectFile or not snapshot.get('sourcePath') or runtimeConnectionDescription(client)['kind'] != 'embedded':
        return ''
    local = Path(projectFile).resolve()
    return str(local) if local != Path(snapshot['sourcePath']).resolve() else ''

try:
    import grpc
except Exception:  # pragma: no cover
    grpc = None  # type: ignore[assignment]


@dataclass(frozen=True)
class OperatorDefinition:
    operatorId: str
    displayName: str
    version: str
    category: str
    iconKey: str
    summary: str
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    inputPortSpecs: dict[str, object]
    outputPortSpecs: dict[str, object]
    paramSchema: dict[str, object]
    editorSpec: dict[str, object] = field(default_factory=dict)
    editorIssues: tuple[dict[str, object], ...] = ()
    icon: dict[str, object] = field(default_factory=dict)
    iconIssues: tuple[dict[str, object], ...] = ()
    deviceRequirements: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PreviewSource:
    sourceId: str
    label: str
    sourceKind: str
    workflowId: str
    nodeId: str
    port: str
    width: int
    height: int
    mimeType: str
    iterationPath: tuple[int, ...] = ()
    originJobId: str = ""
    originProjectRevision: int = 0
    captureId: str = ""
    createdAtMs: int = 0
    snapshotState: str = "UNKNOWN"
    workflowRunId: str = ""
    nodeRunId: str = ""
    originalWidth: int = 0
    originalHeight: int = 0


@dataclass(frozen=True)
class PreviewSourceListing:
    sources: tuple[PreviewSource, ...] = ()
    jobId: str = ""
    legacySnapshotPolicy: str = ""
    captureState: str = "UNKNOWN"
    message: str = ""


@dataclass(frozen=True)
class WorkflowInfo:
    workflowId: str
    name: str
    isEntry: bool
    inputs: dict[str, object]
    outputs: dict[str, object]


@dataclass(frozen=True)
class GlobalCounterInfo:
    name: str
    value: int
    updatedAtMs: int


class RuntimeClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.sqliteReply: Any = None
        self.resourceResult: dict[str, Any] | None = None
        self.diagnostics: dict[str, Any] = {}
        super().__init__(message)


class _PlcCallContext(DisplayCallContext):
    """Also interrupt embedded socket owners when the editor cancels an RPC."""
    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list = []
        self.dispatched = False

    def markDispatched(self) -> None:
        self.check()
        self.dispatched = True

    def add_callback(self, callback) -> bool:
        with self._lock:
            if not self.is_active():
                return False
            self._callbacks.append(callback)
            return True

    def cancel(self) -> None:
        super().cancel()
        with self._lock:
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()


class RuntimeServiceProtocol(Protocol):
    def OpenDraftOperatorPreviewSession(self, request, context): ...

    def OpenPlcDebugSession(self, request, context): ...

    def ExecutePlcDebugCommand(self, request, context): ...

    def ClosePlcDebugSession(self, request, context): ...

    def ListOperators(self, request, context): ...

    def ListRejectedOperators(self, request, context): ...

    def GetOperatorEditorAsset(self, request, context): ...

    def GetOperatorIconAsset(self, request, context): ...

    def ListNodePreviewSources(self, request, context): ...

    def UploadPreviewImage(self, request, context): ...

    def StreamPreviewAsset(self, request, context): ...

    def RunOperatorPreview(self, request, context): ...

    def CancelOperatorPreview(self, request, context): ...

    def OpenOperatorPreviewSession(self, request, context): ...

    def StreamOperatorPreviewFrames(self, request, context): ...

    def CloseOperatorPreviewSession(self, request, context): ...

    def OpenCameraConfigurationSession(self, request, context): ...

    def ConfigureOperatorSession(self, request, context): ...

    def ListWorkflows(self, request, context): ...

    def ListGlobalCounters(self, request, context): ...

    def GetGlobalCounter(self, request, context): ...

    def SetGlobalCounter(self, request, context): ...

    def ResetGlobalCounter(self, request, context): ...

    def LoadProject(self, request, context): ...

    def ValidateProject(self, request, context): ...

    def StartJob(self, request, context): ...

    def GetStartRequest(self, request, context): ...

    def StopJob(self, request, context): ...

    def GetJobStatus(self, request, context): ...

    def StreamJobEvents(self, request, context) -> Iterable[object]: ...


class RuntimeEventStream:
    """One consumer owns iteration/close; other threads only request cancellation."""

    def __init__(self, call: object, converter, onClose, cancellation=None) -> None:
        self._call = call
        self._iterator = iter(call) if isinstance(call, IterableABC) else iter(())
        self._converter = converter
        self._onClose = onClose
        self._cancellation = cancellation
        self._iterationLock = threading.Lock()
        self._cancelLock = threading.Lock()
        self._cancelRequested = threading.Event()
        self._retired = threading.Event()
        self._closed = False
        self._cancelIssued = False

    def __iter__(self):
        return self

    def __next__(self) -> RuntimeEventDTO:
        with self._iterationLock:
            if self._closed or self._cancelRequested.is_set():
                self._closeOwned()
                raise StopIteration
            try:
                event = next(self._iterator)
            except BaseException:
                self._closeOwned()
                raise
            if self._cancelRequested.is_set():
                self._closeOwned()
                raise StopIteration
            return self._converter(event)

    def cancel(self) -> None:
        self._cancelRequested.set()
        # Cancelling a gRPC call or an embedded context is thread-safe. Closing
        # an executing Python generator is not: its consumer retires it below.
        with self._cancelLock:
            if not self._closed and not self._cancelIssued:
                if self._cancellation is not None:
                    self._cancellation.cancel()
                cancel = getattr(self._call, "cancel", None)
                if callable(cancel):
                    cancel()
                self._cancelIssued = True
        self._closeIfIdle()

    def close(self) -> None:
        if not self._closeIfIdle():
            self.cancel()

    def _closeIfIdle(self) -> bool:
        if not self._iterationLock.acquire(blocking=False):
            return False
        try:
            self._closeOwned()
            return True
        finally:
            self._iterationLock.release()

    def _closeOwned(self) -> None:
        if self._closed:
            return
        close = getattr(self._call, "close", None)
        if callable(close):
            close()
        # Failed cleanup remains tracked and retryable; never pretend a live
        # iterator is closed just because cancellation was requested.
        self._closed = True
        self._onClose(self)
        self._retired.set()

    def waitClosed(self, timeout: float) -> bool:
        return self._retired.wait(timeout)


class RuntimeClient:
    def __init__(
        self,
        runtimeService: RuntimeServiceProtocol,
        deadlineMs: int = 10000,
        ownedRuntimeService: object | None = None,
        ownedChannel: object | None = None,
        runtimeTarget: str = "",
        displayService: object | None = None,
    ) -> None:
        self.runtimeService = runtimeService
        self.deadlineMs = deadlineMs
        self._ownedRuntimeService = ownedRuntimeService
        self._ownedChannel = ownedChannel
        self._retiredTransports: list[RuntimeClient] = []
        self._streamLock = threading.RLock()
        self._activeStreams: dict[str, RuntimeEventStream] = {}
        self._retiringStreams: set[RuntimeEventStream] = set()
        self._closed = False
        self._closing = False
        self._closeLock = threading.RLock()
        self._transportLock = threading.RLock()
        self._rpcCalls = 0
        self.inspectionPeakScratchBytes = 0
        self._runtimeTarget = runtimeTarget
        self._displayService = displayService
        self._displayChannel = None
        self._presentationService: Any = None
        self._presentationServer: Any = None
        self._startLock = threading.RLock()
        if self._displayService is None and runtimeTarget and ownedChannel is not None:
            from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import DisplayServiceStub
            self._displayService = DisplayServiceStub(ownedChannel)

        self.cameraRecoveryWarnings: list[dict] = []
        self._displayLock = threading.RLock()
        self._displayCalls: dict[str, set[DisplayCallContext]] = {}
        self._closedDisplayOwners: set[str] = set()
        self.runtimeScope = uuid4().hex
        self._displayDisconnected = False
        subscribe = getattr(ownedChannel, "subscribe", None)
        if callable(subscribe):
            subscribe(self._onChannelState, try_to_connect=False)

    def _onChannelState(self, state) -> None:
        if grpc is None:
            return
        with self._displayLock:
            if self._closed or self._closing:
                return
            if state == grpc.ChannelConnectivity.TRANSIENT_FAILURE:
                self._displayDisconnected = True
            elif state == grpc.ChannelConnectivity.READY and self._displayDisconnected:
                self._displayDisconnected = False
                self.renewDisplaySession()

    def renewDisplaySession(self) -> None:
        with self._displayLock:
            self.runtimeScope = uuid4().hex
            contexts = [ctx for calls in self._displayCalls.values() for ctx in calls]
        for context in contexts:
            context.cancel()

    def switchConnection(self, candidate):
        """Keep controller references stable; never close an old embedded owner implicitly."""
        with self._closeLock, self._transportLock, self._streamLock, self._displayLock:
            if self._closed or self._closing or self._rpcCalls or self._activeStreams or self._retiringStreams or self._displayCalls:
                raise RuntimeClientError('E_RUNTIME_BUSY', '当前请求尚未结束，请等待当前操作完成后切换 Runtime')
            fields = ('runtimeService', '_ownedRuntimeService', '_ownedChannel', '_runtimeTarget', '_displayService',
                      '_displayChannel', '_presentationService', '_presentationServer')
            previous = RuntimeClient(self.runtimeService)
            for field in fields:
                setattr(previous, field, getattr(self, field))
            unsubscribe = getattr(self._ownedChannel, 'unsubscribe', None)
            if callable(unsubscribe):
                unsubscribe(self._onChannelState)
            unsubscribe = getattr(candidate._ownedChannel, 'unsubscribe', None)
            if callable(unsubscribe):
                unsubscribe(candidate._onChannelState)
            for field in fields:
                setattr(self, field, getattr(candidate, field))
                if field not in {'runtimeService', '_runtimeTarget'}:
                    setattr(candidate, field, None)
            candidate._closed = True
            self._retiredTransports.append(previous)
            self.runtimeScope = uuid4().hex
            self._displayDisconnected = False
            subscribe = getattr(self._ownedChannel, 'subscribe', None)
            if callable(subscribe):
                subscribe(self._onChannelState, try_to_connect=False)

    def closeDisplayOwner(self, owner: str) -> None:
        with self._displayLock:
            self._closedDisplayOwners.add(owner)
            calls = self._displayCalls.pop(owner, set())
        for context in calls:
            context.cancel()

    def listOperators(self, *, timeoutMs: int | None = None,
                      cancellationToken: DisplayCallContext | None = None,
                      owner: str = "catalog") -> list[OperatorDefinition]:
        request = runtime_pb2.ListOperatorsRequest()
        reply = (self._call("ListOperators", request) if timeoutMs is None and cancellationToken is None
                 else self._displayCall("ListOperators", request, timeoutMs or self.deadlineMs, cancellationToken, owner))
        parsed: list[OperatorDefinition] = []
        try:
            warnings = json.loads(str(getattr(reply, "camera_recovery_warnings_json", "") or "[]"))
            self.cameraRecoveryWarnings = warnings if isinstance(warnings, list) else []
        except (ValueError, TypeError):
            self.cameraRecoveryWarnings = [{"error": "invalid camera recovery record"}]
        for operatorInfo in getattr(reply, "operators", []):
            rawCategory = str(getattr(operatorInfo, "category", "Other")).strip()
            inputPorts = self._toStrMap(
                getattr(operatorInfo, "input_ports", {})
            )
            outputPorts = self._toStrMap(
                getattr(operatorInfo, "output_ports", {})
            )
            parsed.append(
                OperatorDefinition(
                    operatorId=str(getattr(operatorInfo, "operator_id", "")),
                    deviceRequirements=self._parseSchema(getattr(operatorInfo, "device_requirements_json", "{}")),
                    displayName=str(getattr(operatorInfo, "display_name", "")),
                    version=str(getattr(operatorInfo, "version", "")),
                    category=rawCategory or "Other",
                    iconKey=str(getattr(operatorInfo, "icon_key", "default")),
                    summary=str(getattr(operatorInfo, "summary", "")),
                    inputPorts=inputPorts,
                    outputPorts=outputPorts,
                    inputPortSpecs=self._parsePortSpecs(
                        getattr(operatorInfo, "input_port_specs_json", ""),
                        inputPorts,
                    ),
                    outputPortSpecs=self._parsePortSpecs(
                        getattr(operatorInfo, "output_port_specs_json", ""),
                        outputPorts,
                    ),
                    paramSchema=self._parseSchema(
                        getattr(operatorInfo, "param_schema_json", "{}")
                    ),
                    editorSpec=self._parseSchema(
                        getattr(operatorInfo, "editor_spec_json", "{}")
                    ),
                    editorIssues=tuple(
                        item
                        for item in self._parseJsonList(
                            getattr(operatorInfo, "editor_issues_json", "[]")
                        )
                        if isinstance(item, dict)
                    ),
                    icon={
                        "status": str(getattr(getattr(operatorInfo, "icon", None), "status", "") or "none"),
                        "mimeType": str(getattr(getattr(operatorInfo, "icon", None), "mime_type", "")),
                        "sha256": str(getattr(getattr(operatorInfo, "icon", None), "sha256", "")),
                        "byteSize": int(getattr(getattr(operatorInfo, "icon", None), "byte_size", 0)),
                    },
                    iconIssues=tuple({"ruleId": issue.rule_id, "code": issue.code, "message": issue.message}
                                     for issue in getattr(operatorInfo, "icon_issues", ())),
                )
            )
        return parsed

    def _projectConfigurationCall(self, method, request):
        reply = self._call(method, request)
        if not reply.ok:
            error = RuntimeClientError(reply.code, reply.message)
            if reply.result_json:
                error.diagnostics = json.loads(reply.result_json)
            raise error
        return json.loads(reply.result_json)

    def getOnlineProjectSnapshot(self, projectId=""):
        return self._projectConfigurationCall("GetOnlineProjectSnapshot",
            runtime_pb2.OnlineProjectRequest(project_id=projectId))

    def initializeProjectConfiguration(self, payload, resourceRoot, requestId, *, validate=False, runtimeInstanceId='',
                                       projectPath=''):
        # This entry passes a local filesystem root, not an upload protocol.
        # Remote installation needs its own import path; never pretend that a
        # Designer path is available on another machine.
        from urllib.parse import urlsplit
        target = self._runtimeTarget
        if target and urlsplit('//' + target).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise RuntimeClientError('E_CONFIG_INITIAL_REMOTE',
                '远程 Runtime 尚无在线工程；请先在执行端导入工程，再比较、上传及写入。不会发送 Designer 本地路径。')
        return self._projectConfigurationCall('InitializeProjectConfiguration',
            runtime_pb2.InitialProjectConfigurationRequest(request_id=requestId,
                local_payload_json=json.dumps(payload, ensure_ascii=True), resource_root=str(resourceRoot or ''),
                validate_only=validate, expected_runtime_instance_id=runtimeInstanceId, project_path=str(projectPath or '')))

    def getOnlineGraphSnapshot(self, projectId="", documentRevision=0, jobId=""):
        return self._projectConfigurationCall("GetOnlineGraphSnapshot", runtime_pb2.OnlineGraphRequest(
            project_id=projectId, document_revision=documentRevision, job_id=jobId))

    def writeProjectConfiguration(self, snapshot, localPayload, selectedPaths, requestId, *, validate=False, replacementPolicy="reject",
                                  localProjectPath=''):
        if localProjectPath:
            if runtimeConnectionDescription(self)['kind'] != 'embedded':
                raise RuntimeClientError('E_CONFIG_SOURCE_REMOTE', '独立 Runtime 的工程目录由执行端管理，不能使用 Designer 本地目录')
            if snapshot.get('projectSourceWrite') != 1:
                raise RuntimeClientError('E_CONFIG_SOURCE_UNSUPPORTED', '当前 Runtime 不支持工程目录切换，请升级 Runtime 后重新比较')
        return self._projectConfigurationCall("ValidateProjectWrite" if validate else "WriteProjectConfiguration",
            runtime_pb2.ProjectConfigurationWriteRequest(request_id=requestId,
                expected_runtime_instance_id=snapshot["runtimeInstanceId"], expected_revision=snapshot["documentRevision"],
                local_payload_json=json.dumps(localPayload, ensure_ascii=True), selected_paths=selectedPaths,
                replacement_policy=replacementPolicy, local_project_path=str(localProjectPath or '')))

    def getProjectWriteStatus(self, requestId):
        return self._projectConfigurationCall("GetProjectWriteStatus", runtime_pb2.ProjectWriteLookup(request_id=requestId))

    def deviceNetworkCommand(self, projectId, runtimeId, action, *, deviceId='', requestId='', payload=None):
        try:
            return self._projectConfigurationCall('DeviceNetworkCommand', runtime_pb2.DeviceNetworkRequest(
                project_id=projectId, runtime_instance_id=runtimeId, device_id=deviceId, request_id=requestId,
                action=action, payload_json=json.dumps(payload or {})))
        except RuntimeClientError as error:
            if action == 'info' and error.code in {'E_NETWORK_IDENTITY', 'StatusCode.UNIMPLEMENTED', 'UNIMPLEMENTED'}:
                raise RuntimeClientError('UNIMPLEMENTED', 'Runtime 不支持独立设备设置与连接探测，请升级 Runtime') from error
            raise

    def cancelProjectWrite(self, requestId):
        return self._projectConfigurationCall("CancelProjectWrite", runtime_pb2.ProjectWriteLookup(request_id=requestId))

    def transferProjectResource(self, snapshot, transferId, action, *, resource=None, offset=0, data=b"", replacementOf=""):
        return self._projectConfigurationCall("TransferProjectResource", runtime_pb2.ProjectResourceTransferRequest(
            runtime_instance_id=snapshot["runtimeInstanceId"], project_id=snapshot["payload"]["project"]["projectId"],
            transfer_id=transferId, action=action, resource_json=json.dumps(resource) if resource is not None else "",
            offset=offset, data=data, replacement_of=replacementOf,
            expected_document_revision=snapshot['documentRevision'] if replacementOf else 0))

    def retryProjectResourceRecovery(self, snapshot, requestId):
        if not snapshot.get('resourceRepair'):
            raise RuntimeClientError('UNIMPLEMENTED', 'Runtime does not support resource recovery; upgrade Runtime')
        return self._projectConfigurationCall('RetryProjectResourceRecovery', runtime_pb2.RetryProjectResourceRecoveryRequest(
            request_id=requestId, project_id=snapshot['payload']['project']['projectId'],
            expected_runtime_instance_id=snapshot['runtimeInstanceId'], expected_document_revision=snapshot['documentRevision']))

    def reconcileProjectResources(self):
        return self._queryResourceTransfers('STATUS', list(getattr(self, 'pendingResourceTransfers', {})))

    def cancelProjectResourceTransfers(self, transferIds):
        return self._queryResourceTransfers('CANCEL', transferIds)

    def _recordResourceTransfer(self, results, transferId, key, result):
        state = result.get('state')
        item = dict(result, resourceId=key, requestTransferId=transferId)
        category = {'READY': 'uploaded', 'CANCELLED': 'cancelled', 'PENDING': 'pending'}.get(state)
        if category is None:
            category = 'errors'
            item.update(code=result.get('code', 'E_RESOURCE_TRANSFER_STATE'),
                        message=result.get('message', f'resource {transferId}: {state}; query original request'))
        elif state in {'READY', 'CANCELLED'}:
            self.pendingResourceTransfers.pop(transferId, None)
        results[category].append(item)
        return category

    def _queryResourceTransfers(self, action, transferIds):
        results = dict(uploaded=[], cancelled=[], pending=[], errors=[])
        for transferId in dict.fromkeys(transferIds):
            saved = getattr(self, 'pendingResourceTransfers', {}).get(transferId)
            if saved is None:
                results['errors'].append(dict(requestTransferId=transferId, code='E_RESOURCE_TRANSFER_UNKNOWN',
                    message='no saved upload identity; no request was sent'))
                continue
            snapshot, key = saved
            try:
                result = self.transferProjectResource(snapshot, transferId, action)
                self._recordResourceTransfer(results, transferId, key, result)
            except Exception as error:
                results['errors'].append(dict(resourceId=key, requestTransferId=transferId,
                    code=getattr(error, 'code', 'E_RESOURCE_OUTCOME_UNKNOWN'), message=str(error)))
        return results

    def uploadProjectResources(self, snapshot, payload, root, cancelled=None, *, repairConfirmed=False):
        from pathlib import Path
        from emo_master.core.project.models import ProjectDocument
        from emo_master.core.project.snapshots import verifyResources
        results = dict(uploaded=[], cancelled=[], pending=[], errors=[])
        key, transferId, stage = '', '', 'VALIDATE'
        begun = committing = False
        try:
            document = ProjectDocument.model_validate(payload)
            snapshot = dict(snapshot, payload=payload)
            root = Path(root).resolve()
            paths = verifyResources(document, root.parent if root.is_file() else root)
            if getattr(self, 'pendingResourceTransfers', {}):
                raise RuntimeClientError('E_RESOURCE_OUTCOME_UNKNOWN', 'query the original pending resource transfer before another upload')
            self.pendingResourceTransfers = {}
            for key, path in paths.items():
                transferId, stage = '', 'CHECK_CANCELLED'
                begun = committing = False
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeClientError("E_CONFIG_CANCELLED", "resource upload cancelled")
                transferId, stage = uuid4().hex, 'LOOKUP'
                resource = document.resources.items[key].model_dump(mode="json")
                existing = self.transferProjectResource(snapshot, transferId, "LOOKUP", resource=resource)
                if existing["state"] == "READY":
                    results['uploaded'].append(dict(existing, resourceId=key, requestTransferId=existing['transferId']))
                    continue
                replacement = ''
                stage = 'CONFIRM_REPAIR'
                if existing['state'] == 'CORRUPT':
                    if not repairConfirmed:
                        raise RuntimeClientError('E_RESOURCE_REPAIR_CONFIRM', 'confirm repair using the matching local original')
                    if not snapshot.get('resourceRepair'):
                        raise RuntimeClientError('UNIMPLEMENTED', 'Runtime does not support explicit resource repair')
                    replacement = existing['transferId']
                elif existing['state'] != 'MISSING':
                    raise RuntimeClientError('E_RESOURCE_TRANSFER_STATE', 'unexpected resource lookup state')
                stage = 'BEGIN'
                self.pendingResourceTransfers[transferId] = (snapshot, key)
                self.transferProjectResource(snapshot, transferId, "BEGIN", resource=resource, replacementOf=replacement)
                begun = True
                stage = 'OPEN'
                offset = 0
                with Path(path).open("rb") as stream:
                    while True:
                        stage = 'READ'
                        chunk = stream.read(512 * 1024)
                        if not chunk:
                            break
                        stage = 'CHECK_CANCELLED'
                        if cancelled is not None and cancelled.is_set():
                            raise RuntimeClientError("E_CONFIG_CANCELLED", "resource upload cancelled")
                        stage = 'CHUNK'
                        self.transferProjectResource(snapshot, transferId, "CHUNK", offset=offset, data=chunk)
                        offset += len(chunk)
                # Cancellation can arrive while the final CHUNK reply is in
                # flight. Honor it before starting a new COMMIT at EOF.
                stage = 'CHECK_CANCELLED'
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeClientError("E_CONFIG_CANCELLED", "resource upload cancelled")
                stage = 'COMMIT'
                committing = True
                try:
                    result = self.transferProjectResource(snapshot, transferId, "COMMIT")
                except Exception as error:
                    # STATUS is a read, never a second COMMIT or implicit repair.
                    checked = self._queryResourceTransfers('STATUS', [transferId])
                    for category, items in checked.items():
                        results[category].extend(dict(item, stage='STATUS') for item in items)
                    if checked['errors']:
                        if isinstance(error, RuntimeClientError):
                            raise
                        raise RuntimeClientError('E_RESOURCE_OUTCOME_UNKNOWN',
                            f'query original resource transfer {transferId}; no retry: {error}') from error
                    if checked['uploaded']:
                        continue
                    results['errors'].append(dict(resourceId=key, requestTransferId=transferId, stage=stage,
                        code=getattr(error, 'code', 'E_RESOURCE_OUTCOME_UNKNOWN'), message=str(error)))
                    results['aborted'] = True
                    return results
                if self._recordResourceTransfer(results, transferId, key, result) != 'uploaded':
                    results['aborted'] = True
                    return results
        except Exception as error:
            results['aborted'] = True
            results['errors'].append(dict(resourceId=key, requestTransferId=transferId, stage=stage,
                code=getattr(error, 'code', 'E_RESOURCE_UPLOAD'), message=str(error)))
            # These BEGIN errors precede insertion in the existing Runtime.
            # UNKNOWN, storage failures and lost replies do not prove rejection.
            if stage == 'BEGIN' and isinstance(error, RuntimeClientError) and error.code in {
                    'E_CONFIG_INSTANCE', 'E_RESOURCE_TRANSFER_CAPACITY'}:
                self.pendingResourceTransfers.pop(transferId, None)
            elif begun and not committing:
                checked = self._queryResourceTransfers('CANCEL', [transferId])
                for category, items in checked.items():
                    results[category].extend(dict(item, stage='CANCEL') for item in items)
            if isinstance(error, RuntimeClientError):
                error.resourceResult = results
                raise
            wrapped = RuntimeClientError('E_RESOURCE_UPLOAD', str(error))
            if getattr(error, 'issues', None):
                from dataclasses import asdict
                wrapped.diagnostics = {'issues': [asdict(issue) for issue in error.issues]}
            wrapped.resourceResult = results
            raise wrapped from error
        return results

    def _deviceManagement(self):
        from emo_master.clients.runtime.devices import DeviceManagementClient
        return DeviceManagementClient(self.runtimeService, call=self._deviceManagementCall)

    def _deviceManagementCall(self, name, request, *, timeoutMs, cancellation):
        return self._displayCall(name, request, timeoutMs, cancellation, 'device-management')

    def _deviceCall(self, method, *args, **kwargs):
        from emo_master.clients.runtime.devices import DeviceClientError
        try:
            return getattr(self._deviceManagement(), method)(*args, **kwargs)
        except DeviceClientError as error:
            raise RuntimeClientError(error.code, str(error)) from error

    def listDeviceTypes(self):
        return self._deviceCall('listDeviceTypes')

    def getDeviceStates(self, projectId):
        return self._deviceCall('getDeviceStates', projectId)

    def getDeviceOverview(self, projectId, *, cancellation=None):
        return self._deviceCall('getDeviceOverview', projectId, cancellation=cancellation)

    def getDeviceDiagnostics(self, scope, deviceId, cursor=0):
        return self._deviceCall('getDeviceDiagnostics', scope, deviceId, cursor)

    def controlDevice(self, projectId, runtimeInstanceId, deviceId, state, action, *, confirmed=False):
        return self._deviceCall('controlDevice', projectId, runtimeInstanceId, deviceId, state, action,
                                                       confirmed=confirmed)

    def executeDeviceAction(self, projectId, runtimeInstanceId, deviceId, state, action, arguments):
        return self._deviceCall('executeDeviceAction', projectId, runtimeInstanceId, deviceId, state, action, arguments)

    def recoverDevice(self, projectId, runtimeInstanceId, deviceId, state, *, hardwareSynchronized=False):
        return self._deviceCall('recoverDevice', projectId, runtimeInstanceId, deviceId, state,
                                                       hardwareSynchronized=hardwareSynchronized)

    def cameraDiagnostic(self, projectId, runtimeInstanceId, deviceId, state, action, *, sessionId="", sequence=0,
                         confirmed=False, changes=None):
        reply = self._call("CameraDiagnostic", runtime_pb2.CameraDiagnosticRequest(project_id=projectId,
            runtime_instance_id=runtimeInstanceId, device_id=deviceId, expected_generation=state["generation"],
            expected_definition_revision=state["definitionRevision"], action=action, session_id=sessionId,
            sequence=sequence, confirmed=confirmed, changes_json=json.dumps(changes or {})))
        if not reply.ok:
            raise ValueError(reply.code + ": " + reply.message)
        return dict(sessionId=reply.session_id, metadata=json.loads(reply.metadata_json or "{}"), preview=reply.preview_jpeg)

    def getOperatorIconAsset(self, operatorId: str, version: str, expectedSha256: str,
                             *, timeoutMs: int = 2000,
                             cancellationToken: DisplayCallContext | None = None,
                             owner: str = "icons") -> object:
        if not callable(getattr(self.runtimeService, "GetOperatorIconAsset", None)):
            raise RuntimeClientError("UNIMPLEMENTED", "Runtime has no icon asset API")
        return self._displayCall("GetOperatorIconAsset", runtime_pb2.GetOperatorIconAssetRequest(
            operator_id=operatorId, version=version, expected_sha256=expectedSha256,
        ), timeoutMs, cancellationToken, owner)

    def _displayCall(self, methodName: str, request: object, timeoutMs: int,
                     token: DisplayCallContext | None, owner: str, *, preserveCompleted=None):
        context = token or DisplayCallContext()
        with self._displayLock:
            if self._closed or self._closing or owner in self._closedDisplayOwners:
                raise RuntimeClientError("E_DISPLAY_CANCELLED", "display owner is closed")
            self._displayCalls.setdefault(owner, set()).add(context)
        try:
            context.start(timeoutMs)
            method = getattr(self.runtimeService, methodName)
            future = getattr(method, "future", None)
            markDispatched = getattr(context, "markDispatched", None)
            if callable(markDispatched):
                markDispatched()
            if callable(future):
                call = future(request, timeout=context.time_remaining(), wait_for_ready=False)
                context.attach(call)
                reply = call.result()
            else:
                parameters = _signatureParameters(method)
                if "timeout" in parameters and "context" not in parameters:
                    reply = method(request, timeout=context.time_remaining())
                else:
                    reply = method(request, context)
            if preserveCompleted is None or not preserveCompleted(reply):
                context.check()
            return reply
        except Exception as err:
            try:
                context.check()
            except DisplayCallError as expired:
                raise RuntimeClientError(expired.code, str(expired)) from err
            if grpc is not None and isinstance(err, grpc.RpcError):
                raise RuntimeClientError(str(err.code()), err.details() or "display RPC failed") from err
            raise
        finally:
            with self._displayLock:
                calls = self._displayCalls.get(owner)
                if calls is not None:
                    calls.discard(context)
                    if not calls:
                        self._displayCalls.pop(owner, None)

    def getOperatorEditorAsset(self, operatorId: str, version: str = "") -> object:
        return self._call(
            "GetOperatorEditorAsset",
            runtime_pb2.GetOperatorEditorAssetRequest(
                operator_id=operatorId, version=version
            ),
        )

    def listNodePreviewSources(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
        jobId: str = "",
        inspectionSessionId: str = "",
    ) -> list[PreviewSource]:
        """Compatibility wrapper; sourceKind remains a spatial relationship."""
        return list(self.listNodePreviewSourcesWithMetadata(
            projectId, workflowId, nodeId, jobId=jobId, inspectionSessionId=inspectionSessionId).sources)

    def listNodePreviewSourcesWithMetadata(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
        jobId: str = "",
        inspectionSessionId: str = "",
        cancellation: DisplayCallContext | None = None,
        workflowRunId: str = "",
        nodeRunId: str = "",
    ) -> PreviewSourceListing:
        request = runtime_pb2.ListNodePreviewSourcesRequest(
                project_id=projectId,
                workflow_id=workflowId,
                node_id=nodeId,
                job_id=jobId,
                inspection_session_id=inspectionSessionId,
                workflow_run_id=workflowRunId,
                node_run_id=nodeRunId,
            )
        reply = (self._call("ListNodePreviewSources", request) if cancellation is None else
                 self._displayCall("ListNodePreviewSources", request, 5000, cancellation, 'node-inspection'))
        replyJobId = str(getattr(reply, "job_id", ""))
        policy = str(getattr(reply, "legacy_snapshot_policy", ""))
        captureState = str(getattr(reply, "capture_state", "")) or "UNKNOWN"
        sources = []
        for source in getattr(reply, "sources", []):
            originJobId = str(getattr(source, "origin_job_id", ""))
            captureId = str(getattr(source, "capture_id", ""))
            state = str(getattr(source, "snapshot_state", "")) or "UNKNOWN"
            # Older endpoints may ignore the job filter. Missing provenance must
            # never promote the spatial `current` source to the current Run.
            if state not in {"CURRENT", "PREVIOUS", "UNKNOWN"}:
                state = "UNKNOWN"
            if state == "CURRENT" and (not jobId or not captureId or replyJobId != jobId or originJobId != jobId
                                       or policy != "ALL" or captureState != "CURRENT_AVAILABLE"):
                state = "UNKNOWN"
            sources.append(PreviewSource(
                sourceId=str(getattr(source, "source_id", "")),
                label=str(getattr(source, "label", "")),
                sourceKind=str(getattr(source, "source_kind", "")),
                workflowId=str(getattr(source, "workflow_id", "")),
                nodeId=str(getattr(source, "node_id", "")),
                port=str(getattr(source, "port", "")),
                width=int(getattr(source, "width", 0)),
                height=int(getattr(source, "height", 0)),
                mimeType=str(getattr(source, "mime_type", "")),
                iterationPath=self._parseIterationPath(
                    getattr(source, "iteration_path_json", "[]")),
                originJobId=originJobId,
                originProjectRevision=int(getattr(source, "origin_project_revision", 0)),
                captureId=captureId,
                createdAtMs=int(getattr(source, "created_at_ms", 0)),
                snapshotState=state,
                workflowRunId=str(getattr(source, "workflow_run_id", "")),
                nodeRunId=str(getattr(source, "node_run_id", "")),
                originalWidth=int(getattr(source, 'original_width', 0)),
                originalHeight=int(getattr(source, 'original_height', 0)),
            ))
        return PreviewSourceListing(
            sources=tuple(sources), jobId=replyJobId,
            legacySnapshotPolicy=policy,
            captureState=captureState,
            message=str(getattr(reply, "message", "")),
        )

    def uploadPreviewImage(
        self,
        data: bytes,
        filename: str = "",
        projectId: str = "",
        *, draftSessionId: str = "",
    ) -> object:
        uploadId = f"upload-{threading.get_ident()}"

        def chunks():
            for offset in range(0, len(data), 256 * 1024):
                yield runtime_pb2.PreviewUploadChunk(
                    upload_id=uploadId,
                    filename=filename if offset == 0 else "",
                    content=data[offset : offset + 256 * 1024],
                    project_id=projectId,
                    draft_session_id=draftSessionId,
                )

        return self._call("UploadPreviewImage", chunks())

    def sqliteTarget(self, action: str, databasePath: str, projectDirectory: str,
                     table: str = "", columns=None, *, confirmed=False, cancellationToken=None):
        method = {"inspect": "InspectSqliteTarget", "initialize": "InitializeSqliteTarget"}[action]
        if not callable(getattr(self.runtimeService, method, None)):
            raise RuntimeClientError("E_SQLITE_UNSUPPORTED", "旧 Runtime 不支持 SQLite 目标检查/初始化")
        wire = [runtime_pb2.SqliteColumnPlan(name=c["name"], storage_type=c["storageType"],
            nullable=c.get("nullable", False), has_default="default" in c,
            default_json=json.dumps(c["default"], ensure_ascii=False, allow_nan=False) if "default" in c else "") for c in columns or []]
        fields = dict(database_path=databasePath, project_directory=projectDirectory, table=table)
        request = (runtime_pb2.InitializeSqliteTargetRequest(**fields, columns=wire, confirmed=confirmed)
                   if action == "initialize" else runtime_pb2.InspectSqliteTargetRequest(**fields, proposed_columns=wire))
        try:
            reply = (self._call(method, request) if cancellationToken is None else
                     self._displayCall(method, request, self.deadlineMs, cancellationToken, 'sqlite-editor'))
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" in error.code:
                raise RuntimeClientError("E_SQLITE_UNSUPPORTED", "旧 Runtime 不支持 SQLite 管理接口") from error
            raise
        if not getattr(reply, "ok", False):
            rejected = RuntimeClientError(str(reply.code), str(reply.message))
            rejected.sqliteReply = reply
            raise rejected
        return reply

    def inspectionSession(self, action: str, projectId: str, sessionId: str = ""):
        method = {"open": "OpenRunInspectionSession", "renew": "RenewRunInspectionSession",
                  "close": "CloseRunInspectionSession"}[action]
        if not callable(getattr(self.runtimeService, method, None)):
            raise RuntimeClientError("E_INSPECTION_UNSUPPORTED", "Runtime 不支持两次运行图片检查会话")
        try:
            reply = self._call(method, runtime_pb2.RunInspectionSessionRequest(project_id=projectId, session_id=sessionId))
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" in error.code:
                raise RuntimeClientError("E_INSPECTION_UNSUPPORTED", "旧 Runtime 不支持运行检查会话；无法保留两次节点图片") from error
            raise
        if not getattr(reply, "ok", False):
            raise RuntimeClientError(str(reply.code), str(reply.message))
        return reply

    def readInspectionAsset(self, projectId: str, sessionId: str, assetId: str,
                            cancellation: DisplayCallContext) -> tuple[bytearray, str]:
        """One bounded stream, retired by its actual consumer, not cancellation."""
        cancellation.start(5000)
        request = runtime_pb2.GetPreviewAssetRequest(project_id=projectId, asset_id=assetId,
                                                    inspection_session_id=sessionId)
        method: Any = self.runtimeService.StreamPreviewAsset
        if "context" in _signatureParameters(method):
            stream = method(request, cancellation)
        else:
            stream = method(request, timeout=cancellation.time_remaining())
            cancellation.attach(stream)
        buffer = bytearray()
        mime = ""
        complete = False
        try:
            for chunk in stream:
                cancellation.check()
                if str(chunk.asset_id) != assetId:
                    raise RuntimeClientError("E_INSPECTION_IDENTITY", "读图资产标识不匹配")
                size = len(buffer) + len(chunk.content)
                if size > 4 * 1024 * 1024:
                    raise RuntimeClientError("E_INSPECTION_READ_BUDGET", "编码图片超过4 MiB，读图与 Qt 转换暂存合计限8 MiB")
                # bytearray.extend can reserve >4 MiB for a <=4 MiB payload.
                # Exact-sized buffers bound both the old/new copy and the
                # subsequent Qt encoded copy to 8 MiB of image storage.
                joined = bytearray(size)
                self.inspectionPeakScratchBytes = max(self.inspectionPeakScratchBytes,
                    len(buffer) + size + len(chunk.content))
                joined[:len(buffer)] = buffer
                joined[len(buffer):] = chunk.content
                buffer = joined
                del joined
                mime = str(chunk.mime_type)
            if not buffer:
                raise RuntimeClientError("E_INSPECTION_READ", "资产为空或读取失败")
            complete = True
            return buffer, mime
        finally:
            # The same deadline/cancellation also guards decode. A successful
            # stream must retire its call without cancelling the whole choice.
            if not complete:
                cancellation.cancel()
            cancel = getattr(stream, "cancel", None)
            if callable(cancel):
                cancel()
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def downloadPreviewAsset(
        self,
        assetId: str,
        projectId: str = "",
        *, draftSessionId: str = "",
    ) -> tuple[bytes, str]:
        chunks = self._call(
            "StreamPreviewAsset",
            runtime_pb2.GetPreviewAssetRequest(
                asset_id=assetId,
                project_id=projectId,
                draft_session_id=draftSessionId,
            ),
            useDeadline=False,
        )
        content: list[bytes] = []
        mimeType = ""
        for chunk in chunks if isinstance(chunks, IterableABC) else ():
            content.append(bytes(getattr(chunk, "content", b"")))
            if not mimeType:
                mimeType = str(getattr(chunk, "mime_type", ""))
        return b"".join(content), mimeType

    def runOperatorPreview(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
        operatorId: str,
        params: dict[str, object],
        imageAssetId: str,
        requestId: str = "",
        *, projectPayload=None, jobId: str = "", draftSessionId: str = "",
    ) -> object:
        return self._call(
            "RunOperatorPreview",
            runtime_pb2.RunOperatorPreviewRequest(
                project_id=projectId,
                workflow_id=workflowId,
                node_id=nodeId,
                operator_id=operatorId,
                params_json=json.dumps(params, ensure_ascii=True),
                image_asset_id=imageAssetId,
                request_id=requestId,
                project_json=json.dumps(projectPayload, ensure_ascii=False) if projectPayload else "",
                job_id=jobId,
                draft_session_id=draftSessionId,
            ),
        )

    def openDraftPurePreviewSession(self, projectId, workflowId, nodeId, operatorId, projectPayload):
        from emo_master.apps.runtime.preview.draft import MAX_DRAFT_PREVIEW_BYTES
        method = "OpenDraftPurePreviewSession"
        unsupported = "当前 Runtime 不支持草稿本地图片预览，请更新并重启 Runtime；无需运行或保存工程"
        if not callable(getattr(self.runtimeService, method, None)):
            raise RuntimeClientError("E_PREVIEW_UNSUPPORTED", unsupported)
        request = runtime_pb2.OpenOperatorPreviewSessionRequest(project_id=projectId,
            workflow_id=workflowId, node_id=nodeId, operator_id=operatorId,
            project_json=json.dumps(projectPayload, ensure_ascii=False, allow_nan=False))
        if request.ByteSize() > MAX_DRAFT_PREVIEW_BYTES:
            raise RuntimeClientError("E_PREVIEW_CONTEXT_INVALID", "纯预览草稿请求不得超过 768 KiB")
        try:
            return self._call(method, request)
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" not in error.code:
                raise
            raise RuntimeClientError("E_PREVIEW_UNSUPPORTED", unsupported) from error

    def closeDraftPurePreviewSession(self, sessionId):
        return self._call("CloseDraftPurePreviewSession",
            runtime_pb2.CloseOperatorPreviewSessionRequest(session_id=sessionId))

    def cancelOperatorPreview(self, requestId: str) -> object:
        return self._call(
            "CancelOperatorPreview",
            runtime_pb2.CancelOperatorPreviewRequest(request_id=requestId),
        )

    def openOperatorPreviewSession(
        self,
        projectId: str,
        workflowId: str,
        nodeId: str,
        operatorId: str,
        params: dict[str, object],
        configurationOnly: bool = False,
    ) -> object:
        call = self._cameraConfigurationCall if configurationOnly else self._call
        return call(
            "OpenCameraConfigurationSession" if configurationOnly else "OpenOperatorPreviewSession",
            runtime_pb2.OpenOperatorPreviewSessionRequest(
                project_id=projectId,
                workflow_id=workflowId,
                node_id=nodeId,
                operator_id=operatorId,
                params_json=json.dumps(params, ensure_ascii=True),
                configuration_only=configurationOnly,
            ),
        )

    def openDraftOperatorPreviewSession(
        self, projectId: str, workflowId: str, nodeId: str, operatorId: str,
        params: dict[str, object], projectPayload: dict[str, object],
        *, jobId: str = "", configurationOnly: bool = False,
    ) -> object:
        from emo_master.apps.runtime.preview.draft import MAX_DRAFT_PREVIEW_BYTES
        if not callable(getattr(self.runtimeService, "OpenDraftOperatorPreviewSession", None)):
            raise RuntimeClientError(
                "E_PREVIEW_UNSUPPORTED", "当前 Runtime 不支持草稿相机预览，请更新并重启 Runtime"
            )
        request = runtime_pb2.OpenOperatorPreviewSessionRequest(
            project_id=projectId, workflow_id=workflowId, node_id=nodeId, operator_id=operatorId,
            params_json=json.dumps(params, ensure_ascii=False, allow_nan=False),
            project_json=json.dumps(projectPayload, ensure_ascii=False, allow_nan=False),
            job_id=jobId,
            configuration_only=configurationOnly,
        )
        if request.ByteSize() > MAX_DRAFT_PREVIEW_BYTES:
            raise RuntimeClientError("E_PREVIEW_CONTEXT_INVALID", "相机预览草稿请求不得超过 768 KiB")
        if configurationOnly:
            return self._cameraConfigurationCall("OpenCameraConfigurationSession", request)
        try:
            return self._call("OpenDraftOperatorPreviewSession", request)
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" not in error.code:
                raise
            raise RuntimeClientError(
                "E_PREVIEW_UNSUPPORTED", "当前 Runtime 不支持草稿相机预览，请更新并重启 Runtime"
            ) from error

    def _cameraConfigurationCall(self, methodName: str, request: object):
        if not callable(getattr(self.runtimeService, methodName, None)):
            raise RuntimeClientError("E_PREVIEW_UNSUPPORTED", "当前 Runtime 不支持相机实时调参，请更新并重启 Runtime")
        try:
            return self._call(methodName, request)
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" not in error.code:
                raise
            raise RuntimeClientError("E_PREVIEW_UNSUPPORTED", "当前 Runtime 不支持相机实时调参，请更新并重启 Runtime") from error

    def configureOperatorSession(self, sessionId: str, identity: str, requestId: str,
                                 action: str, payload: dict[str, object]) -> object:
        return self._cameraConfigurationCall("ConfigureOperatorSession", runtime_pb2.ConfigureOperatorSessionRequest(
            session_id=sessionId, device_identity=identity, request_id=requestId,
            action=action, payload_json=json.dumps(payload, ensure_ascii=True, allow_nan=False)))

    def streamOperatorPreviewFrames(self, sessionId: str) -> Iterable[object]:
        stream = self._call(
            "StreamOperatorPreviewFrames",
            runtime_pb2.StreamOperatorPreviewFramesRequest(session_id=sessionId),
            useDeadline=False,
        )
        return stream if isinstance(stream, IterableABC) else ()

    def closeOperatorPreviewSession(self, sessionId: str) -> object:
        return self._call(
            "CloseOperatorPreviewSession",
            runtime_pb2.CloseOperatorPreviewSessionRequest(session_id=sessionId),
        )

    def openPlcDebugSession(self, projectId: str, workflowId: str, nodeId: str,
                            operatorId: str, params: dict[str, object], requestId: str,
                            cancellation=None) -> object:
        request = runtime_pb2.OpenPlcDebugSessionRequest(
            project_id=projectId, workflow_id=workflowId, node_id=nodeId, operator_id=operatorId,
            params_json=json.dumps(params, allow_nan=False), request_id=requestId,
        )
        connectTimeout = params.get("connectTimeoutMs", 2000)
        if isinstance(connectTimeout, bool) or not isinstance(connectTimeout, int):
            raise ValueError("connectTimeoutMs must be an integer")
        timeout = max(self.deadlineMs, connectTimeout + 2000)
        return self._plcCall("OpenPlcDebugSession", request, min(timeout, 65000), cancellation)

    def executePlcDebugCommand(self, sessionId: str, runtimeInstanceId: str, command: str,
                               params: dict[str, object], requestId: str, cancellation=None) -> object:
        request = runtime_pb2.ExecutePlcDebugCommandRequest(
            session_id=sessionId, runtime_instance_id=runtimeInstanceId, command=command,
            params_json=json.dumps(params, allow_nan=False), request_id=requestId,
        )
        return self._plcCall("ExecutePlcDebugCommand", request,
                             125000 if command in {"read", "write"} else 5000, cancellation)

    def closePlcDebugSession(self, sessionId: str, runtimeInstanceId: str, cancellation=None) -> object:
        return self._plcCall("ClosePlcDebugSession", runtime_pb2.ClosePlcDebugSessionRequest(
            session_id=sessionId, runtime_instance_id=runtimeInstanceId), 5000, cancellation)

    def _plcCall(self, methodName: str, request, timeoutMs: int, cancellation):
        if not callable(getattr(self.runtimeService, methodName, None)):
            raise RuntimeClientError("E_PLC_UNSUPPORTED", "当前 Runtime 不支持 PLC 运行调试")
        token, finished = _PlcCallContext(), threading.Event()
        token.start(timeoutMs)

        def watch():
            while not finished.wait(0.02):
                if (cancellation is not None and cancellation.is_set()) or not token.is_active():
                    token.cancel()
                    return
        monitor = threading.Thread(target=watch, name="plc-debug-cancellation", daemon=True)
        monitor.start()
        try:
            if cancellation is not None and cancellation.is_set():
                token.cancel()
            preserve = None
            if methodName == "ExecutePlcDebugCommand" and request.command == "write":
                def completedWrite(reply):
                    try:
                        result = json.loads(str(getattr(reply, "result_json", "{}")))
                        return result.get("receipt", {}).get("outcome") in {"confirmed", "rejected", "unknown"}
                    except (ValueError, AttributeError):
                        return False
                preserve = completedWrite
            return self._displayCall(methodName, request, timeoutMs, token, "plc-debug", preserveCompleted=preserve)
        except (RuntimeClientError, NotImplementedError) as error:
            code = getattr(error, "code", "")
            if "UNIMPLEMENTED" in code or isinstance(error, NotImplementedError):
                raise RuntimeClientError("E_PLC_UNSUPPORTED", "当前 Runtime 不支持 PLC 运行调试") from error
            if methodName == "ExecutePlcDebugCommand" and request.command == "write":
                if not token.dispatched:
                    raise RuntimeClientError("E_PLC_CANCELLED", "写入请求已取消，未下发至 Runtime") from error
                raise RuntimeClientError("E_PLC_WRITE_UNKNOWN", "写入结果未知，未自动重试；" + str(error)) from error
            raise
        finally:
            finished.set()
            monitor.join(timeout=0.2)

    def operatorDebugCall(self, methodName: str, request) -> object:
        methods = {"GetOperatorDebugCapabilities", "OpenOperatorDebugSession", "GetOperatorDebugSession",
                   "PrepareOperatorDebugInputs", "ExecuteOperatorDebugNode", "GetOperatorDebugExecution",
                   "ReadOperatorDebugEvents", "CancelOperatorDebugExecution", "ResetOperatorDebugSession",
                   "RenewOperatorDebugSession", "CloseOperatorDebugSession", "WriteOperatorDebugAsset", "ReadOperatorDebugAsset",
                   "ListOperatorDebugSources", "ImportOperatorDebugSource", "CopyOperatorDebugVariables"}
        methods.update({"GetWorkflowDebugCapabilities", "OpenWorkflowDebugSession", "GetWorkflowDebugSession",
                        "PrepareWorkflowDebugInputs", "StartWorkflowDebug", "ControlWorkflowDebug", "GetWorkflowDebugCommand",
                        "GetWorkflowDebugSnapshot", "ReadWorkflowDebugEvents", "WriteWorkflowDebugAsset", "ReadWorkflowDebugAsset",
                        "RenewWorkflowDebugSession", "CloseWorkflowDebugSession"})
        if methodName not in methods or not callable(getattr(self.runtimeService, methodName, None)):
            raise RuntimeClientError("E_DEBUG_UNSUPPORTED", "Runtime does not support operator debugging; update Runtime")
        try:
            return self._call(methodName, request)
        except (RuntimeClientError, NotImplementedError) as error:
            if isinstance(error, NotImplementedError) or "UNIMPLEMENTED" in getattr(error, "code", ""):
                raise RuntimeClientError("E_DEBUG_UNSUPPORTED", "Runtime does not support operator debugging; update Runtime") from error
            # No retry or local execution fallback. The caller queries its original request ID.
            raise

    def listRejectedOperators(self) -> list[object]:
        reply = self._call("ListRejectedOperators", runtime_pb2.ListRejectedOperatorsRequest())
        return list(getattr(reply, "rejected", []))

    def listWorkflows(self, projectId: str = "") -> list[WorkflowInfo]:
        reply = self._call(
            "ListWorkflows", runtime_pb2.ListWorkflowsRequest(project_id=projectId)
        )
        result: list[WorkflowInfo] = []
        for workflow in getattr(reply, "workflows", []):
            result.append(
                WorkflowInfo(
                    workflowId=str(getattr(workflow, "workflow_id", "")),
                    name=str(getattr(workflow, "name", "")),
                    isEntry=bool(getattr(workflow, "is_entry", False)),
                    inputs=self._parseSchema(getattr(workflow, "inputs_json", "{}")),
                    outputs=self._parseSchema(getattr(workflow, "outputs_json", "{}")),
                )
            )
        return result

    def _variableCall(self, operation, projectId, variableId="", jobId="", value=None, revision=None):
        request = runtime_pb2.GlobalVariablesRequest(project_id=projectId, variable_id=variableId, job_id=jobId)
        if operation == "SetGlobalVariable":
            request.value_json = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if revision is not None:
            request.expected_revision = revision
        reply = self._call(operation, request)
        if not reply.ok:
            raise RuntimeClientError(reply.code, reply.message)
        return json.loads(reply.variables_json)

    def dispatchQueueState(self, projectId, runGroupId="", targetSlotId="", includeItems=False):
        try:
            reply = self._call("GetDispatchQueueState", runtime_pb2.DispatchQueueRequest(
                project_id=projectId, run_group_id=runGroupId, target_slot_id=targetSlotId, include_items=includeItems))
        except AttributeError as error:
            raise RuntimeClientError("E_DISPATCH_MONITOR_UNSUPPORTED", "当前 Runtime 不支持队列监控") from error
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" in error.code:
                raise RuntimeClientError("E_DISPATCH_MONITOR_UNSUPPORTED", "当前 Runtime 不支持队列监控") from error
            raise
        if not reply.ok:
            raise RuntimeClientError(reply.code, reply.message)
        return json.loads(reply.state_json)

    def listGlobalVariables(self, projectId, jobId=""):
        return self._variableCall("ListGlobalVariables", projectId, jobId=jobId)

    def globalVariableState(self, projectId, jobId=""):
        reply = self._call("ListGlobalVariables", runtime_pb2.GlobalVariablesRequest(project_id=projectId, job_id=jobId))
        if not reply.ok:
            raise RuntimeClientError(reply.code, reply.message)
        return {"variables": json.loads(reply.variables_json), "jobs": json.loads(reply.jobs_json or "[]")}

    def getGlobalVariable(self, projectId, variableId, jobId=""):
        return self._variableCall("GetGlobalVariable", projectId, variableId, jobId)[0]

    def setGlobalVariable(self, projectId, variableId, value, revision, jobId=""):
        return self._variableCall("SetGlobalVariable", projectId, variableId, jobId, value, revision)[0]

    def resetGlobalVariable(self, projectId, variableId, revision, jobId=""):
        return self._variableCall("ResetGlobalVariable", projectId, variableId, jobId, revision=revision)[0]

    def listGlobalCounters(self, projectId: str) -> list[GlobalCounterInfo]:
        reply = self._call(
            "ListGlobalCounters",
            runtime_pb2.ListGlobalCountersRequest(project_id=projectId),
        )
        self._raiseGlobalCounterReply(reply)
        return [
            self._toGlobalCounterInfo(counter)
            for counter in getattr(reply, "counters", [])
        ]

    def getGlobalCounter(self, projectId: str, name: str) -> GlobalCounterInfo:
        reply = self._call(
            "GetGlobalCounter",
            runtime_pb2.GetGlobalCounterRequest(project_id=projectId, name=name),
        )
        self._raiseGlobalCounterReply(reply)
        return self._toGlobalCounterInfo(getattr(reply, "counter", None))

    def setGlobalCounter(
        self,
        projectId: str,
        name: str,
        value: int,
    ) -> GlobalCounterInfo:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or value > MAX_GLOBAL_COUNTER_VALUE
        ):
            raise RuntimeClientError(
                "E_COUNTER_VALUE_RANGE",
                f"counter value must be an integer between 0 and {MAX_GLOBAL_COUNTER_VALUE}",
            )
        reply = self._call(
            "SetGlobalCounter",
            runtime_pb2.SetGlobalCounterRequest(
                project_id=projectId,
                name=name,
                value=value,
            ),
        )
        self._raiseGlobalCounterReply(reply)
        return self._toGlobalCounterInfo(getattr(reply, "counter", None))

    def resetGlobalCounter(self, projectId: str, name: str) -> GlobalCounterInfo:
        reply = self._call(
            "ResetGlobalCounter",
            runtime_pb2.ResetGlobalCounterRequest(project_id=projectId, name=name),
        )
        self._raiseGlobalCounterReply(reply)
        return self._toGlobalCounterInfo(getattr(reply, "counter", None))

    def loadProject(self, projectPath: str) -> object:
        return self._call(
            "LoadProject", runtime_pb2.LoadProjectRequest(project_path=projectPath)
        )

    def validateProject(self, projectId: str) -> object:
        return self._call(
            "ValidateProject", runtime_pb2.ValidateProjectRequest(project_id=projectId)
        )

    def displayAddress(self) -> str:
        """Return an existing endpoint; observing must never provision a Runtime."""
        return self._runtimeTarget

    def _callPresentation(self, methodName: str, request):
        if self._displayService is None:
            raise RuntimeClientError("UNIMPLEMENTED", "页面结果服务未启用；请在运行面板确认流程状态")
        method = getattr(self._displayService, methodName)
        try:
            return method(request, timeout=self.deadlineMs / 1000.0)
        except Exception as err:
            if grpc is not None and isinstance(err, grpc.RpcError):
                raise RuntimeClientError(str(err.code()), err.details() or "display RPC failed") from err
            raise

    def getDisplayCapabilities(self):
        return self._callPresentation("Capabilities", runtime_pb2.DisplayEmpty())

    def listDisplayJobs(self, projectId: str):
        reply = self._callPresentation("ListJobs", runtime_pb2.DisplayEmpty(project_id=projectId))
        # Do not trust an older endpoint to honor the additive filter field.
        return [job for job in reply.jobs if getattr(job, "project_id", "") == projectId]

    def prepareStart(self, capturePresentation: bool = False, *, captureRequirements: dict | None = None,
                     legacySnapshotPolicy: str = "ALL") -> str:
        """Negotiate only on explicit Run; return a generation for start lookup."""
        policy = normalizeLegacySnapshotPolicy(legacySnapshotPolicy)
        with self._startLock:
            if self._closed or self._closing:
                raise RuntimeClientError("E_RUNTIME_CLOSED", "Runtime client is closed")
            from emo_master.apps.runtime.grpc_server.service import RuntimeService
            if self._displayService is None and isinstance(self.runtimeService, RuntimeService):
                if not capturePresentation:
                    if policy == "NONE" and not getattr(self.runtimeService, "supportsLegacySnapshotPolicy", False):
                        raise RuntimeClientError("E_SNAPSHOT_POLICY_UNSUPPORTED", "当前 Runtime 不支持 NONE 节点快照策略；未创建任务")
                    # Keep no-page embedded runs on their existing execution path,
                    # including their resource cost. Lookup is already provided by
                    # this owned in-process Runtime; no display host is needed.
                    return self.runtimeService.runtimeInstanceId
                from emo_master.apps.runtime.presentation.service import PresentationService
                from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
                from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import DisplayServiceStub
                with self.runtimeService._previewJobLock:
                    presentation = getattr(self.runtimeService, "_presentationOwner", None)
                    if presentation is None:
                        presentation = PresentationService(self.runtimeService,
                            self.runtimeService.workspaceRoot.parent / "presentation")
                        self._presentationService = presentation
                # Reuse the only execution/device owner, never create another Runtime.
                self._presentationServer = AioRuntimeServer(self.runtimeService, presentation)
                self._runtimeTarget = f"127.0.0.1:{self._presentationServer.port}"
                self._displayChannel = grpc.insecure_channel(self._runtimeTarget)
                self._displayService = DisplayServiceStub(self._displayChannel)
            try:
                capabilities = self.getDisplayCapabilities()
            except RuntimeClientError as error:
                if not capturePresentation and policy == "ALL" and "UNIMPLEMENTED" in error.code:
                    return ""  # Older external Runtime retains its original no-page path.
                if policy == "NONE" and "UNIMPLEMENTED" in error.code:
                    raise RuntimeClientError("E_SNAPSHOT_POLICY_UNSUPPORTED", "当前 Runtime 不支持 NONE 节点快照策略；未创建任务") from error
                raise
            supported = set(capabilities.capabilities)
            if policy == "NONE" and not {"legacy_snapshot_policy_v1", "preview_snapshot_origin_v1"}.issubset(supported):
                raise RuntimeClientError("E_SNAPSHOT_POLICY_UNSUPPORTED", "当前 Runtime 不支持 NONE 节点快照策略及快照来源标识；未创建任务")
            if capturePresentation and "normal_start_capture" not in supported:
                raise RuntimeClientError("E_CAPTURE_UNSUPPORTED", "当前 Runtime 不支持正常运行时的页面采集")
            if capturePresentation and captureRequirements:
                images = len(set(captureRequirements.get("imageLaneBySource", {}).values()))
                scopes = captureRequirements.get("scopeCount", 1)
                required = ({"normal_two_image_lanes"} if images > 1 else set())
                if scopes > 1:
                    required.add("normal_multi_scope")
                if required:
                    from emo_master.core.presentation.capture_limits import normalCaptureProfile
                    try:
                        profile = json.loads(getattr(capabilities, "normal_capture_limits_json", ""))
                    except (TypeError, ValueError):
                        profile = None
                    if not required.issubset(supported) or profile != normalCaptureProfile():
                        raise RuntimeClientError("E_CAPTURE_PROFILE_UNSUPPORTED",
                            "当前 Runtime 未声明此多图/多作用域采集额度；未创建任务")
            if "start_request_lookup" not in supported:
                if capturePresentation or policy == "NONE":
                    raise RuntimeClientError("E_START_LOOKUP_UNSUPPORTED", "当前 Runtime 不支持启动请求核实")
                return ""
            generation = str(capabilities.runtime_instance_id)
            if not generation:
                raise RuntimeClientError("E_RUNTIME_IDENTITY", "Runtime 未提供实例标识")
            return generation

    def getStartRequest(self, startRequestId: str, runtimeInstanceId: str):
        return self._call("GetStartRequest", runtime_pb2.StartRequestLookup(
            start_request_id=startRequestId, runtime_instance_id=runtimeInstanceId))

    def releaseDisplayJob(self, jobId: str, runtimeInstanceId: str):
        status = self.getJobStatus(jobId)
        if not getattr(status, "ok", False) or getattr(status, "status", "") not in {"COMPLETED", "FAILED", "ABORTED"}:
            raise RuntimeClientError("E_JOB_NOT_TERMINAL", "上次任务尚未确认结束，不能释放或再次启动")
        return self._callPresentation("ReleaseJob", runtime_pb2.DisplayRequest(
            job_id=jobId, runtime_instance_id=runtimeInstanceId))

    def startJob(
        self,
        projectId: str,
        workflowId: str = "",
        inputs: dict[str, object] | str | None = None,
        *,
        capturePresentation: bool = False,
        legacySnapshotPolicy: str = "ALL",
        startRequestId: str = "",
        expectedRuntimeInstanceId: str = "",
        inspectionSessionId: str = "",
        expectedDocumentRevision: int = 0,
    ) -> object:
        policy = normalizeLegacySnapshotPolicy(legacySnapshotPolicy)
        if policy == "NONE":
            # The caller must retain the identity before a lost reply. Never
            # manufacture a hidden token that it cannot later reconcile.
            if not startRequestId or not expectedRuntimeInstanceId:
                raise RuntimeClientError("E_START_IDENTITY_REQUIRED",
                    "NONE 启动需要明确的启动请求与 Runtime 实例标识；未创建任务")
            # Unsupported peers must fail before StartJob can silently ignore
            # this additive protobuf field. A changed instance is not a retry.
            generation = self.prepareStart(capturePresentation, legacySnapshotPolicy=policy)
            if generation != expectedRuntimeInstanceId:
                raise RuntimeClientError("E_RUNTIME_IDENTITY",
                    "Runtime 实例已变化，请核实原启动请求；未创建任务")
        if isinstance(inputs, str):
            inputsJson = inputs
        else:
            inputsJson = json.dumps(inputs or {}, ensure_ascii=True)
        request = runtime_pb2.StartJobRequest(
            project_id=projectId, workflow_id=workflowId, inputs_json=inputsJson,
            capture_presentation=capturePresentation, start_request_id=startRequestId,
            expected_runtime_instance_id=expectedRuntimeInstanceId,
            legacy_snapshot_policy=policy,
            inspection_session_id=inspectionSessionId,
            expected_document_revision=expectedDocumentRevision,
        )
        return self._call("StartJob", request)

    def stopJob(self, jobId: str, mode: str = "graceful") -> object:
        request = runtime_pb2.StopJobRequest(job_id=jobId, mode=mode)
        return self._call("StopJob", request)

    def getJobStatus(self, jobId: str) -> object:
        return self._call("GetJobStatus", runtime_pb2.GetJobStatusRequest(job_id=jobId))

    def streamJobEvents(
        self, jobId: str, afterSequence: int = 0, follow: bool = False
    ) -> Iterable[RuntimeEventDTO]:
        stream = self.iterJobEvents(jobId, afterSequence, follow)
        return stream if follow else list(stream)

    def iterJobEvents(
        self, jobId: str, afterSequence: int = 0, follow: bool = False
    ) -> Iterable[RuntimeEventDTO]:
        """Yield event DTOs without materializing a live subscription."""
        request = runtime_pb2.StreamJobEventsRequest(
            job_id=jobId, after_sequence=afterSequence, follow=follow
        )
        context = None
        method = self.runtimeService.StreamJobEvents
        if follow and "context" in _signatureParameters(method):
            # Embedded RuntimeService follows the same cancellation contract as
            # gRPC, but the generator must be closed by its consuming thread.
            context = DisplayCallContext()
            events = method(request, context)
        else:
            events = self._call("StreamJobEvents", request, useDeadline=False)
        if not isinstance(events, IterableABC):
            return iter(())
        if not follow:
            return (self._toEventDTO(event) for event in events)
        with self._streamLock:
            previous = self._activeStreams.pop(jobId, None)
            if previous is not None:
                self._retiringStreams.add(previous)
                previous.cancel()
            stream = RuntimeEventStream(
                events,
                self._toEventDTO,
                lambda value: self._removeActiveStream(jobId, value),
                context,
            )
            self._activeStreams[jobId] = stream
        return stream

    def cancelEventStream(self, jobId: str) -> bool:
        with self._streamLock:
            stream = self._activeStreams.get(jobId)
        if stream is None:
            return False
        stream.cancel()
        return True

    def close(self) -> None:
        # Keep failed owners reachable for an explicit cleanup retry. A timeout
        # is not evidence that a thread/process relinquished its resources.
        with self._closeLock:
            if self._closed:
                return
            self._closing = True
            with self._streamLock:
                streams = set(self._activeStreams.values()) | self._retiringStreams
            for stream in streams:
                stream.cancel()
            for stream in streams:
                if not stream.waitClosed(max(0.1, self.deadlineMs / 1000.0)):
                    raise TimeoutError("runtime event consumer still owns its iterator")
            with self._displayLock:
                displayOwners = list(self._displayCalls)
            for owner in displayOwners:
                self.closeDisplayOwner(owner)
            unsubscribe = getattr(self._ownedChannel, "unsubscribe", None)
            if callable(unsubscribe):
                unsubscribe(self._onChannelState)
            for field in ("_presentationServer", "_displayChannel", "_ownedRuntimeService", "_ownedChannel"):
                resource = getattr(self, field)
                self._closeOwned(resource)
                setattr(self, field, None)
            for previous in self._retiredTransports:
                previous.close()
            self._retiredTransports.clear()
            # PresentationService is owned by RuntimeService, including when a
            # debug viewer borrows it. This adapter never disposes a borrowed owner.
            self._closed = True
            self._closing = False

    def _closeOwned(self, resource: object | None) -> None:
        close = getattr(resource, "close", None)
        if callable(close):
            close()

    def _removeActiveStream(self, jobId: str, stream: RuntimeEventStream) -> None:
        with self._streamLock:
            self._retiringStreams.discard(stream)
            if self._activeStreams.get(jobId) is stream:
                self._activeStreams.pop(jobId, None)

    def _call(self, methodName: str, request: object, useDeadline: bool = True):
        with self._transportLock:
            method = getattr(self.runtimeService, methodName)
            self._rpcCalls += 1
        try:
            if useDeadline and self.deadlineMs > 0:
                parameters = _signatureParameters(method)
                if not parameters or "timeout" in parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                ):
                    return method(request, timeout=self.deadlineMs / 1000.0)
            return method(request, None)
        except Exception as err:
            if grpc is not None and isinstance(err, grpc.RpcError):
                code = err.code()
                detail = err.details() or "runtime RPC failed"
                raise RuntimeClientError(str(code), detail) from err
            raise
        finally:
            with self._transportLock:
                self._rpcCalls -= 1

    def communicationState(self, projectId="", afterSequence=0, *, projectPath="", cancellation=None):
        request = runtime_pb2.CommunicationRequest(protocol_version="1.1", project_id=projectId,
                                                   after_sequence=afterSequence, project_path=projectPath)
        return self._communicationCall("GetCommunicationState", request, cancellation)

    def releaseCommunicationForReplacement(self, projectPath, projectId):
        """Draft opening bypasses LoadProject; it must still retire the old listener."""
        import os
        from pathlib import Path
        try:
            state = self.communicationState()
        except RuntimeClientError as error:
            if error.code == "E_TCP_UNSUPPORTED":
                return
            raise
        if not state.get("projectId"):
            return
        key = os.path.normcase(str(Path(projectPath).resolve()))
        if state["projectId"] == projectId and state.get("projectPath") == key:
            return
        self.controlCommunication("UNLOAD", state)

    def controlCommunication(self, action, state, *, config=None, requestId=None, saveToken="", cancellation=None):
        request = runtime_pb2.CommunicationRequest(protocol_version="1.1", project_id=state["projectId"], project_path=state["projectPath"], save_token=saveToken,
            runtime_instance_id=state["runtimeInstanceId"], project_load_generation=state["projectLoadGeneration"],
            expected_config_revision=state["desiredRevision"], request_id=requestId or uuid4().hex,
            action=action, config_json=json.dumps(config, ensure_ascii=True) if config is not None else "")
        return self._communicationCall("ControlCommunication", request, cancellation)

    def _communicationCall(self, method, request, cancellation):
        if not callable(getattr(self.runtimeService, method, None)):
            raise RuntimeClientError("E_TCP_UNSUPPORTED", "此 Runtime 不支持项目 TCP 通信服务，请更新 Runtime")
        try:
            reply = self._displayCall(method, request, 5000, cancellation, "project-communication")
        except RuntimeClientError as error:
            if "UNIMPLEMENTED" in error.code:
                raise RuntimeClientError("E_TCP_UNSUPPORTED", "此 Runtime 不支持项目 TCP 通信服务，请更新 Runtime") from error
            raise
        if not reply.ok:
            code = "E_TCP_PROTOCOL_MISMATCH" if reply.code == "E_TCP_UNSUPPORTED" else reply.code
            raise RuntimeClientError(code, reply.message)
        value = json.loads(reply.state_json)
        if not isinstance(value, dict) or value.get("protocolVersion") != "1.1":
            raise RuntimeClientError("E_TCP_PROTOCOL", "无效通信状态，请更新 Runtime")
        return value

    def _toEventDTO(self, event: object) -> RuntimeEventDTO:
        payloadJson = getattr(event, "payload_json", "{}")
        payload: dict[str, object] = {}
        if isinstance(payloadJson, str) and payloadJson.strip():
            try:
                parsedPayload = json.loads(payloadJson)
                if isinstance(parsedPayload, dict):
                    payload = parsedPayload
            except json.JSONDecodeError:
                payload = {}
        iterationPath = self._parseIterationPath(
            getattr(event, "iteration_path_json", "[]")
        )
        return RuntimeEventDTO(
            jobId=str(getattr(event, "job_id", "")),
            eventType=str(getattr(event, "event_type", "")),
            message=str(getattr(event, "message", "")),
            level=str(getattr(event, "level", "INFO")),
            nodeId=str(getattr(event, "node_id", "")),
            code=str(getattr(event, "code", "")),
            payload=payload,
            sequence=int(getattr(event, "sequence", 0)),
            timestampMs=int(getattr(event, "timestamp_ms", 0)),
            projectId=str(getattr(event, "project_id", "")),
            workflowId=str(getattr(event, "workflow_id", "")),
            workflowRunId=str(getattr(event, "workflow_run_id", "")),
            parentWorkflowRunId=str(getattr(event, "parent_workflow_run_id", "")),
            nodeRunId=str(getattr(event, "node_run_id", "")),
            iterationPath=iterationPath,
        )

    def _raiseGlobalCounterReply(self, reply: object) -> None:
        if bool(getattr(reply, "ok", False)):
            return
        raise RuntimeClientError(
            str(getattr(reply, "code", "E_RUNTIME_STATE_UNAVAILABLE")),
            str(getattr(reply, "message", "global counter request failed")),
        )

    def _toGlobalCounterInfo(self, counter: object) -> GlobalCounterInfo:
        return GlobalCounterInfo(
            name=str(getattr(counter, "name", "")),
            value=int(getattr(counter, "value", 0)),
            updatedAtMs=int(getattr(counter, "updated_at_ms", 0)),
        )

    def _toStrMap(self, rawValue: object) -> dict[str, str]:
        mapping = self._toDict(rawValue)
        if mapping is None:
            return {}
        return {
            key: value
            for key, value in mapping.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _toDict(self, rawValue: object) -> dict[object, object] | None:
        if isinstance(rawValue, dict):
            return rawValue
        itemsMethod = getattr(rawValue, "items", None)
        if not callable(itemsMethod):
            return None
        try:
            items = itemsMethod()
            if not isinstance(items, IterableABC):
                return None
            converted: dict[object, object] = {}
            for item in cast(Iterable[object], items):
                if not isinstance(item, tuple) or len(item) != 2:
                    return None
                converted[item[0]] = item[1]
            return converted
        except Exception:
            return None

    def _parseSchema(self, rawValue: object) -> dict[str, object]:
        if not isinstance(rawValue, str) or rawValue.strip() == "":
            return {}
        try:
            parsed = json.loads(rawValue)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _parseJsonList(self, rawValue: object) -> list[object]:
        if not isinstance(rawValue, str) or not rawValue.strip():
            return []
        try:
            parsed = json.loads(rawValue)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _parsePortSpecs(
        self,
        rawValue: object,
        fallback: dict[str, str],
    ) -> dict[str, object]:
        parsed = self._parseSchema(rawValue)
        if not parsed:
            return dict(fallback)
        result: dict[str, object] = {}
        for name, spec in parsed.items():
            try:
                result[name] = validatePortSpec(spec, f"ports.{name}")
            except PortSpecValidationError:
                return dict(fallback)
        return result

    def _parseIterationPath(self, rawValue: object) -> tuple[int, ...]:
        if not isinstance(rawValue, str) or not rawValue.strip():
            return ()
        try:
            parsed = json.loads(rawValue)
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, list):
            return ()
        return tuple(item for item in parsed if isinstance(item, int) and not isinstance(item, bool))


def _signatureParameters(method) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(method).parameters)
    except (TypeError, ValueError):
        return {}
