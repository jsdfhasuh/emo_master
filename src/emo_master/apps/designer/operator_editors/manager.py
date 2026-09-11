from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from emo_master.apps.designer.operator_editors.controller_protocol import (
    EditorContext,
    EditorKey,
    OperatorEditorController,
    loadController,
)
from emo_master.apps.designer.operator_editors.trust import EditorTrustStore
from emo_master.apps.designer.operator_editors.ui_loader import (
    EditorAssetCache,
    loadUiBytes,
)
from emo_master.apps.designer.operator_editors.workspace_window import (
    OperatorWorkspaceWindow,
)


class OperatorEditorManager:
    def __init__(
        self,
        *,
        runtimeClient: object,
        settingsStore: object,
        applyParams: Callable[[EditorKey, dict[str, object]], bool],
        appendLog: Callable[[str, str], None],
        cacheRoot: Path | None = None,
    ) -> None:
        self.runtimeClient = runtimeClient
        self.applyParams = applyParams
        self.appendLog = appendLog
        self._trustStore = EditorTrustStore(settingsStore)
        self._assetCache = EditorAssetCache(cacheRoot)
        self._windows: dict[EditorKey, OperatorWorkspaceWindow] = {}

    def open(
        self,
        *,
        projectId: str,
        workflowId: str,
        nodeId: str,
        operatorId: str,
        displayName: str,
        schema: dict[str, object],
        values: dict[str, object],
        operatorDefinition: dict[str, object] | None = None,
        workflowOptions: list[str] | None = None,
        parent: object | None = None,
    ) -> OperatorWorkspaceWindow:
        key = EditorKey(projectId, workflowId, nodeId)
        existing = self._windows.get(key)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing

        definition = operatorDefinition or {}
        version = str(definition.get("version", ""))
        rawEditor = definition.get("editorSpec", {})
        editorSpec = dict(rawEditor) if isinstance(rawEditor, dict) else {}
        editorIssues = definition.get("editorIssues", [])
        previewMode = str(editorSpec.get("previewMode", "none"))
        context = EditorContext(
            key=key,
            operatorId=operatorId,
            version=version,
            previewMode=previewMode,
            paramSchema=schema,
            runtimeClient=self.runtimeClient,
            applyParams=self.applyParams,
            appendLog=self.appendLog,
            workflowOptions=workflowOptions,
        )

        customRoot = None
        controller = None
        fallbackReason = ""
        if editorIssues:
            fallbackReason = "Runtime 在注册阶段禁用了此专用编辑器"
        elif editorSpec.get("kind") == "customUi":
            try:
                customRoot, controller = self._loadCustomEditor(
                    operatorId,
                    version,
                    editorSpec,
                    parent,
                )
            except Exception as err:
                fallbackReason = str(err)
                self.appendLog(
                    "WARN",
                    f"算子 {operatorId} 专用编辑器加载失败，已回退通用表单：{err}",
                )

        try:
            window = OperatorWorkspaceWindow(
                key=key,
                title=f"{displayName} · {nodeId}",
                context=context,
                schema=schema,
                values=values,
                customRoot=customRoot,
                controller=controller,
                fallbackReason=fallbackReason,
                parent=parent,
                onClosed=self._onWindowClosed,
            )
        except Exception as err:
            if controller is None:
                raise
            try:
                controller.dispose()
            except Exception:
                pass
            fallbackReason = str(err)
            self.appendLog(
                "WARN",
                f"算子 {operatorId} Controller 初始化失败，已回退通用表单：{err}",
            )
            window = OperatorWorkspaceWindow(
                key=key,
                title=f"{displayName} · {nodeId}",
                context=context,
                schema=schema,
                values=values,
                fallbackReason=fallbackReason,
                parent=parent,
                onClosed=self._onWindowClosed,
            )
        try:
            window.openController()
        except Exception as err:
            self.appendLog(
                "WARN", f"算子 {operatorId} Controller 打开失败，已回退通用表单：{err}"
            )
            window.forceClose()
            window = OperatorWorkspaceWindow(
                key=key,
                title=f"{displayName} · {nodeId}",
                context=context,
                schema=schema,
                values=values,
                fallbackReason=str(err),
                parent=parent,
                onClosed=self._onWindowClosed,
            )
        self._windows[key] = window
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def get(self, key: EditorKey) -> OperatorWorkspaceWindow | None:
        return self._windows.get(key)

    def keys(self) -> tuple[EditorKey, ...]:
        return tuple(self._windows)

    def count(self) -> int:
        return len(self._windows)

    def closeNode(self, projectId: str, workflowId: str, nodeId: str) -> None:
        self._closeKeys(
            key
            for key in self._windows
            if key.projectId == projectId
            and key.workflowId == workflowId
            and key.nodeId == nodeId
        )

    def closeWorkflow(self, projectId: str, workflowId: str) -> None:
        self._closeKeys(
            key
            for key in self._windows
            if key.projectId == projectId and key.workflowId == workflowId
        )

    def closeProject(self, projectId: str) -> None:
        self._closeKeys(key for key in self._windows if key.projectId == projectId)

    def closeAll(self) -> None:
        self._closeKeys(tuple(self._windows))

    def _closeKeys(self, keys) -> None:
        for key in list(keys):
            window = self._windows.get(key)
            if window is not None:
                window.forceClose()

    def _onWindowClosed(self, key: EditorKey, window: object) -> None:
        if self._windows.get(key) is window:
            self._windows.pop(key, None)

    def _loadCustomEditor(
        self,
        operatorId: str,
        version: str,
        editorSpec: dict[str, object],
        parent: object | None,
    ) -> tuple[object, OperatorEditorController]:
        method = getattr(self.runtimeClient, "getOperatorEditorAsset", None)
        if not callable(method):
            raise RuntimeError("连接的 Runtime 不支持专用算子编辑器")
        reply = method(operatorId, version)
        if not bool(getattr(reply, "ok", False)):
            raise RuntimeError(
                str(getattr(reply, "message", "")) or "Runtime 未返回编辑器资源"
            )
        content = bytes(getattr(reply, "content", b""))
        uiHash = str(getattr(reply, "sha256", ""))
        if not uiHash or hashlib.sha256(content).hexdigest() != uiHash:
            raise RuntimeError("编辑器 UI 完整性校验失败")
        self._assetCache.store(content, uiHash)
        controllerEntry = str(editorSpec.get("controllerEntry", ""))
        if not self._trustStore.ensureTrusted(
            operatorId=operatorId,
            version=version,
            controllerEntry=controllerEntry,
            uiHash=uiHash,
            parent=parent,
        ):
            raise RuntimeError("用户未信任此插件 Controller")
        root = loadUiBytes(content, parent)
        controller = loadController(controllerEntry)
        return root, controller
