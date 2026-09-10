from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable, Iterator, Mapping
from uuid import uuid4

import cv2
import numpy as np

from emo_master.apps.runtime.events.operator_logger import BufferedOperatorLogger
from emo_master.core.plugin.models import PluginDescriptor


@dataclass(frozen=True)
class LivePreviewFrame:
    sessionId: str
    jpeg: bytes
    sequence: int
    blockId: int
    deviceTimestamp: int
    actualExposureUs: float
    width: int
    height: int


class _LiveSession:
    def __init__(
        self,
        sessionId: str,
        projectId: str,
        workflowId: str,
        nodeId: str,
        operator: Any,
        params: dict[str, object],
        onTerminated: Callable[["_LiveSession"], None],
    ) -> None:
        self.sessionId = sessionId
        self.projectId = projectId
        self.workflowId = workflowId
        self.nodeId = nodeId
        self.operator = operator
        self.params = params
        self.onTerminated = onTerminated
        self.stopEvent = threading.Event()
        self.condition = threading.Condition()
        self.latest: LivePreviewFrame | None = None
        self.error: str = ""
        self.errorCode = "E_PREVIEW_FAILED"
        self._disposeLock = threading.Lock()
        self._disposed = False
        self._disposeError: str | None = None
        self.logger = BufferedOperatorLogger()
        self.thread = threading.Thread(
            target=self._run,
            name=f"operator-live-preview-{sessionId}",
            daemon=True,
        )

    def start(self) -> None:
        init = getattr(self.operator, "initOperator", None)
        if callable(init):
            init(self._context())
        self.thread.start()

    def close(self, timeoutSeconds: float = 3.0) -> str | None:
        self.stopEvent.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=max(0.0, timeoutSeconds))
        if self.thread.is_alive():
            return (
                "preview capture thread did not stop within "
                f"{max(0.0, timeoutSeconds):g} seconds"
            )
        return self._dispose()

    def frames(self, context: object | None = None) -> Iterator[LivePreviewFrame]:
        delivered = 0
        while not self.stopEvent.is_set():
            isActive = getattr(context, "is_active", None)
            if callable(isActive) and not isActive():
                return
            frame: LivePreviewFrame | None = None
            with self.condition:
                self.condition.wait_for(
                    lambda: self.stopEvent.is_set()
                    or self.error != ""
                    or (self.latest is not None and self.latest.sequence > delivered),
                    timeout=1.0,
                )
                if self.latest is not None and self.latest.sequence > delivered:
                    delivered = self.latest.sequence
                    frame = self.latest
                elif self.error:
                    raise RuntimeError(self.error)
            if frame is not None:
                yield frame

    def _run(self) -> None:
        try:
            self._captureLoop()
        finally:
            disposeError = self._dispose()
            if disposeError:
                with self.condition:
                    if not self.error:
                        self.error = f"preview cleanup failed: {disposeError}"
                    self.condition.notify_all()
            self.onTerminated(self)

    def _captureLoop(self) -> None:
        sequence = 0
        while not self.stopEvent.is_set():
            started = time.monotonic()
            try:
                result = self.operator.executeNode({}, dict(self.params), self._context())
                if not isinstance(result, dict) or result.get("status") != "ok":
                    error = result.get("error", {}) if isinstance(result, dict) else {}
                    message = error.get("message", "live preview failed") if isinstance(error, dict) else str(error)
                    if isinstance(error, dict):
                        self.errorCode = str(error.get("code") or "E_PREVIEW_FAILED")
                    raise RuntimeError(f"{self.errorCode}: {message}")
                outputs = result.get("outputs", {})
                image = outputs.get("image") if isinstance(outputs, dict) else None
                if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
                    raise RuntimeError("live preview did not return a uint8 image")
                preview = _previewImage(image)
                ok, encoded = cv2.imencode(
                    ".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
                )
                if not ok:
                    raise RuntimeError("failed to encode live preview JPEG")
                height, width = preview.shape[:2]
                sequence += 1
                frame = LivePreviewFrame(
                    sessionId=self.sessionId,
                    jpeg=encoded.tobytes(),
                    sequence=sequence,
                    blockId=_intValue(outputs.get("blockId", 0)),
                    deviceTimestamp=_intValue(outputs.get("deviceTimestamp", 0)),
                    actualExposureUs=_floatValue(
                        outputs.get("actualExposureUs", 0.0)
                    ),
                    width=int(width),
                    height=int(height),
                )
                with self.condition:
                    self.latest = frame
                    self.condition.notify_all()
            except Exception as err:
                if not self.stopEvent.is_set():
                    with self.condition:
                        summary = self.logger.summary()
                        suffix = f"; recent logs: {summary}" if summary else ""
                        self.error = f"{err}{suffix}"
                        self.condition.notify_all()
                return
            remaining = 0.1 - (time.monotonic() - started)
            if remaining > 0:
                self.stopEvent.wait(remaining)

    def _dispose(self) -> str | None:
        with self._disposeLock:
            if self._disposed:
                return self._disposeError
            self._disposed = True
            dispose = getattr(self.operator, "disposeOperator", None)
            if callable(dispose):
                try:
                    dispose()
                except Exception as err:
                    self._disposeError = str(err)
            return self._disposeError

    def _context(self) -> dict[str, object]:
        def raiseIfCancelled() -> None:
            if self.stopEvent.is_set():
                error = RuntimeError("preview stopped")
                error.code = "E_CANCELLED"  # type: ignore[attr-defined]
                raise error

        return {
            "jobId": "preview",
            "projectId": self.projectId,
            "workflowId": self.workflowId,
            "workflowRunId": self.sessionId,
            "parentWorkflowRunId": "",
            "nodeId": self.nodeId,
            "nodeRunId": self.sessionId,
            "iterationPath": [],
            "workspacePath": "",
            "isCancellationRequested": self.stopEvent.is_set(),
            "raiseIfCancellationRequested": raiseIfCancelled,
            "isPreview": True,
            "logger": self.logger,
        }


class LivePreviewManager:
    def __init__(
        self,
        operatorRegistry: Mapping[str, object],
        eventPublisher: Callable[..., object] | None = None,
    ) -> None:
        self.operatorRegistry = operatorRegistry
        self.eventPublisher = eventPublisher
        self._sessions: dict[str, _LiveSession] = {}
        # Keep only bounded error summaries, never disposed operators or images.
        self._failures: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()

    def open(
        self,
        operatorId: str,
        projectId: str,
        workflowId: str,
        nodeId: str,
        params: dict[str, object],
    ) -> tuple[str | None, str | None]:
        descriptor = self.operatorRegistry.get(operatorId)
        if not isinstance(descriptor, PluginDescriptor):
            return None, "operator is unavailable"
        editor = descriptor.manifest.editor
        if editor is None or descriptor.editorIssues or editor.previewMode != "live":
            return None, "operator does not allow live preview"
        operator = descriptor.operatorClass()
        validator = getattr(operator, "validateParams", None)
        previewParams = dict(params)
        previewParams["triggerMode"] = "freeRun"
        if callable(validator):
            validation = validator(previewParams)
            if validation is not None:
                return None, str(validation.get("message", validation)) if isinstance(validation, dict) else str(validation)
        previewParams["captureTimeoutMs"] = min(
            _intValue(previewParams.get("captureTimeoutMs", 1000), 1000), 1000
        )
        sessionId = str(uuid4())
        session = _LiveSession(
            sessionId,
            projectId,
            workflowId,
            nodeId,
            operator,
            previewParams,
            self._sessionTerminated,
        )
        with self._lock:
            self._sessions[sessionId] = session
        try:
            session.start()
        except Exception as err:
            cleanupError = session.close()
            with self._lock:
                self._sessions.pop(sessionId, None)
            if cleanupError:
                return None, f"{err}; cleanup failed: {cleanupError}"
            return None, str(err)
        return sessionId, None

    def stream(self, sessionId: str, context: object | None = None) -> Iterator[LivePreviewFrame]:
        with self._lock:
            session = self._sessions.get(sessionId)
            failure = self._failures.get(sessionId)
        if session is None:
            if failure is not None:
                raise RuntimeError(failure[1])
            return iter(())
        return session.frames(context)

    def close(self, sessionId: str, timeoutSeconds: float = 3.0) -> str | None:
        with self._lock:
            session = self._sessions.get(sessionId)
            self._failures.pop(sessionId, None)
        if session is None:
            return None
        error = session.close(timeoutSeconds)
        if error is None:
            with self._lock:
                self._failures.pop(sessionId, None)
                if self._sessions.get(sessionId) is session:
                    self._sessions.pop(sessionId, None)
        return error

    def _sessionTerminated(self, session: _LiveSession) -> None:
        with self._lock:
            if session.error and not session.stopEvent.is_set():
                self._failures[session.sessionId] = (session.projectId, session.error)
                while len(self._failures) > 128:
                    self._failures.pop(next(iter(self._failures)))
            if self._sessions.get(session.sessionId) is session:
                self._sessions.pop(session.sessionId, None)
        if session.error and self.eventPublisher is not None:
            try:
                self.eventPublisher(
                    jobId="__runtime__",
                    eventType="node.preview.failed",
                    message=session.error,
                    level="ERROR",
                    code=session.errorCode,
                    projectId=session.projectId,
                    workflowId=session.workflowId,
                    nodeId=session.nodeId,
                    workflowRunId=session.sessionId,
                    nodeRunId=session.sessionId,
                    payload={"phase": "preview", "logs": session.logger.records()},
                )
            except Exception:
                # A failed log sink must not prevent resource cleanup or delivery.
                pass

    def closeProject(self, projectId: str, timeoutSeconds: float = 3.0) -> list[str]:
        with self._lock:
            self._failures = {
                key: value for key, value in self._failures.items()
                if value[0] != projectId
            }
            sessionIds = [
                sessionId
                for sessionId, session in self._sessions.items()
                if session.projectId == projectId
            ]
        errors = []
        deadline = time.monotonic() + max(0.0, timeoutSeconds)
        for sessionId in sessionIds:
            remaining = max(0.0, deadline - time.monotonic())
            error = self.close(sessionId, remaining)
            if error:
                errors.append(error)
        return errors

    def closeAll(self) -> list[str]:
        with self._lock:
            self._failures.clear()
            sessionIds = list(self._sessions)
        return [error for sessionId in sessionIds if (error := self.close(sessionId))]


def _previewImage(image: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    height, width = image.shape[:2]
    scale = min(1.0, 1920.0 / max(width, height))
    if scale >= 1.0:
        return image
    return cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _intValue(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _floatValue(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default
