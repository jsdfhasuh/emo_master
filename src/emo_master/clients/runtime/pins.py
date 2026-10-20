"""Finite, asynchronous view pins using only public typed asset lease RPCs."""
from dataclasses import dataclass, replace
from itertools import islice
import queue
from types import MappingProxyType
import threading
import time
from uuid import uuid4

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


@dataclass(frozen=True)
class PinView:
    state: str
    scope: object
    deadline: float
    error: str = ""


class _PinSuspended(Exception):
    pass


class PinStore:
    limit = 16 * 1024 * 1024

    def __init__(self, session):
        self.session = session
        self.lock = threading.RLock()
        self.entries = {}
        self.stop = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self._work, name="display-finite-pins")
        self.thread.start()

    def acquire(self, scope, generation, ttlMs=30000, *, sourceIds=None):
        if not 0 < ttlMs <= 30000:
            raise ValueError("租约必须为1—30000ms")
        size = sum({id(image): image.nbytes for image in scope.images.values()}.values())
        if sourceIds is not None:
            sourceIds = list(islice(iter(sourceIds), 129))
            if len(sourceIds) > 128:
                raise ValueError("PIN_BUDGET")
        requested = set(scope.images if sourceIds is None else sourceIds)
        wanted = frozenset(source.sourceId for source in scope.result.sources if source.sourceId in requested) | frozenset(scope.images)
        states = {**scope.imageStates, **{source.sourceId: "LOADING" for source in scope.result.sources
                  if source.image and source.sourceId in wanted and source.sourceId not in scope.images
                  and source.sourceId not in scope.failures}}
        scope = replace(scope, imageStates=MappingProxyType(states))
        with self.lock:
            if len(self.entries) >= 2 or self.bytesHeld() + size > self.limit or self.stop.is_set():
                raise ValueError("PIN_BUDGET")
            if generation != self.session.readSnapshot().generation:
                raise ValueError("会话已切换")
            key = str(uuid4())
            self.entries[key] = {"scope": scope, "generation": generation, "size": size,
                "deadline": time.monotonic() + ttlMs / 1000, "ttl": ttlMs,
                "state": "PENDING", "leases": [], "cancelled": False, "error": "",
                "wanted": wanted, "leaseReady": False, "decodeQueued": False, "decoding": False, "active": True}
            return key

    def read(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return PinView("EXPIRED", None, 0, "RESOURCE_EXPIRED")
            if entry["state"] == "FAILED" and time.monotonic() < entry["deadline"]:
                return PinView("FAILED", None, entry["deadline"], entry["error"])
            if not self._valid(entry):
                return PinView("EXPIRED", None, entry["deadline"], "RESOURCE_EXPIRED")
            return PinView(entry["state"], entry["scope"], entry["deadline"], entry["error"])

    def setActive(self, key, active):
        """GUI publishes visibility; the worker never reads a QWidget."""
        with self.lock:
            if key in self.entries:
                self.entries[key]["active"] = bool(active)

    def release(self, key):
        with self.lock:
            if key in self.entries:
                self.entries[key]["cancelled"] = True

    def bytesHeld(self):
        with self.lock:
            return sum(entry["size"] for entry in self.entries.values())

    def _work(self):
        while True:
            with self.lock:
                entries = list(self.entries.items())
                self.session._pinWaiting = any(entry["leaseReady"] and entry["state"] == "PENDING"
                    and entry["active"] and not entry["decodeQueued"] and self._valid(entry)
                    for _key, entry in entries)
            if self.stop.is_set() and not entries:
                return
            for key, entry in entries:
                stale = entry["generation"] != self.session.readSnapshot().generation
                if self.stop.is_set() or stale or entry["cancelled"] or time.monotonic() >= entry["deadline"]:
                    if entry["decoding"]:
                        continue
                    for lease in entry["leases"]:
                        try:
                            self.session.stub.ReleaseLease(lease, timeout=.5)
                        except Exception as error:
                            # Owner TTL remains bounded; failed explicit release is
                            # surfaced on close, never claimed to have succeeded.
                            self.errors.append(str(error))
                            self.errors[:] = self.errors[-16:]
                    with self.lock:
                        self.entries.pop(key, None)
                    continue
                if entry["state"] != "PENDING" or entry["decodeQueued"] or not entry["active"]:
                    continue
                if entry["leaseReady"]:
                    try:
                        with self.lock:
                            self.session.pending.put_nowait(("pin", key))
                            entry["decodeQueued"] = True
                    except queue.Full:
                        pass  # At most two finite entries retry; no extra queue.
                    continue
                try:
                    identity = entry["scope"].result.identity
                    leased = {lease.resource_id for lease in entry["leases"]}
                    for source in entry["scope"].result.sources:
                        with self.lock:
                            if not self._valid(entry) or not entry["active"]:
                                break
                        if (source.image is not None and source.sourceId in entry["wanted"]
                                and source.image.resourceId not in leased):
                            lease = self.session.stub.AcquireLease(pb.DisplayAssetRequest(
                                runtime_instance_id=identity.runtimeInstanceId, job_id=identity.jobId,
                                resource_id=source.image.resourceId, ttl_ms=entry["ttl"]), timeout=.5)
                            entry["leases"].append(lease)
                            leased.add(source.image.resourceId)
                    with self.lock:
                        if not self._valid(entry) or not entry["active"]:
                            continue
                        entry["leaseReady"] = True
                        if not any(source.image and source.sourceId in entry["wanted"]
                                   and source.sourceId not in entry["scope"].images
                                   and source.sourceId not in entry["scope"].failures
                                   for source in entry["scope"].result.sources):
                            entry["state"] = "PINNED"
                        elif self._valid(entry) and entry["active"]:
                            self.session._pinWaiting = True
                except Exception as error:
                    with self.lock:
                        entry["state"], entry["error"] = "FAILED", str(error)
                        entry["scope"], entry["size"] = None, 0
                        entry["cancelled"] = True
            self.stop.wait(.02) if not self.stop.is_set() else time.sleep(.005)

    def _valid(self, entry):
        return (not self.stop.is_set() and not entry["cancelled"] and time.monotonic() < entry["deadline"]
                and entry["generation"] == self.session.readSnapshot().generation)

    def _decode(self, key):
        """Runs only in the session's existing asset decoder, not this worker."""
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return
            if not self._valid(entry) or not entry["active"]:
                entry["decodeQueued"] = False
                return
            entry["decoding"] = True
            scope = entry["scope"]
        images, failures, states = dict(scope.images), dict(scope.failures), dict(scope.imageStates)
        assets = {(source.image.resourceId, source.image.sha256, source.image.byteSize): images[source.sourceId]
                  for source in scope.result.sources if source.image and source.sourceId in images}

        def reserve(size):
            # ReadAsset uses existing single-decoder scratch; reserve validated
            # decoded bytes atomically before OpenCV allocates, not 8 MiB guesses.
            with self.lock:
                if not self._valid(entry) or not entry["active"]:
                    raise _PinSuspended()
                if self.bytesHeld() + size > self.limit:
                    raise ValueError("PIN_BUDGET")
                entry["size"] += size
        try:
            for source in scope.result.sources:
                if (not source.image or source.sourceId not in entry["wanted"]
                        or source.sourceId in images or source.sourceId in failures):
                    continue
                with self.lock:
                    if not self._valid(entry) or not entry["active"]:
                        break
                image = source.image
                assetKey = (image.resourceId, image.sha256, image.byteSize)
                try:
                    if assetKey not in assets:
                        assets[assetKey] = self.session._readImage(scope.result, image, beforeDecode=reserve)
                    images[source.sourceId] = assets[assetKey]
                except _PinSuspended:
                    break
                except Exception as error:
                    failures[source.sourceId] = str(error)
                    self.session.stats["read_failed"] += 1
                states.pop(source.sourceId, None)
                with self.lock:
                    entry["size"] = sum({id(image): image.nbytes for image in images.values()}.values())
            with self.lock:
                if self._valid(entry):
                    entry["scope"] = replace(scope, images=MappingProxyType(images), failures=MappingProxyType(failures),
                                             imageStates=MappingProxyType(states), readyNs=time.perf_counter_ns())
                    entry["size"] = sum({id(image): image.nbytes for image in images.values()}.values())
                    entry["state"] = "PENDING" if any(source.image and source.sourceId in entry["wanted"]
                        and source.sourceId not in images and source.sourceId not in failures
                        for source in scope.result.sources) else "PINNED"
                else:
                    images.clear()
                    assets.clear()
        finally:
            with self.lock:
                entry["decoding"] = False
                entry["decodeQueued"] = False

    def close(self):
        self.stop.set()
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError("lease worker has not retired")
        if self.errors:
            raise RuntimeError("lease release failed; owner TTL still applies: " + self.errors[-1])
