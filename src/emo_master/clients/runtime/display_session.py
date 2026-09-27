"""Read-only shared session: one subscription, one bounded decode worker.

Only generated wire classes and core DTOs are imported. No Designer, Qt,
WorkflowRunner, Supervisor or Runtime service implementation is loaded.
"""
from collections import deque
import hashlib
import json
import queue
import threading
import time
from types import MappingProxyType

import grpc

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.core.presentation.results import ClosedResult
from emo_master.clients.runtime.view_state import ScopeView, SessionView


def decodeResult(wire):
    identity = wire.identity
    sources = []
    for source in wire.sources:
        value = {"sourceId": source.source_id, "state": source.state}
        if source.HasField("value_json"):
            value["valueJson"] = source.value_json
        if source.reason_code:
            value.update(reasonCode=source.reason_code, reason=source.reason or source.reason_code)
        if source.HasField("image"):
            image = source.image
            value["image"] = {"resourceId": image.resource_id, "ownerResultKey": image.owner_result_key,
                "byteSize": image.byte_size, "sha256": image.sha256, "mimeType": image.mime_type,
                "provenance": {"frameIdentity": image.frame_identity, "coordinateSpaceId": image.coordinate_space_id,
                               "trust": image.trust, "adapterVersion": image.adapter_version or None,
                               "parentFrameIdentity": image.parent_frame_identity or None}}
        sources.append(value)
    return ClosedResult.model_validate_json(json.dumps({"identity": {
        "runtimeInstanceId": identity.runtime_instance_id, "jobId": identity.job_id,
        "resultScopeId": identity.result_scope_id, "invocationId": identity.invocation_id,
        "resultKey": identity.result_key, "resultOrdinal": identity.result_ordinal,
        "executionRevision": identity.execution_revision, "capturePlanRevision": identity.capture_plan_revision,
        "mode": identity.mode}, "expectedSourceIds": list(wire.expected_source_ids), "sources": sources,
        "status": wire.status, "executionTerminal": wire.execution_terminal,
        "timing": {"captureStartedNs": wire.capture_started_ns, "scopeEndedNs": wire.scope_ended_ns,
                   "closedNs": wire.closed_ns} if wire.capture_started_ns else None}))


def decodePng(content):
    import cv2
    import numpy as np
    if len(content) < 33 or content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
        raise ValueError("invalid PNG header")
    width, height = int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(content[25], 0)
    if content[24] != 8 or channels == 0 or not 0 < width * height * channels <= 8 * 1024 * 1024:
        raise ValueError("decoded image budget exceeded")
    pixels = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.nbytes > 8 * 1024 * 1024:
        raise ValueError("image decoding failed or exceeded budget")
    # Immutable bytes prevent observers from enabling writes again.
    return np.frombuffer(pixels.tobytes(), dtype=pixels.dtype).reshape(pixels.shape)


class DisplaySession:
    def __init__(self, address, jobId):
        if not jobId:
            raise ValueError("an explicitly selected Job is required")
        self.channel = grpc.insecure_channel(address, options=[("grpc.max_receive_message_length", 9 * 1024 * 1024)])
        self.stub = rpc.DisplayServiceStub(self.channel)
        self.jobId = jobId
        self.instanceId = ""
        self.cursor = 0
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.pending = queue.Queue(maxsize=8)
        self.latest = {}
        self.high = {}
        self.started = {}
        self.seen = deque(maxlen=32)
        self.records = deque(maxlen=128)
        self.errors = deque(maxlen=64)
        self.stats = {"received": 0, "decoded": 0, "dropped": 0, "read_failed": 0, "resets": 0}
        self.listeners = set()
        self.generation = 0
        self.stream = None
        self.connection = "CONNECTING"
        self.connectionDetail = "连接指定任务"
        self.revision = 0
        self.loading = {}
        self.readyAt = {}
        self._pinStore = None
        self.compatible = False
        self.threads = [threading.Thread(target=self._receive, name="display-metadata"),
                        threading.Thread(target=self._health, name="display-snapshot-health"),
                        threading.Thread(target=self._decode, name="display-read-decode")]
        for thread in self.threads:
            thread.start()

    def observe(self, callback):
        with self.lock:
            self.listeners.add(callback)
        def detach():
            with self.lock:
                self.listeners.discard(callback)
        return detach

    def readSnapshot(self):
        """Atomic initial/current read; no RPC, execution or writable mappings.

        Qt adapters poll at a bounded rate instead of queueing one GUI event per
        result. Legacy observe(callback(result)) remains supported.
        """
        with self.lock:
            scopes = {key: ScopeView(result, MappingProxyType(dict(images)),
                       MappingProxyType(dict(errors)), self.readyAt.get(key, 0))
                      for key, (result, images, errors) in self.latest.items()}
            return SessionView(self.revision, self.generation, self.instanceId, self.jobId,
                self.connection, self.connectionDetail, MappingProxyType(scopes),
                MappingProxyType(dict(self.loading)), MappingProxyType(dict(self.started)))

    def pins(self):
        with self.lock:
            if self._pinStore is None:
                from emo_master.clients.runtime.pins import PinStore
                self._pinStore = PinStore(self)
            return self._pinStore

    def _connection(self, state, detail=""):
        with self.lock:
            if (state, detail) != (self.connection, self.connectionDetail):
                self.connection, self.connectionDetail = state, detail
                self.revision += 1

    def _accept(self, snapshot):
        with self.lock:
            if snapshot.job_id != self.jobId:
                return
            self._connection("CONNECTED")
            self.revision += 1
            changed = snapshot.runtime_instance_id != self.instanceId
            if changed or snapshot.reset_required:
                self.generation += 1
                self.latest.clear()
                self.high.clear()
                self.started.clear()
                self.seen.clear()
                self.loading.clear()
                self.readyAt.clear()
                self.stats["resets"] += 1
            elif snapshot.cursor < self.cursor:
                return
            self.instanceId, self.cursor = snapshot.runtime_instance_id, snapshot.cursor
            for scope, ordinal in snapshot.latest_started_ordinals.items():
                self.started[scope] = max(ordinal, self.started.get(scope, 0))
            for wire in snapshot.results:
                result = decodeResult(wire)
                if (result.identity.jobId != self.jobId or result.identity.runtimeInstanceId != self.instanceId):
                    raise ValueError("result belongs to another Runtime/Job")
                if result.identity.resultKey in self.seen:
                    continue
                scope = result.identity.resultScopeId
                if result.identity.resultOrdinal > self.high.get(scope, 0):
                    self.high[scope] = result.identity.resultOrdinal
                    # An actual new closed result invalidates the preceding image,
                    # including when this result is incomplete/failed.
                    self.latest.pop(scope, None)
                    self.loading[scope] = result
                self.stats["received"] += 1
                task = (self.generation, result, time.perf_counter_ns(), wire.owner_age_ms)
                try:
                    self.pending.put_nowait(task)
                    # A rejected final notification must remain eligible for
                    # the authoritative health snapshot to retry delivery.
                    self.seen.append(result.identity.resultKey)
                except queue.Full:
                    self.stats["dropped"] += 1

    def _receive(self):
        while not self.stop.is_set():
            try:
                capabilities = self.stub.Capabilities(pb.DisplayEmpty(), timeout=.5)
                if capabilities.protocol_version != "1.0" or not {"snapshot", "subscribe", "asset_id"}.issubset(capabilities.capabilities):
                    self.compatible = False
                    self._connection("INCOMPATIBLE", "服务端不支持正式展示协议")
                    self.stop.wait(.5)
                    continue
                self.compatible = True
                with self.lock:
                    if self.instanceId and capabilities.runtime_instance_id != self.instanceId:
                        self.generation += 1
                        self.latest.clear()
                        self.high.clear()
                        self.started.clear()
                        self.seen.clear()
                        self.loading.clear()
                        self.readyAt.clear()
                        self.cursor = 0
                        self.instanceId = capabilities.runtime_instance_id
                        self.stats["resets"] += 1
                request = pb.DisplayRequest(runtime_instance_id=self.instanceId, job_id=self.jobId, after_cursor=self.cursor, replay=True)
                # Periodic snapshot also recovers a lost final notification.
                self._accept(self.stub.Snapshot(request, timeout=.5))
                request.runtime_instance_id, request.after_cursor = self.instanceId, self.cursor
                request.replay = False
                self.stream = self.stub.Subscribe(request)
                for snapshot in self.stream:
                    self._accept(snapshot)
                    if self.stop.is_set():
                        break
            except grpc.RpcError as error:
                if not self.stop.is_set():
                    self._connection(error.code().name, error.details() or "连接失效")
                if error.code() not in (grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.CANCELLED):
                    self.errors.append((error.code().name, error.details()))
                    self.stop.wait(.05)
            except (ValueError, TypeError) as error:
                self._connection("INVALID_RESULT", str(error))
                self.errors.append(("INVALID_RESULT", str(error)))
                self.stop.wait(.05)
            finally:
                if self.stream is not None:
                    self.stream.cancel()

    def _health(self):
        while not self.stop.wait(.5):
            if not self.compatible:
                continue
            with self.lock:
                generation = self.generation
                request = pb.DisplayRequest(runtime_instance_id=self.instanceId,
                    job_id=self.jobId, after_cursor=self.cursor, replay=True)
            try:
                snapshot = self.stub.Snapshot(request, timeout=.5)
                with self.lock:
                    if generation == self.generation:
                        self._accept(snapshot)
            except grpc.RpcError as error:
                if generation != self.generation:
                    continue
                if not self.stop.is_set():
                    self._connection(error.code().name, error.details() or "连接失效")
                if error.code() == grpc.StatusCode.NOT_FOUND:
                    with self.lock:
                        self.latest.clear()
                if not self.stop.is_set():
                    self.errors.append((error.code().name, error.details()))

            except (ValueError, TypeError) as error:
                if generation == self.generation:
                    self._connection("INVALID_RESULT", str(error))

    def _decode(self):
        while not self.stop.is_set():
            try:
                generation, result, receivedAt, ownerAge = self.pending.get(timeout=.05)
            except queue.Empty:
                continue
            with self.lock:
                if generation != self.generation or result.identity.jobId != self.jobId:
                    continue
            images = {}
            failures = {}
            started = time.monotonic()
            for source in result.sources:
                if source.image is None:
                    continue
                try:
                    image = source.image
                    reply = self.stub.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=result.identity.runtimeInstanceId,
                        job_id=result.identity.jobId, resource_id=image.resourceId), timeout=.5)
                    content = reply.content
                    if len(content) != image.byteSize or hashlib.sha256(content).hexdigest() != image.sha256 or reply.sha256 != image.sha256:
                        raise ValueError("asset digest/size mismatch")
                    images[source.sourceId] = decodePng(content)
                    self.stats["decoded"] += 1
                except (grpc.RpcError, ValueError) as error:
                    failures[source.sourceId] = str(error)
                    self.stats["read_failed"] += 1
            with self.lock:
                applied = generation == self.generation and result.identity.resultOrdinal >= self.high.get(result.identity.resultScopeId, 0)
                decodedNs = time.perf_counter_ns()
                self.records.append({"key": result.identity.resultKey, "ordinal": result.identity.resultOrdinal,
                    "decoded": list(images), "failures": failures, "read_decode_ms": (time.monotonic() - started) * 1000,
                    "received_ns": receivedAt, "decoded_ns": decodedNs,
                    "model_ns": decodedNs if applied else None, "applied_to_live": applied,
                    "scope_ended_ns": result.timing.scopeEndedNs if result.timing else None,
                    "owner_age_at_send_ms": ownerAge,
                    "received_to_model_ms": (time.perf_counter_ns() - receivedAt) / 1e6})
                if not applied:
                    continue
                self.latest[result.identity.resultScopeId] = (result, images, failures)
                self.loading.pop(result.identity.resultScopeId, None)
                self.readyAt[result.identity.resultScopeId] = decodedNs
                self.revision += 1
                while sum(image.nbytes for _, frames, _ in self.latest.values() for image in frames.values()) > 16 * 1024 * 1024:
                    scope = next(key for key, (_, frames, _) in self.latest.items() if frames)
                    oldResult, oldFrames, oldFailures = self.latest[scope]
                    self.latest[scope] = (oldResult, {}, {**oldFailures, **{key: "CLIENT_BUDGET" for key in oldFrames}})
                callbacks = tuple(self.listeners)
            for callback in callbacks:
                try:
                    callback(result)
                except Exception as error:
                    self.errors.append(("OBSERVER_FAILED", str(error)))

    def selectJob(self, jobId):
        if not jobId:
            raise ValueError("an explicitly selected Job is required")
        with self.lock:
            self.jobId = jobId
            self.cursor = 0
            self.generation += 1
            self.latest.clear()
            self.high.clear()
            self.started.clear()
            self.seen.clear()
            self.loading.clear()
            self.readyAt.clear()
            self._connection("CONNECTING", "切换指定任务")
        if self.stream is not None:
            self.stream.cancel()

    def close(self):
        self.stop.set()
        if self.stream is not None:
            self.stream.cancel()
        pinError = None
        if self._pinStore is not None:
            try:
                self._pinStore.close()
            except RuntimeError as error:
                pinError = error
        self.channel.close()
        for thread in self.threads:
            thread.join(2)
            if thread.is_alive():
                raise RuntimeError("display client work has not stopped")
        with self.lock:
            self.listeners.clear()
            self.latest.clear()
            self.loading.clear()
            self.readyAt.clear()
            self._connection("CLOSED", "会话已关闭")
            while not self.pending.empty():
                self.pending.get_nowait()
        if pinError is not None:
            raise pinError
