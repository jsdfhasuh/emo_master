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


def pngDecodedBytes(content):
    """Bound the OpenCV output from PNG metadata before allocating pixels."""
    if (len(content) < 33 or content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR"
            or int.from_bytes(content[8:12], "big") != 13):
        raise ValueError("invalid PNG header")
    width, height = int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    # OpenCV expands grayscale+alpha to BGRA, not a two-channel array.
    channels = {0: 1, 2: 3, 4: 4, 6: 4}.get(content[25], 0)
    offset = 33
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset:offset + 4], "big")
        kind = content[offset + 4:offset + 8]
        if kind == b"tRNS" and content[25] == 2:
            channels = 4
        if kind == b"IDAT":
            break
        offset += length + 12
    size = width * height * channels
    if content[24] != 8 or channels == 0 or not width or not height or not 0 < size <= 8 * 1024 * 1024:
        raise ValueError("decoded image budget exceeded")
    return size


def decodePng(content):
    import cv2
    import numpy as np
    size = pngDecodedBytes(content)
    pixels = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)
    if pixels is None or pixels.nbytes > size:
        raise ValueError("image decoding failed or exceeded budget")
    # Immutable bytes prevent observers from enabling writes again.
    return np.frombuffer(pixels.tobytes(), dtype=pixels.dtype).reshape(pixels.shape)


class DisplaySession:
    def __init__(self, address, jobId, *, imageDemand=False):
        if not jobId:
            raise ValueError("an explicitly selected Job is required")
        self.channel = grpc.insecure_channel(address, options=[("grpc.max_receive_message_length", 9 * 1024 * 1024)])
        self.stub = rpc.DisplayServiceStub(self.channel)
        self.jobId = jobId
        self.instanceId = ""
        self.cursor = 0
        self._acceptedCursor = 0
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.pending = queue.Queue(maxsize=8)
        # Local GUI interest only. The default retains P2/P3 headless decoding.
        self.imageDemand = bool(imageDemand)
        self._imageConsumers = {}
        self._wantedImages = frozenset() if self.imageDemand else None
        self._scheduled = set()
        self._pinWaiting = False
        self.imageStates = {}
        self._receivedAt = {}
        self.latest = {}
        self.high = {}
        self.started = {}
        self.expired = {}
        self.seen = deque(maxlen=32)
        self.records = deque(maxlen=128)
        self.errors = deque(maxlen=64)
        self.stats = {"received": 0, "decoded": 0, "dropped": 0, "read_failed": 0, "resets": 0,
                      "expired_scopes": 0}
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
                       MappingProxyType(dict(errors)), self.readyAt.get(key, 0),
                       MappingProxyType(dict(self.imageStates.get(key, {}))))
                      for key, (result, images, errors) in self.latest.items()}
            return SessionView(self.revision, self.generation, self.instanceId, self.jobId,
                self.connection, self.connectionDetail, MappingProxyType(scopes),
                MappingProxyType(dict(self.loading)), MappingProxyType(dict(self.started)),
                MappingProxyType({scope: ordinal for scope, ordinal in self.expired.items()
                                  if self.high.get(scope, 0) <= ordinal}))

    def setImageDemand(self, consumerId, sourceIds):
        """Replace one local consumer's interest; None explicitly requests all.

        No RPC or Qt calls. Eight owners / 128 IDs each bound bookkeeping, not
        capture permission: only sources already in ClosedResult can be read.
        Independent consumers are unioned; defaults never suspend headless work.
        """
        if not self.imageDemand:
            return
        if not isinstance(consumerId, str) or not 0 < len(consumerId) <= 160:
            raise ValueError("invalid image demand owner")
        if sourceIds is not None:
            from itertools import islice
            if isinstance(sourceIds, str):
                raise ValueError("image demand requires source IDs")
            sources = list(islice(iter(sourceIds), 129))
            if len(sources) > 128 or any(not isinstance(key, str) or not 0 < len(key) <= 160 for key in sources):
                raise ValueError("IMAGE_DEMAND_BUDGET")
            sourceIds = frozenset(sources)
        with self.lock:
            if consumerId not in self._imageConsumers and len(self._imageConsumers) >= 8:
                raise ValueError("IMAGE_DEMAND_BUDGET")
            if consumerId in self._imageConsumers and self._imageConsumers[consumerId] == sourceIds:
                return
            self._imageConsumers[consumerId] = sourceIds
            self._refreshImageDemand()

    def removeImageDemand(self, consumerId):
        with self.lock:
            if consumerId in self._imageConsumers:
                del self._imageConsumers[consumerId]
                self._refreshImageDemand()

    def _refreshImageDemand(self):
        values = tuple(self._imageConsumers.values())
        self._wantedImages = None if any(value is None for value in values) else frozenset().union(*values)
        self._enqueueDemand()

    def _wantsImage(self, sourceId):
        return self._wantedImages is None or sourceId in self._wantedImages

    def _enqueueResult(self, result, receivedAt, ownerAge):
        key = (self.generation, result.identity.resultKey)
        if key in self._scheduled:
            return True
        # A lease-ready finite pin gets the next free queue slot under sustained
        # live traffic. Metadata retries still use the authoritative snapshot.
        if self._pinWaiting and self.pending.qsize() >= self.pending.maxsize - 1:
            return False
        try:
            self.pending.put_nowait((self.generation, result, receivedAt, ownerAge))
        except queue.Full:
            return False
        self._scheduled.add(key)
        return True

    def _enqueueDemand(self):
        if not self.imageDemand or self.stop.is_set():
            return
        for scopeId, (result, images, failures) in self.latest.items():
            states = {source.sourceId: "LOADING" if self._wantsImage(source.sourceId) else "NOT_REQUESTED"
                      for source in result.sources if source.image is not None
                      and source.sourceId not in images and source.sourceId not in failures}
            if states != self.imageStates.get(scopeId, {}):
                self.imageStates[scopeId] = states
                self.revision += 1
            if "LOADING" in states.values():
                receivedAt, ownerAge = self._receivedAt[scopeId]
                self._enqueueResult(result, receivedAt, ownerAge)

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
            if not changed and snapshot.cursor < self._acceptedCursor:
                return  # A delayed reset from the same generation is stale too.
            if changed or snapshot.reset_required:
                self.generation += 1
                self.latest.clear()
                self.high.clear()
                self.started.clear()
                self.expired.clear()
                self.seen.clear()
                self.loading.clear()
                self.readyAt.clear()
                self.imageStates.clear()
                self._receivedAt.clear()
                self.stats["resets"] += 1
            self.instanceId, self.cursor = snapshot.runtime_instance_id, snapshot.cursor
            self._acceptedCursor = snapshot.cursor
            for scope, ordinal in snapshot.latest_started_ordinals.items():
                self.started[scope] = max(ordinal, self.started.get(scope, 0))
            for scope, ordinal in snapshot.expired_scope_ordinals.items():
                if ordinal > self.expired.get(scope, 0):
                    self.stats["expired_scopes"] += 1
                self.expired[scope] = max(ordinal, self.expired.get(scope, 0))
                if self.high.get(scope, 0) <= ordinal:
                    self.high[scope] = ordinal
                    self.latest.pop(scope, None)
                    self.loading.pop(scope, None)
                    self.readyAt.pop(scope, None)
                    self.imageStates.pop(scope, None)
                    self._receivedAt.pop(scope, None)
            for wire in snapshot.results:
                result = decodeResult(wire)
                if (result.identity.jobId != self.jobId or result.identity.runtimeInstanceId != self.instanceId):
                    raise ValueError("result belongs to another Runtime/Job")
                if (result.identity.resultKey in self.seen
                        or result.identity.resultOrdinal <= self.expired.get(result.identity.resultScopeId, 0)):
                    continue
                scope = result.identity.resultScopeId
                if result.identity.resultOrdinal > self.high.get(scope, 0):
                    self.high[scope] = result.identity.resultOrdinal
                    # An actual new closed result invalidates the preceding image,
                    # including when this result is incomplete/failed.
                    self.latest.pop(scope, None)
                    self.imageStates.pop(scope, None)
                    self._receivedAt.pop(scope, None)
                    self.loading[scope] = result
                self.stats["received"] += 1
                if self._enqueueResult(result, time.perf_counter_ns(), wire.owner_age_ms):
                    # A rejected final notification must remain eligible for
                    # the authoritative health snapshot to retry delivery.
                    self.seen.append(result.identity.resultKey)
                else:
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
                        self.expired.clear()
                        self.seen.clear()
                        self.loading.clear()
                        self.readyAt.clear()
                        self.imageStates.clear()
                        self._receivedAt.clear()
                        self.cursor = 0
                        self._acceptedCursor = 0
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

    def _readImage(self, result, image, *, beforeDecode=None):
        reply = self.stub.ReadAsset(pb.DisplayAssetRequest(runtime_instance_id=result.identity.runtimeInstanceId,
            job_id=result.identity.jobId, resource_id=image.resourceId), timeout=.5)
        content = reply.content
        if len(content) != image.byteSize or hashlib.sha256(content).hexdigest() != image.sha256 or reply.sha256 != image.sha256:
            raise ValueError("asset digest/size mismatch")
        if beforeDecode is not None:
            beforeDecode(pngDecodedBytes(content))
        pixels = decodePng(content)
        self.stats["decoded"] += 1
        return pixels

    def _decode(self):
        while not self.stop.is_set():
            try:
                task = self.pending.get(timeout=.05)
            except queue.Empty:
                continue
            # Frozen reads share this queue and decoder, never an extra worker.
            if len(task) == 2:
                self._pinStore._decode(task[1])
                with self.lock:
                    self._enqueueDemand()
                continue
            generation, result, receivedAt, ownerAge = task
            try:
                self._decodeLive(generation, result, receivedAt, ownerAge)
            finally:
                with self.lock:
                    self._scheduled.discard((generation, result.identity.resultKey))
                    self._enqueueDemand()

    def _decodeLive(self, generation, result, receivedAt, ownerAge):
        scopeId = result.identity.resultScopeId
        with self.lock:
            if generation != self.generation or result.identity.jobId != self.jobId:
                return
            existing = self.latest.get(scopeId)
            sameResult = existing is not None and existing[0].identity.resultKey == result.identity.resultKey
            images = dict(existing[1]) if sameResult else {}
            failures = dict(existing[2]) if sameResult else {}
        decodedAssets = {(source.image.resourceId, source.image.sha256, source.image.byteSize): images[source.sourceId]
                         for source in result.sources if source.image is not None and source.sourceId in images}
        started = time.monotonic()
        for source in result.sources:
            if source.image is None or source.sourceId in images or source.sourceId in failures:
                continue
            with self.lock:
                if self.imageDemand and (not self._wantsImage(source.sourceId)
                        or generation != self.generation or result.identity.resultOrdinal < self.high.get(scopeId, 0)
                        or result.identity.resultOrdinal <= self.expired.get(scopeId, 0)):
                    continue
            try:
                image = source.image
                assetKey = (image.resourceId, image.sha256, image.byteSize)
                if assetKey not in decodedAssets:
                    decodedAssets[assetKey] = self._readImage(result, image)
                images[source.sourceId] = decodedAssets[assetKey]
            except (grpc.RpcError, ValueError) as error:
                failures[source.sourceId] = str(error)
                self.stats["read_failed"] += 1
        with self.lock:
            applied = (generation == self.generation
                and result.identity.resultOrdinal >= self.high.get(scopeId, 0)
                and result.identity.resultOrdinal > self.expired.get(scopeId, 0))
            decodedNs = time.perf_counter_ns()
            self.records.append({"key": result.identity.resultKey, "ordinal": result.identity.resultOrdinal,
                "decoded": list(images), "failures": failures, "read_decode_ms": (time.monotonic() - started) * 1000,
                "received_ns": receivedAt, "decoded_ns": decodedNs,
                "model_ns": decodedNs if applied else None, "applied_to_live": applied,
                "scope_ended_ns": result.timing.scopeEndedNs if result.timing else None,
                "owner_age_at_send_ms": ownerAge,
                "received_to_model_ms": (time.perf_counter_ns() - receivedAt) / 1e6})
            if not applied:
                return
            self.latest[scopeId] = (result, images, failures)
            self.loading.pop(scopeId, None)
            self.readyAt[scopeId] = decodedNs
            self._receivedAt[scopeId] = (receivedAt, ownerAge)
            self.imageStates[scopeId] = {source.sourceId: "NOT_REQUESTED" for source in result.sources
                if source.image is not None and source.sourceId not in images and source.sourceId not in failures}
            self.revision += 1
            while sum({id(image): image.nbytes for _, frames, _ in self.latest.values()
                       for image in frames.values()}.values()) > 16 * 1024 * 1024:
                scope = next(key for key, (_, frames, _) in self.latest.items() if frames)
                oldResult, oldFrames, oldFailures = self.latest[scope]
                self.latest[scope] = (oldResult, {}, {**oldFailures, **{key: "CLIENT_BUDGET" for key in oldFrames}})
            # Loading another image of an already published result changes the
            # view revision, not the formal-result observer stream.
            callbacks = () if self.imageDemand and sameResult else tuple(self.listeners)
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
            self._acceptedCursor = 0
            self.generation += 1
            self.latest.clear()
            self.high.clear()
            self.started.clear()
            self.expired.clear()
            self.seen.clear()
            self.loading.clear()
            self.readyAt.clear()
            self.imageStates.clear()
            self._receivedAt.clear()
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
            self.imageStates.clear()
            self._receivedAt.clear()
            self._imageConsumers.clear()
            self._scheduled.clear()
            self._connection("CLOSED", "会话已关闭")
            while not self.pending.empty():
                self.pending.get_nowait()
        if pinError is not None:
            raise pinError
