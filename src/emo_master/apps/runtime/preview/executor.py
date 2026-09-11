from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import threading
from typing import Mapping
from uuid import uuid4

import numpy as np

from emo_master.apps.runtime.events.operator_logger import BufferedOperatorLogger
from emo_master.apps.runtime.preview.store import PreviewAsset, PreviewAssetStore
from emo_master.core.contracts.port_types import (
    isPortRequired,
    matchesPortSpec,
    normalizePortType,
)
from emo_master.core.plugin.models import PluginDescriptor


@dataclass(frozen=True)
class PreviewOutput:
    port: str
    asset: PreviewAsset


@dataclass(frozen=True)
class PreviewExecutionResult:
    ok: bool
    code: str = ""
    message: str = ""
    outputs: dict[str, object] | None = None
    assets: tuple[PreviewOutput, ...] = ()


class _PreviewCancelled(RuntimeError):
    pass


class PurePreviewExecutor:
    def __init__(
        self,
        operatorRegistry: Mapping[str, object],
        assetStore: PreviewAssetStore,
    ) -> None:
        self.operatorRegistry = operatorRegistry
        self.assetStore = assetStore
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="operator-preview")
        self._stateLock = threading.Lock()
        self._cancellations: dict[str, threading.Event] = {}
        self._closed = False

    def execute(
        self,
        operatorId: str,
        params: dict[str, object],
        imageAssetId: str,
        *,
        projectId: str = "",
        projectKey: str = "",
        workflowId: str = "",
        nodeId: str = "",
        timeoutSeconds: float = 5.0,
        requestId: str = "",
    ) -> PreviewExecutionResult:
        descriptor = self.operatorRegistry.get(operatorId)
        if not isinstance(descriptor, PluginDescriptor):
            return PreviewExecutionResult(False, "E_PREVIEW_UNSUPPORTED", "operator is unavailable")
        editor = descriptor.manifest.editor
        if editor is None or descriptor.editorIssues or editor.previewMode != "pure":
            return PreviewExecutionResult(
                False,
                "E_PREVIEW_UNSUPPORTED",
                "operator does not allow pure preview execution",
            )
        try:
            image = self.assetStore.readImage(imageAssetId)
        except (KeyError, ValueError) as err:
            return PreviewExecutionResult(False, "E_PREVIEW_SOURCE_NOT_FOUND", str(err))
        inputs: dict[str, object] = {"image": image}
        frame = self.assetStore.companionPayload(imageAssetId)
        if frame is not None and "frame" in descriptor.manifest.inputPorts:
            inputs["frame"] = frame
        cancellation = threading.Event()
        cancellationId = requestId or str(uuid4())
        with self._stateLock:
            if self._closed:
                return PreviewExecutionResult(
                    False,
                    "E_PREVIEW_EXEC_FAILED",
                    "preview executor is closed",
                )
            if cancellationId in self._cancellations:
                return PreviewExecutionResult(
                    False,
                    "E_RESOURCE_BUSY",
                    "preview request_id is already active",
                )
            self._cancellations[cancellationId] = cancellation
        try:
            future = self._executor.submit(
                self._executeNow,
                descriptor,
                inputs,
                params,
                projectId,
                projectKey,
                workflowId,
                nodeId,
                cancellation,
            )
        except RuntimeError as err:
            with self._stateLock:
                if self._cancellations.get(cancellationId) is cancellation:
                    self._cancellations.pop(cancellationId, None)
            return PreviewExecutionResult(False, "E_PREVIEW_EXEC_FAILED", str(err))

        def completed(done: Future[PreviewExecutionResult]) -> None:
            if cancellation.is_set():
                self._discardFutureAssets(done)
            with self._stateLock:
                if self._cancellations.get(cancellationId) is cancellation:
                    self._cancellations.pop(cancellationId, None)

        future.add_done_callback(completed)
        try:
            return future.result(timeout=max(0.1, timeoutSeconds))
        except FutureTimeoutError:
            cancellation.set()
            future.cancel()
            if future.done():
                self._discardFutureAssets(future)
            return PreviewExecutionResult(
                False,
                "E_PREVIEW_TIMEOUT",
                f"operator preview exceeded {max(0.1, timeoutSeconds):g} seconds",
            )
        except _PreviewCancelled:
            return PreviewExecutionResult(
                False,
                "E_CANCELLED",
                "operator preview cancelled",
            )
        except Exception as err:
            return PreviewExecutionResult(False, "E_PREVIEW_EXEC_FAILED", str(err))

    def _executeNow(
        self,
        descriptor: PluginDescriptor,
        inputs: dict[str, object],
        params: dict[str, object],
        projectId: str,
        projectKey: str,
        workflowId: str,
        nodeId: str,
        cancellation: threading.Event,
    ) -> PreviewExecutionResult:
        def raiseIfCancelled() -> None:
            if cancellation.is_set():
                raise _PreviewCancelled("operator preview cancelled")

        raiseIfCancelled()
        invalidInputs = [
            name
            for name, value in inputs.items()
            if name in descriptor.manifest.inputPorts
            and not matchesPortSpec(value, descriptor.manifest.inputPorts[name])
        ]
        if invalidInputs:
            return PreviewExecutionResult(
                False,
                "E_INPUT_TYPE",
                "invalid preview inputs: " + ", ".join(sorted(invalidInputs)),
            )
        missingInputs = [
            name
            for name, spec in descriptor.manifest.inputPorts.items()
            if isPortRequired(spec) and name not in inputs
        ]
        if missingInputs:
            return PreviewExecutionResult(
                False,
                "E_INPUT_MISSING",
                "missing preview inputs: " + ", ".join(sorted(missingInputs)),
            )
        operator = descriptor.operatorClass()
        previewLogger = BufferedOperatorLogger()
        validator = getattr(operator, "validateParams", None)
        if callable(validator):
            validationError = validator(params)
            if validationError is not None:
                if isinstance(validationError, dict):
                    return PreviewExecutionResult(
                        False,
                        str(validationError.get("code", "E_PARAM_INVALID")),
                        str(validationError.get("message", "invalid preview parameters")),
                    )
                return PreviewExecutionResult(False, "E_PARAM_INVALID", str(validationError))
        with tempfile.TemporaryDirectory(prefix="emo-preview-") as workspace:
            runtimeContext = {
                "jobId": "preview",
                "projectId": projectId,
                "workflowId": workflowId,
                "workflowRunId": "preview",
                "parentWorkflowRunId": "",
                "nodeId": nodeId,
                "nodeRunId": "preview",
                "iterationPath": [],
                "workspacePath": str(Path(workspace)),
                "isCancellationRequested": cancellation.is_set(),
                "raiseIfCancellationRequested": raiseIfCancelled,
                "isPreview": True,
                "logger": previewLogger,
            }
            raiseIfCancelled()
            try:
                result = operator.executeNode(inputs, params, runtimeContext)
            except Exception as err:
                summary = previewLogger.summary()
                suffix = f"; recent logs: {summary}" if summary else ""
                raise RuntimeError(f"{err}{suffix}") from err
            raiseIfCancelled()
        if not isinstance(result, dict) or result.get("status") != "ok":
            error = result.get("error", {}) if isinstance(result, dict) else {}
            code = error.get("code", "E_PREVIEW_EXEC_FAILED") if isinstance(error, dict) else "E_PREVIEW_EXEC_FAILED"
            message = error.get("message", "operator preview failed") if isinstance(error, dict) else str(error)
            summary = previewLogger.summary()
            suffix = f"; recent logs: {summary}" if summary else ""
            return PreviewExecutionResult(False, str(code), f"{message}{suffix}")
        rawOutputs = result.get("outputs", {})
        if not isinstance(rawOutputs, dict):
            return PreviewExecutionResult(False, "E_OUTPUT_TYPE", "preview outputs must be an object")
        missingOutputs = [
            name
            for name, spec in descriptor.manifest.outputPorts.items()
            if isPortRequired(spec) and name not in rawOutputs
        ]
        if missingOutputs:
            return PreviewExecutionResult(
                False,
                "E_OUTPUT_MISSING",
                "missing preview outputs: " + ", ".join(sorted(missingOutputs)),
            )
        structured: dict[str, object] = {}
        assets: list[PreviewOutput] = []
        for port, value in rawOutputs.items():
            spec = descriptor.manifest.outputPorts.get(port)
            if spec is None or not matchesPortSpec(value, spec):
                return PreviewExecutionResult(
                    False, "E_OUTPUT_TYPE", f"preview output has invalid type: {port}"
                )
        try:
            for port, value in rawOutputs.items():
                raiseIfCancelled()
                spec = descriptor.manifest.outputPorts.get(port)
                if spec is None:  # pragma: no cover - validated above
                    continue
                if normalizePortType(spec) == "image" and isinstance(value, np.ndarray):
                    asset = self.assetStore.addTransientImage(
                        value,
                        hint=port,
                        projectKey=projectKey,
                    )
                    assets.append(PreviewOutput(port, asset))
                elif _isJsonValue(value):
                    structured[port] = value
            raiseIfCancelled()
        except BaseException:
            for output in assets:
                self.assetStore.removeTransient(output.asset.assetId)
            raise
        return PreviewExecutionResult(
            True,
            outputs=structured,
            assets=tuple(assets),
        )

    def close(self) -> None:
        with self._stateLock:
            if self._closed:
                return
            self._closed = True
            cancellations = tuple(self._cancellations.values())
        for cancellation in cancellations:
            cancellation.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def cancel(self, requestId: str) -> bool:
        with self._stateLock:
            cancellation = self._cancellations.get(requestId)
        if cancellation is None:
            return False
        cancellation.set()
        return True

    def _discardFutureAssets(self, future: Future[PreviewExecutionResult]) -> None:
        try:
            result = future.result()
        except BaseException:
            return
        for output in result.assets:
            self.assetStore.removeTransient(output.asset.assetId)


def parsePreviewParams(rawValue: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        parsed = json.loads(rawValue or "{}")
    except json.JSONDecodeError as err:
        return None, f"invalid params_json: {err}"
    if not isinstance(parsed, dict):
        return None, "params_json must contain an object"
    return parsed, None


def _isJsonValue(value: object) -> bool:
    try:
        json.dumps(value, allow_nan=False)
        return True
    except (TypeError, ValueError):
        return False
