from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EditorKey:
    projectId: str
    workflowId: str
    nodeId: str


class EditorContextError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@runtime_checkable
class OperatorEditorController(Protocol):
    def bind(self, rootWidget: object, context: "EditorContext") -> None: ...

    def loadParams(self, params: dict[str, object]) -> None: ...

    def collectParams(self) -> dict[str, object]: ...

    def validate(self) -> object: ...

    def onOpen(self) -> None: ...

    def onClose(self) -> None: ...

    def dispose(self) -> None: ...


class EditorContext:
    """Restricted bridge exposed to editor controllers.

    Controllers can apply node parameters and use the explicit preview RPCs, but
    they never receive the Designer MainWindow or its mutable stores.
    """

    def __init__(
        self,
        *,
        key: EditorKey,
        operatorId: str,
        version: str,
        previewMode: str,
        paramSchema: dict[str, object],
        runtimeClient: object,
        applyParams: Callable[[EditorKey, dict[str, object]], bool],
        appendLog: Callable[[str, str], None],
        workflowOptions: list[str] | None = None,
        getCurrentJobId: Callable[[], str | None] | None = None,
        getSqliteDraft: Callable[[EditorKey], dict] | None = None,
        getPreviewProject: Callable[[EditorKey], dict[str, object]] | None = None,
        variableDefinitions=None,
        variableBindings=None,
        applyConfiguration=None,
    ) -> None:
        self.key = key
        self.operatorId = operatorId
        self.version = version
        self.previewMode = previewMode
        self.paramSchema = dict(paramSchema)
        self.workflowOptions = list(workflowOptions or [])
        self._runtimeClient = runtimeClient
        self._getCurrentJobId = getCurrentJobId or (lambda: None)
        self._getSqliteDraft = getSqliteDraft
        self._getPreviewProject = getPreviewProject
        self._invalidatePreviewSources: Callable[[], None] = lambda: None
        self._applyParams = applyParams
        self.variableDefinitions = variableDefinitions
        self.variableBindings = list(variableBindings or [])
        self.applyConfiguration = applyConfiguration
        self.collectVariableBindings = lambda: self.variableBindings
        self._appendLog = appendLog
        self._markDirty: Callable[[], None] = lambda: None
        self._setStatus: Callable[[str], None] = lambda _message: None
        self._setError: Callable[[str], None] = lambda _message: None

    def bindWindowHooks(
        self,
        markDirty: Callable[[], None],
        setStatus: Callable[[str], None],
        setError: Callable[[str], None],
    ) -> None:
        self._markDirty = markDirty
        self._setStatus = setStatus
        self._setError = setError

    def applyParams(self, params: dict[str, object]) -> bool:
        return bool(self._applyParams(self.key, dict(params)))

    def markDirty(self) -> None:
        self._markDirty()

    def setStatus(self, message: str) -> None:
        self._setStatus(str(message))

    def setError(self, message: str) -> None:
        self._setError(str(message))

    def log(self, level: str, message: str) -> None:
        self._appendLog(str(level), str(message))

    def currentJobId(self) -> str:
        return str(self._getCurrentJobId() or "")

    def sqliteDraft(self):
        from copy import deepcopy
        if self._getSqliteDraft is None:
            raise EditorContextError('E_SQLITE_DRAFT', '当前 Designer 没有提供正式来源目录')
        return deepcopy(self._getSqliteDraft(self.key))

    def sqliteTarget(self, action, path, directory, table='', columns=None, **kwargs):
        method = getattr(self._runtimeClient, 'sqliteTarget', None)
        if not callable(method):
            raise EditorContextError('E_SQLITE_UNSUPPORTED', '旧 Runtime 不支持 SQLite 检查和初始化')
        return method(action, path, directory, table, columns, **kwargs)

    def sqliteLocation(self):
        address = str(getattr(self._runtimeClient, '_runtimeTarget', ''))
        local = not address or address.startswith(('127.0.0.1:', 'localhost:', '[::1]:'))
        return ('本机内嵌 Runtime' if not address else address), local

    def bindPreviewInvalidation(self, callback: Callable[[], None]) -> None:
        self._invalidatePreviewSources = callback

    def invalidatePreviewSources(self) -> None:
        self._invalidatePreviewSources()

    def listPreviewSourcesWithMetadata(self):
        from emo_master.apps.designer.services.runtime_client import PreviewSourceListing
        method = getattr(self._runtimeClient, "listNodePreviewSourcesWithMetadata", None)
        if callable(method):
            return method(self.key.projectId, self.key.workflowId, self.key.nodeId,
                          jobId=self.currentJobId())
        # The legacy path has no trusted Run identity, even if sourceKind says
        # `current` (which only means this node rather than an upstream node).
        method = self._runtimeMethod("listNodePreviewSources")
        result = method(self.key.projectId, self.key.workflowId, self.key.nodeId)
        sources = tuple(result) if isinstance(result, Iterable) else ()
        return PreviewSourceListing(sources=sources)

    def listPreviewSources(self) -> list[object]:
        return list(self.listPreviewSourcesWithMetadata().sources)

    def uploadPreviewImage(self, data: bytes, filename: str = "") -> str:
        reply = self._runtimeMethod("uploadPreviewImage")(
            data,
            filename,
            self.key.projectId,
        )
        self._requireOk(reply, "E_PREVIEW_ASSET_INVALID")
        assetId = str(getattr(reply, "asset_id", ""))
        if not assetId:
            raise EditorContextError(
                "E_PREVIEW_ASSET_INVALID", "Runtime did not return a preview asset"
            )
        return assetId

    def downloadPreviewAsset(self, assetId: str) -> tuple[bytes, str]:
        result = self._runtimeMethod("downloadPreviewAsset")(
            assetId,
            self.key.projectId,
        )
        if not isinstance(result, tuple) or len(result) != 2:
            raise EditorContextError(
                "E_PREVIEW_ASSET_INVALID", "Runtime returned an invalid preview asset"
            )
        return bytes(result[0]), str(result[1])

    def runPurePreview(
        self,
        params: dict[str, object],
        imageAssetId: str,
        requestId: str = "",
    ) -> object:
        if self.previewMode != "pure":
            raise EditorContextError(
                "E_PREVIEW_UNSUPPORTED", "operator does not allow pure preview"
            )
        options = {}
        if self.variableDefinitions is not None and self._getPreviewProject is not None:
            options = {"projectPayload": self._variablePreviewProject(), "jobId": self.currentJobId()}
        return self._runtimeMethod("runOperatorPreview")(
            self.key.projectId,
            self.key.workflowId,
            self.key.nodeId,
            self.operatorId,
            dict(params),
            imageAssetId,
            requestId,
            **options,
        )

    def _variablePreviewProject(self):
        from copy import deepcopy
        payload = deepcopy(self._getPreviewProject(self.key))
        node = next(item for item in payload["workflows"][self.key.workflowId]["nodes"] if item["nodeId"] == self.key.nodeId)
        bindings = self.collectVariableBindings()
        if bindings:
            node["globalVariableBindings"] = bindings
        else:
            node.pop("globalVariableBindings", None)
        return payload

    def cancelPurePreview(self, requestId: str) -> None:
        if not requestId:
            return
        reply = self._runtimeMethod("cancelOperatorPreview")(requestId)
        self._requireOk(reply, "E_CANCELLED")

    def openLivePreview(self, params: dict[str, object]) -> str:
        if self.previewMode != "live":
            raise EditorContextError(
                "E_PREVIEW_UNSUPPORTED", "operator does not allow live preview"
            )
        if self._getPreviewProject is not None:
            method = getattr(self._runtimeClient, "openDraftOperatorPreviewSession", None)
            if not callable(method):
                raise EditorContextError(
                    "E_PREVIEW_UNSUPPORTED", "当前 Runtime 不支持草稿相机预览，请更新并重启 Runtime"
                )
            projectPayload = self._variablePreviewProject() if self.variableDefinitions is not None else self._getPreviewProject(self.key)
            reply = method(self.key.projectId, self.key.workflowId, self.key.nodeId,
                           self.operatorId, dict(params), projectPayload,
                           **({"jobId": self.currentJobId()} if self.variableDefinitions is not None else {}))
        else:
            reply = self._runtimeMethod("openOperatorPreviewSession")(
                self.key.projectId, self.key.workflowId, self.key.nodeId,
                self.operatorId, dict(params),
            )
        self._requireOk(reply, "E_PREVIEW_SESSION_OPEN_FAILED")
        sessionId = str(getattr(reply, "session_id", ""))
        if not sessionId:
            raise EditorContextError(
                "E_PREVIEW_SESSION_OPEN_FAILED", "Runtime did not return a session"
            )
        return sessionId

    def streamLivePreview(self, sessionId: str) -> Iterable[object]:
        return self._runtimeMethod("streamOperatorPreviewFrames")(sessionId)

    def closeLivePreview(self, sessionId: str) -> None:
        reply = self._runtimeMethod("closeOperatorPreviewSession")(sessionId)
        self._requireOk(reply, "E_PREVIEW_RELEASE_FAILED")

    def plcDebugLocation(self) -> str:
        return str(getattr(self._runtimeClient, "_runtimeTarget", "")) or "本机内嵌 Runtime"

    def openPlcDebugSession(self, params: dict[str, object], requestId: str, cancellation=None) -> object:
        return self._plcDebugMethod("openPlcDebugSession")(
            self.key.projectId, self.key.workflowId, self.key.nodeId, self.operatorId,
            dict(params), requestId, cancellation=cancellation)

    def executePlcDebugCommand(self, sessionId: str, runtimeInstanceId: str, command: str,
                               params: dict[str, object], requestId: str, cancellation=None) -> object:
        return self._plcDebugMethod("executePlcDebugCommand")(
            sessionId, runtimeInstanceId, command, dict(params), requestId, cancellation=cancellation)

    def closePlcDebugSession(self, sessionId: str, runtimeInstanceId: str, cancellation=None) -> object:
        return self._plcDebugMethod("closePlcDebugSession")(
            sessionId, runtimeInstanceId, cancellation=cancellation)

    def _plcDebugMethod(self, name: str):
        method = getattr(self._runtimeClient, name, None)
        if self.operatorId not in {"communication.plc.slmp_read", "communication.plc.slmp_write"} or not callable(method):
            raise EditorContextError("E_PLC_UNSUPPORTED", "当前 Runtime 不支持 PLC 运行调试")
        return method

    def _runtimeMethod(self, name: str):
        method = getattr(self._runtimeClient, name, None)
        if not callable(method):
            raise EditorContextError(
                "E_PREVIEW_UNSUPPORTED",
                "connected Runtime does not support operator editor previews",
            )
        return method

    @staticmethod
    def _requireOk(reply: object, fallbackCode: str) -> None:
        if bool(getattr(reply, "ok", False)):
            return
        code = str(getattr(reply, "code", "")) or fallbackCode
        message = str(getattr(reply, "message", "")) or "Runtime request failed"
        raise EditorContextError(code, message)


def loadController(controllerEntry: str) -> OperatorEditorController:
    if ":" not in controllerEntry:
        raise ValueError("controllerEntry must use module:Class syntax")
    moduleName, className = controllerEntry.split(":", 1)
    if not moduleName or not className:
        raise ValueError("controllerEntry must use module:Class syntax")
    module = importlib.import_module(moduleName)
    controllerClass = getattr(module, className)
    controller = controllerClass()
    required = (
        "bind",
        "loadParams",
        "collectParams",
        "validate",
        "onOpen",
        "onClose",
        "dispose",
    )
    missing = [name for name in required if not callable(getattr(controller, name, None))]
    if missing:
        raise TypeError("controller is missing methods: " + ", ".join(missing))
    return controller
