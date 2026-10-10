from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import copy, deepcopy
from dataclasses import dataclass
import importlib
import logging
import threading
import time
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
        # Reconnection must not send a previous window's session to a new
        # Runtime. Cleanup remains pinned to the original transport.
        self._pureRuntimeClient = copy(runtimeClient)
        self._pureService = getattr(runtimeClient, "runtimeService", None)
        self._getCurrentJobId = getCurrentJobId or (lambda: None)
        self._getSqliteDraft = getSqliteDraft
        self._getPreviewProject = getPreviewProject
        self._pureDraftLock = threading.RLock()
        self._pureDraftTouched = 0.0
        self._pureDraftSessionId = ""
        self._pureDraftFingerprint = ""
        self._pureDraftProject = None
        self._pureDraftAssets: set[str] = set()
        self._pureDraftOutputAssets: set[str] = set()
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
        if self._currentPureDraft() is not None:
            return PreviewSourceListing(sources=(), captureState="DRAFT_LOCAL_ONLY",
                message="草稿仅支持本地图片纯预览；不复用旧工程快照。任务图片请到运行结果检查查看。变量使用草稿初始值。")
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
        self.prepareLocalPreview()
        options = {"draftSessionId": self._pureDraftSessionId} if self._pureDraftSessionId else {}
        reply = self._runtimeMethod("uploadPreviewImage", client=self._pureRuntimeClient if options else None)(
            data, filename, self.key.projectId, **options)
        if not bool(getattr(reply, "ok", False)):
            self.closePurePreview()
        self._requireOk(reply, "E_PREVIEW_ASSET_INVALID")
        self._pureDraftTouched = time.monotonic()
        assetId = str(getattr(reply, "asset_id", ""))
        if not assetId:
            raise EditorContextError(
                "E_PREVIEW_ASSET_INVALID", "Runtime did not return a preview asset"
            )
        if self._pureDraftSessionId:
            self._pureDraftAssets.add(assetId)
        return assetId

    def downloadPreviewAsset(self, assetId: str) -> tuple[bytes, str]:
        options = {}
        if self._currentPureDraft() is not None:
            self._requireCurrentPureDraft()
            if assetId not in self._pureDraftAssets:
                raise EditorContextError("E_PREVIEW_ASSET_INVALID", "图片不属于当前预览窗口的草稿会话，请重新选择预览图片")
            options = {"draftSessionId": self._pureDraftSessionId}
        result = self._runtimeMethod("downloadPreviewAsset", client=self._pureRuntimeClient if options else None)(
            assetId, self.key.projectId, **options)
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
        if self._currentPureDraft() is not None:
            # Direct callers remain supported; GUI workers use preparePurePreview
            # to capture mutable Qt/canvas state on the GUI thread first.
            return self.preparePurePreview(params, imageAssetId, requestId)()
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

    def _currentPureDraft(self):
        if self.previewMode != "pure" or self._getPreviewProject is None:
            return None
        self._checkPureRuntime()
        payload = self._variablePreviewProject() if self.variableDefinitions is not None else self._getPreviewProject(self.key)
        try:
            next(node for node in payload["workflows"][self.key.workflowId]["nodes"]
                 if node["nodeId"] == self.key.nodeId)
        except (KeyError, TypeError, StopIteration) as error:
            raise EditorContextError("E_PREVIEW_CONTEXT_INVALID", "当前草稿节点已删除或切换，请重新打开配置窗口") from error
        # A current canvas draft is authoritative even for default 2.1 projects.
        # Only callers without a draft provider use the legacy loaded-project path.
        return deepcopy(payload)

    def _checkPureRuntime(self):
        if getattr(self._runtimeClient, "runtimeService", None) is not self._pureService:
            raise EditorContextError("E_PREVIEW_CONTEXT_INVALID", "Runtime 连接已改变，请重新打开预览窗口")

    def prepareLocalPreview(self):
        """Called before the file picker: unsupported Runtime never invites an upload."""
        payload = self._currentPureDraft()
        if payload is None:
            return
        from emo_master.apps.runtime.preview.pure_draft import draftFingerprint
        fingerprint = draftFingerprint(payload)
        if (self._pureDraftSessionId and fingerprint == self._pureDraftFingerprint
                and time.monotonic() - self._pureDraftTouched < 290):
            return
        self.closePurePreview()
        reply = self._runtimeMethod("openDraftPurePreviewSession", client=self._pureRuntimeClient)(
            self.key.projectId, self.key.workflowId, self.key.nodeId, self.operatorId, payload)
        self._requireOk(reply, "E_PREVIEW_CONTEXT_INVALID")
        sessionId = str(getattr(reply, "session_id", ""))
        if not sessionId:
            raise EditorContextError("E_PREVIEW_CONTEXT_INVALID", "Runtime 未返回草稿纯预览会话")
        with self._pureDraftLock:
            self._pureDraftSessionId = sessionId
            self._pureDraftFingerprint = fingerprint
            self._pureDraftProject = payload
            self._pureDraftTouched = time.monotonic()

    def _requireCurrentPureDraft(self):
        from emo_master.apps.runtime.preview.pure_draft import draftFingerprint
        payload = self._currentPureDraft()
        if (not self._pureDraftSessionId or payload is None
                or draftFingerprint(payload) != self._pureDraftFingerprint):
            self.closePurePreview()
            raise EditorContextError("E_PREVIEW_CONTEXT_INVALID", "工程草稿已变更或会话已失效，请重新选择预览图片")
        return payload

    def preparePurePreview(self, params, imageAssetId, requestId=""):
        """Freeze draft and editor values on the UI thread, not in its worker."""
        from functools import partial
        if self.previewMode != "pure":
            raise EditorContextError("E_PREVIEW_UNSUPPORTED", "operator does not allow pure preview")
        payload = self._currentPureDraft()
        if payload is None:
            options = {}
            if self.variableDefinitions is not None and self._getPreviewProject is not None:
                options = {"projectPayload": self._variablePreviewProject(), "jobId": self.currentJobId()}
            return partial(self._runtimeMethod("runOperatorPreview"),
                self.key.projectId, self.key.workflowId, self.key.nodeId, self.operatorId,
                deepcopy(params), imageAssetId, requestId, **options)
        payload = self._requireCurrentPureDraft()
        if imageAssetId not in self._pureDraftAssets:
            raise EditorContextError("E_PREVIEW_ASSET_INVALID", "图片不属于当前草稿预览会话")
        return partial(self._runDraftPurePreview, deepcopy(params), imageAssetId, requestId,
                       self._pureDraftSessionId, payload)

    def _runDraftPurePreview(self, params, imageAssetId, requestId, sessionId, payload):
        self._checkPureRuntime()
        reply = self._runtimeMethod("runOperatorPreview", client=self._pureRuntimeClient)(
            self.key.projectId, self.key.workflowId, self.key.nodeId, self.operatorId,
            params, imageAssetId, requestId, projectPayload=payload, draftSessionId=sessionId)
        # The GUI discards a late reply by generation. Never adopt its assets
        # into a replacement session even when the project/node IDs are equal.
        with self._pureDraftLock:
            if self._pureDraftSessionId == sessionId and bool(getattr(reply, "ok", False)):
                self._pureDraftTouched = time.monotonic()
                self._pureDraftAssets.difference_update(self._pureDraftOutputAssets)
                self._pureDraftOutputAssets = {str(asset.asset_id) for asset in getattr(reply, "assets", [])}
                self._pureDraftAssets.update(self._pureDraftOutputAssets)
            elif self._pureDraftSessionId == sessionId and str(getattr(reply, "code", "")) == "E_PREVIEW_CONTEXT_INVALID":
                self._pureDraftTouched = 0.0
        return reply

    def validatePurePreviewResult(self):
        # Result delivery runs on the GUI thread and must re-check edits made
        # after dispatch. Histogram has no image download to do this for it.
        if self._pureDraftSessionId or self._currentPureDraft() is not None:
            self._requireCurrentPureDraft()

    def closePurePreview(self):
        with self._pureDraftLock:
            sessionId = self._pureDraftSessionId
            self._pureDraftSessionId = ""
            self._pureDraftFingerprint = ""
            self._pureDraftProject = None
            self._pureDraftAssets.clear()
            self._pureDraftOutputAssets.clear()
        if sessionId:
            try:
                reply = self._runtimeMethod("closeDraftPurePreviewSession", client=self._pureRuntimeClient)(sessionId)
                self._requireOk(reply, "E_PREVIEW_RELEASE_FAILED")
            except Exception as error:
                # Retirement may run after all widgets have been destroyed,
                # on a background thread. Never invoke a Qt-owned log hook.
                logging.getLogger(__name__).warning("纯预览会话释放失败（租约到期后失效）：%s", error)

    def _variablePreviewProject(self):
        payload = deepcopy(self._getPreviewProject(self.key))
        try:
            node = next(item for item in payload["workflows"][self.key.workflowId]["nodes"] if item["nodeId"] == self.key.nodeId)
        except (KeyError, TypeError, StopIteration) as error:
            raise EditorContextError("E_PREVIEW_CONTEXT_INVALID", "当前草稿节点已删除或切换，请重新打开配置窗口") from error
        bindings = self.collectVariableBindings()
        if bindings:
            node["globalVariableBindings"] = bindings
        else:
            node.pop("globalVariableBindings", None)
        return payload

    def cancelPurePreview(self, requestId: str) -> None:
        if not requestId:
            return
        reply = self._runtimeMethod("cancelOperatorPreview",
            client=self._pureRuntimeClient if self._getPreviewProject is not None else None)(requestId)
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

    def _runtimeMethod(self, name: str, *, client=None):
        method = getattr(self._runtimeClient if client is None else client, name, None)
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
