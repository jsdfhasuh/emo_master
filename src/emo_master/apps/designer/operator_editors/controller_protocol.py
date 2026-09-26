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
    ) -> None:
        self.key = key
        self.operatorId = operatorId
        self.version = version
        self.previewMode = previewMode
        self.paramSchema = dict(paramSchema)
        self.workflowOptions = list(workflowOptions or [])
        self._runtimeClient = runtimeClient
        self._applyParams = applyParams
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

    def listPreviewSources(self) -> list[object]:
        method = self._runtimeMethod("listNodePreviewSources")
        result = method(self.key.projectId, self.key.workflowId, self.key.nodeId)
        return list(result) if isinstance(result, Iterable) else []

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
        return self._runtimeMethod("runOperatorPreview")(
            self.key.projectId,
            self.key.workflowId,
            self.key.nodeId,
            self.operatorId,
            dict(params),
            imageAssetId,
            requestId,
        )

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
        reply = self._runtimeMethod("openOperatorPreviewSession")(
            self.key.projectId,
            self.key.workflowId,
            self.key.nodeId,
            self.operatorId,
            dict(params),
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
