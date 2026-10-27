"""Bounded, nonblocking snapshots; encoding and publication belong to one thread."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter, _SNAPSHOT_TYPES, _isJsonValue
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.core.contracts.port_types import normalizePortType


@dataclass
class _Capture:
    node: SimpleNamespace
    outputs: dict
    context: object
    captureId: str
    createdAtMs: int
    size: int
    inspection: bool


class AsyncPreviewSnapshotWriter:
    def __init__(self, jobWorkspace: Path, *, jobId="", projectRevision=0,
                 inspectionPublisher=None, errorPublisher, cancellation=None,
                 maxPending=64, maxBytes=64 * 1024 * 1024, inlineInspection=False,
                 minIntervalMs=0) -> None:
        if maxPending < 1 or maxBytes < 1:
            raise ValueError("snapshot buffer limits must be positive")
        self._writer = PreviewSnapshotWriter(jobWorkspace, jobId=jobId, projectRevision=projectRevision,
                                             inspectionPublisher=inspectionPublisher, inlineInspection=inlineInspection)
        self._errorPublisher = errorPublisher
        self._cancellation = cancellation or CancellationToken()
        self._maxPending, self._maxBytes = maxPending, maxBytes
        self._minInterval = max(0, minIntervalMs) / 1000.
        self._nextCaptureAt = 0.
        self._condition = threading.Condition()
        self._pending: OrderedDict[tuple, _Capture] = OrderedDict()
        self._bufferedBytes = 0  # Pending plus the one actively written capture.
        self._closing = False
        self._failure: Exception | None = None
        self._stats = dict(submitted=0, coalesced=0, written=0, failed=0, discarded=0, inspectionFailed=0,
                           peakPending=0, peakBufferedBytes=0)
        self._thread = threading.Thread(target=self._run, name=f"preview-writer-{jobId}", daemon=True)
        self._thread.start()

    @staticmethod
    def _identity(item):
        return dict(workflowId=item.context.workflowId, nodeId=item.node.nodeId,
                    workflowRunId=item.context.workflowRunId, nodeRunId=item.context.nodeRunId,
                    captureId=item.captureId)

    def capture(self, node, outputs, context):
        captureId = str(uuid4())
        capturedAt = int(time.time() * 1000)
        eligible, size = {}, 0
        for port, value in outputs.items():
            kind = normalizePortType(node.outputPorts.get(port, ""))
            if kind not in _SNAPSHOT_TYPES:
                continue
            if kind == "image":
                if not isinstance(value, np.ndarray) or value.dtype != np.uint8:
                    continue
                size += value.nbytes
            elif _isJsonValue(value):
                size += len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            else:
                continue
            eligible[port] = value
        if eligible:
            # Coalesce whole invocations with identical port sets, not individual
            # ports: image/geometry keep one capture identity. Partial outputs
            # retain their order and do not erase a different port's last value.
            key = (context.workflowId, node.nodeId, tuple(sorted(eligible)))
            inspection = self._writer.inspectionPublisher is not None and any(
                normalizePortType(node.outputPorts.get(port, "")) == "image" for port in eligible)
            metadata = dict(captureId=captureId, state="PREPARING" if inspection else "", retired=[])
            with self._condition:
                self._cancellation.raise_if_cancelled()
                if self._closing:
                    raise RuntimeError("snapshot writer is closed")
                self._stats["submitted"] += 1
                previous = self._pending.pop(key, None)
                if previous is not None:
                    self._retire(previous, metadata, "coalesced")
                del previous
                while self._pending and (len(self._pending) >= self._maxPending or
                        self._bufferedBytes + size > self._maxBytes):
                    _, previous = self._pending.popitem(last=False)
                    self._retire(previous, metadata, "discarded")
                    del previous
                if self._bufferedBytes and self._bufferedBytes + size > self._maxBytes:
                    # An active encoder owns its pixels until it returns. Skip
                    # optional preview admission instead of waiting on that owner.
                    self._stats["discarded"] += 1
                    metadata["state"] = "SKIPPED" if inspection else ""
                    return metadata if inspection or metadata["retired"] else None
                # Reserve before copying. Only one oversized capture is admitted
                # when otherwise empty; no unbounded feeder retains raw images.
                frozen = {port: value.copy() if isinstance(value, np.ndarray) else deepcopy(value)
                          for port, value in eligible.items()}
                item = _Capture(SimpleNamespace(nodeId=node.nodeId, outputPorts=deepcopy(dict(node.outputPorts))),
                                frozen, deepcopy(context), captureId, capturedAt, size, inspection)
                self._pending[key] = item  # Replacements move to the newest position.
                self._bufferedBytes += size
                self._stats["peakPending"] = max(self._stats["peakPending"], len(self._pending))
                self._stats["peakBufferedBytes"] = max(self._stats["peakBufferedBytes"], self._bufferedBytes)
                self._condition.notify_all()
            return metadata if inspection or metadata["retired"] else None
        return None

    def _retire(self, item, metadata, statistic):
        self._bufferedBytes -= item.size
        self._stats[statistic] += 1
        if item.inspection:
            # Piggyback small retirement notices on the next node.completed.
            # The producer never publishes image bytes or waits on a PNG writer.
            metadata["retired"].append(self._identity(item))

    def _inspect(self, item, encodedImages):
        if not item.inspection:
            return
        state, message = "READY", ""
        try:
            self._writer._captureInspection(item.node, item.outputs, item.context, item.captureId,
                                            createdAtMs=item.createdAtMs, encodedImages=encodedImages)
        except CancellationRequested:
            raise
        except Exception as error:
            state, message = "FAILED", str(error)[:300]
            with self._condition:
                self._stats["inspectionFailed"] += 1
        self._writer.inspectionPublisher(eventType="inspection.snapshot", context=item.context,
            message="node inspection completed", payload={"completion": dict(self._identity(item),
                originJobId=self._writer.jobId, originProjectRevision=self._writer.projectRevision,
                state=state, message=message)})
        if state == "FAILED":
            self._errorPublisher(eventType="preview.snapshot.failed", context=item.context,
                message=message, level="WARN", code="E_PREVIEW_SNAPSHOT_FAILED",
                payload={"status": "FAILED", "message": message, "captureId": item.captureId})

    def captureArtifact(self, node, artifact, context) -> None:
        self._writer.captureArtifact(node, artifact, context)

    def _discardPending(self) -> None:
        self._stats["discarded"] += len(self._pending)
        self._bufferedBytes -= sum(item.size for item in self._pending.values())
        self._pending.clear()
        self._condition.notify_all()

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    while not self._pending and not self._closing:
                        self._condition.wait()
                    if self._cancellation.is_cancelled():
                        self._closing = True
                        self._discardPending()
                    if not self._pending:
                        return
                    # Continuous Jobs share a bounded preview cadence. Waiting
                    # here coalesces pending invocations, never the business path.
                    delay = self._nextCaptureAt - time.monotonic()
                    if not self._closing and delay > 0:
                        self._condition.wait(min(delay, .05))
                        continue
                    _, item = self._pending.popitem(last=False)
                    self._nextCaptureAt = time.monotonic() + self._minInterval
                    self._condition.notify_all()
                try:
                    encodedImages: dict[str, bytes] = {}
                    self._inspect(item, encodedImages)
                    self._writer.captureLegacy(item.node, item.outputs, item.context,
                                               captureId=item.captureId, createdAtMs=item.createdAtMs,
                                               encodedImages=encodedImages)
                    with self._condition:
                        self._stats["written"] += 1
                except CancellationRequested:
                    with self._condition:
                        self._stats["discarded"] += 1
                except Exception as error:
                    with self._condition:
                        self._stats["failed"] += 1
                    self._errorPublisher(eventType="preview.snapshot.failed", context=item.context,
                        message=str(error), level="WARN", code="E_PREVIEW_SNAPSHOT_FAILED",
                        payload={"status": "FAILED", "message": str(error), "captureId": item.captureId})
                finally:
                    encodedImages.clear()
                    with self._condition:
                        self._bufferedBytes -= item.size
                        self._condition.notify_all()
                del item  # Do not retain the last full-resolution image while idle.
        except Exception as error:
            # A failed notification channel must not leave producers waiting on
            # a dead thread. The worker owner observes it during close.
            with self._condition:
                self._failure = error
                self._closing = True
                self._discardPending()

    def close(self, *, drain=True) -> None:
        with self._condition:
            self._closing = True
            if not drain or self._cancellation.is_cancelled():
                self._discardPending()
            self._condition.notify_all()
        while self._thread.is_alive():
            self._thread.join(.05)
            if self._cancellation.is_cancelled():
                with self._condition:
                    self._discardPending()
        with self._condition:
            error, self._failure = self._failure, None
        if error is not None:
            raise RuntimeError(f"snapshot worker failed: {error}") from error

    def statistics(self) -> dict[str, int]:
        with self._condition:
            return dict(self._stats)
