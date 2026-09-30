"""Finite, asynchronous view pins using only public typed asset lease RPCs."""
from dataclasses import dataclass
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

    def acquire(self, scope, generation, ttlMs=30000):
        if not 0 < ttlMs <= 30000:
            raise ValueError("租约必须为1—30000ms")
        size = sum({id(image): image.nbytes for image in scope.images.values()}.values())
        with self.lock:
            if len(self.entries) >= 2 or self.bytesHeld() + size > self.limit or self.stop.is_set():
                raise ValueError("PIN_BUDGET")
            if generation != self.session.readSnapshot().generation:
                raise ValueError("会话已切换")
            key = str(uuid4())
            self.entries[key] = {"scope": scope, "generation": generation, "size": size,
                "deadline": time.monotonic() + ttlMs / 1000, "ttl": ttlMs,
                "state": "PENDING", "leases": [], "cancelled": False, "error": ""}
            return key

    def read(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry is None:
                return PinView("EXPIRED", None, 0, "RESOURCE_EXPIRED")
            if time.monotonic() >= entry["deadline"]:
                return PinView("EXPIRED", None, entry["deadline"], "RESOURCE_EXPIRED")
            return PinView(entry["state"], entry["scope"], entry["deadline"], entry["error"])

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
            if self.stop.is_set() and not entries:
                return
            for key, entry in entries:
                stale = entry["generation"] != self.session.readSnapshot().generation
                if self.stop.is_set() or stale or entry["cancelled"] or time.monotonic() >= entry["deadline"]:
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
                if entry["state"] != "PENDING":
                    continue
                try:
                    identity = entry["scope"].result.identity
                    leased = set()
                    for source in entry["scope"].result.sources:
                        if (source.image is not None and source.sourceId in entry["scope"].images
                                and source.image.resourceId not in leased):
                            lease = self.session.stub.AcquireLease(pb.DisplayAssetRequest(
                                runtime_instance_id=identity.runtimeInstanceId, job_id=identity.jobId,
                                resource_id=source.image.resourceId, ttl_ms=entry["ttl"]), timeout=.5)
                            entry["leases"].append(lease)
                            leased.add(source.image.resourceId)
                    with self.lock:
                        entry["state"] = "PINNED"
                except Exception as error:
                    with self.lock:
                        entry["state"], entry["error"] = "FAILED", str(error)
                        entry["scope"], entry["size"] = None, 0
                        entry["cancelled"] = True
            self.stop.wait(.02) if not self.stop.is_set() else time.sleep(.005)

    def close(self):
        self.stop.set()
        self.thread.join(3)
        if self.thread.is_alive():
            raise RuntimeError("lease worker has not retired")
        if self.errors:
            raise RuntimeError("lease release failed; owner TTL still applies: " + self.errors[-1])
