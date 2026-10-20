"""Result-owned bounded disk assets; leases and active reads prevent eviction."""
from collections import OrderedDict
import hashlib
from pathlib import Path
import threading
import time
from uuid import uuid4


class AssetStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.items: OrderedDict = OrderedDict()
        self.leases: dict = {}
        self.readers: dict = {}
        self.retained: set = set()

    def _expire(self):
        now = time.monotonic()
        for key, (_resource, deadline) in list(self.leases.items()):
            if deadline <= now:
                del self.leases[key]

    def _collect(self):
        self._expire()
        pinned = {value[0] for value in self.leases.values()} | set(self.readers)
        for key, item in list(self.items.items()):
            if item["owner"] not in self.retained and key not in pinned:
                item["path"].unlink(missing_ok=True)
                del self.items[key]

    def adopt(self, staging, jobId, resultKey, provenance):
        with self.lock:
            self._collect()
            size = staging.stat().st_size
            if not 0 < size <= 8 * 1024 * 1024 or sum(i["size"] for i in self.items.values()) + size > 256 * 1024 * 1024:
                raise ValueError("asset cache budget exceeded")
            resourceId = str(uuid4())
            # Hash streaming: no extra full encoded-image copy in the owner process.
            digest = hashlib.sha256()
            with staging.open("rb") as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    digest.update(block)
            path = self.root / resourceId
            staging.replace(path)
            self.items[resourceId] = {"path": path, "size": size, "job": jobId, "owner": resultKey, "sha": digest.hexdigest()}
            self.retained.add(resultKey)
            return {"resourceId": resourceId, "ownerResultKey": resultKey, "byteSize": size,
                    "sha256": digest.hexdigest(), "mimeType": "image/png", "provenance": provenance}

    def retain(self, keys):
        with self.lock:
            self.retained = set(keys)
            self._collect()

    def read(self, jobId, resourceId):
        started = time.monotonic()
        with self.lock:
            self._collect()
            item = self.items[resourceId]
            if item["job"] != jobId:
                raise KeyError("resource does not belong to Job")
            if sum(self.readers.values()) >= 2:
                raise ValueError("asset read quota exceeded")
            self.readers[resourceId] = self.readers.get(resourceId, 0) + 1
        try:
            data = item["path"].read_bytes()
            if time.monotonic() - started > .5:
                raise TimeoutError("asset read deadline")
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha"]:
                raise ValueError("asset integrity error")
            return data, item["sha"]
        finally:
            with self.lock:
                self.readers[resourceId] -= 1
                if not self.readers[resourceId]:
                    del self.readers[resourceId]
                self._collect()

    def lease(self, jobId, resourceId, ttlMs):
        with self.lock:
            self._collect()
            item = self.items[resourceId]
            if item["job"] != jobId:
                raise KeyError("resource does not belong to Job")
            pinned = {value[0] for value in self.leases.values()} | {resourceId}
            if (len(self.leases) >= 16 or not 0 < ttlMs <= 30000
                    or sum(self.items[key]["size"] for key in pinned) > 64 * 1024 * 1024):
                raise ValueError("lease budget exceeded")
            key = str(uuid4())
            self.leases[key] = (resourceId, time.monotonic() + ttlMs / 1000)
            return key

    def release(self, leaseId):
        with self.lock:
            self.leases.pop(leaseId, None)
            self._collect()

    def stats(self):
        with self.lock:
            self._collect()
            return {"cache_bytes": sum(i["size"] for i in self.items.values()),
                    "lease_handles": len(self.leases), "readers": sum(self.readers.values()), "assets": len(self.items)}

    def close(self):
        with self.lock:
            if self.readers:
                raise RuntimeError("cannot close while reads still own resources")
            self.leases.clear()
            self.retained.clear()
            self._collect()
