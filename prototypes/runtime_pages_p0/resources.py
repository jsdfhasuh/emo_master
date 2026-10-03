"""One ownership ledger for the P0 pipeline, including disk and reader leases."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from uuid import uuid4

from .contracts import BUDGET, Unavailable


class Resources:
    def __init__(self):
        self.lock = threading.RLock()
        self.tokens = {}
        self.used = dict(memory=0, staging=0, cache=0, lease=0)
        self.peak = dict(self.used)
        self.jobs = {}
        self.limits = dict(memory=BUDGET.runtime_memory, staging=BUDGET.staging_disk,
                           cache=BUDGET.cache_disk, lease=BUDGET.lease_disk)

    def reserve(self, job, kind, size):
        with self.lock:
            job_memory = self.jobs.get(job, 0)
            if size < 0 or self.used[kind] + size > self.limits[kind]:
                raise Unavailable(f"runtime {kind} quota")
            if kind == "memory" and job_memory + size > BUDGET.job_memory:
                raise Unavailable("job memory quota")
            token = str(uuid4())
            self.tokens[token] = (job, kind, size)
            self.used[kind] += size
            self.peak[kind] = max(self.peak[kind], self.used[kind])
            if kind == "memory":
                self.jobs[job] = job_memory + size
            return token

    def release(self, token):
        with self.lock:
            job, kind, size = self.tokens.pop(token)
            self.used[kind] -= size
            if kind == "memory":
                self.jobs[job] -= size
                if not self.jobs[job]:
                    del self.jobs[job]

    def snapshot(self):
        with self.lock:
            return dict(used=dict(self.used), peak=dict(self.peak), jobs=dict(self.jobs), owners=len(self.tokens))


@dataclass
class Asset:
    asset_id: str
    job: str
    result_key: str
    path: Path
    size: int
    sha256: str
    shape: tuple
    tokens: tuple
    readers: int = 0


class Assets:
    def __init__(self, root, resources):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.resources = resources
        self.lock = threading.RLock()
        self.entries = OrderedDict()
        self.latest = {}
        self.leases = {}
        self.lease_tokens = {}
        self.evictions = 0
        self.closed = False

    def _expire(self):
        now = time.monotonic()
        for lease, (asset_id, deadline) in list(self.leases.items()):
            if now >= deadline:
                self.unpin(lease)

    def _delete(self, asset_id):
        asset = self.entries[asset_id]
        assert asset.readers == 0 and asset_id not in self.lease_tokens
        asset.path.unlink(missing_ok=True)  # actual resource first, accounting second
        del self.entries[asset_id]
        for token in asset.tokens:
            self.resources.release(token)
        self.evictions += 1

    def _evict(self, needed=0, count=False):
        self._expire()
        for asset_id in list(self.entries):
            enough = self.resources.used["cache"] + needed <= BUDGET.cache_disk
            if enough and (not count or len(self.entries) < BUDGET.history):
                return
            entry = self.entries[asset_id]
            if entry.readers or asset_id in self.lease_tokens or asset_id in self.latest.values():
                continue
            self._delete(asset_id)

    def adopt(self, job, key, path, size, digest, shape):
        with self.lock:
            if self.closed:
                raise Unavailable("asset store closed")
            self._evict(size, count=True)
            if len(self.entries) >= BUDGET.history:
                raise Unavailable("asset count quota")
            disk = self.resources.reserve(job, "cache", size)
            memory = None
            asset_id = str(uuid4())
            destination = self.root / f"{asset_id}.png"
            try:
                memory = self.resources.reserve(job, "memory", 4096)
                Path(path).replace(destination)  # outside Job/export workspace
                asset = Asset(asset_id, job, key, destination, size, digest, tuple(shape), (disk, memory))
                self.entries[asset_id] = asset
                return dict(asset_id=asset_id, job=job, result_key=key, size=size, sha256=digest, shape=shape)
            except BaseException:
                destination.unlink(missing_ok=True)
                self.resources.release(disk)
                if memory:
                    self.resources.release(memory)
                raise

    def set_latest(self, job, asset_id):
        with self.lock:
            if asset_id:
                self.latest[job] = asset_id
            else:
                self.latest.pop(job, None)

    def pin(self, asset_id, seconds=BUDGET.lease_seconds):
        with self.lock:
            self._expire()
            if not 0 < seconds <= BUDGET.lease_seconds or len(self.leases) >= 16:
                raise Unavailable("lease duration/count quota")
            asset = self.entries.get(asset_id)
            if asset is None:
                raise KeyError("expired asset")
            if asset_id not in self.lease_tokens:
                self.lease_tokens[asset_id] = self.resources.reserve(asset.job, "lease", asset.size)
            lease = str(uuid4())
            self.leases[lease] = (asset_id, time.monotonic() + seconds)
            return lease

    def unpin(self, lease):
        with self.lock:
            entry = self.leases.pop(lease, None)
            if entry and not any(value[0] == entry[0] for value in self.leases.values()):
                self.resources.release(self.lease_tokens.pop(entry[0]))

    def read(self, request, context):
        asset_id = request["asset_id"]
        with self.lock:
            self._expire()
            asset = self.entries.get(asset_id)
            if asset is None or asset.job != request["job"]:
                raise KeyError("expired or wrong-job asset")
            token = self.resources.reserve(asset.job, "memory", 3 * 64 * 1024)
            asset.readers += 1
        deadline = time.monotonic() + BUDGET.read_seconds
        try:
            with asset.path.open("rb") as handle:
                while context.is_active():
                    if time.monotonic() > deadline:
                        raise TimeoutError("asset read deadline")
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        return
                    yield chunk
        finally:
            with self.lock:
                asset.readers -= 1
                self.resources.release(token)

    def close(self):
        with self.lock:
            self.closed = True
            self.latest.clear()
            for lease in list(self.leases):
                self.unpin(lease)
            for asset_id in list(self.entries):
                self._delete(asset_id)
