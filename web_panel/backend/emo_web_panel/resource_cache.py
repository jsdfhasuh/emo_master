"""Bounded encoded-byte cache for the internal resource port, independent of SDK.

Callers authenticate and authorize before get(). Keys include runtime instance,
job, resource ID and SHA256, so restart/reused resource IDs cannot alias bytes.
No image conversion, filesystem paths, network credentials, or arbitrary RPCs.
"""
import asyncio
from collections import OrderedDict
import hashlib
import time


class ResourceCache:
    def __init__(self, max_bytes=16 * 1024 * 1024, max_item_bytes=4 * 1024 * 1024,
                 concurrency=4, ttl=30, max_pending=64, freeze_ttl=120, owner_bytes=8 * 1024 * 1024):
        self.max_bytes, self.max_item_bytes, self.ttl = max_bytes, max_item_bytes, ttl
        self.max_pending = max_pending
        self.freeze_ttl, self.owner_bytes = freeze_ttl, owner_bytes
        self.items, self.pending = OrderedDict(), {}
        self.bytes = 0
        self.gate = asyncio.Semaphore(concurrency)
        self.closed = False

    @staticmethod
    def key(instance, job, resource, sha256):
        if not all(isinstance(s, str) and s for s in (instance, job, resource, sha256)):
            raise ValueError('Complete resource identity required')
        if len(sha256) != 64 or any(c not in '0123456789abcdef' for c in sha256):
            raise ValueError('Resource SHA256 required')
        return instance, job, resource, sha256

    def _expire(self):
        now = time.monotonic()
        for key, item in list(self.items.items()):
            item['pins'] = {owner: until for owner, until in item['pins'].items() if until > now}
            if item['expires'] <= now and not item['pins']:
                self.bytes -= len(item['data'])
                del self.items[key]

    def pin(self, key, owner, ttl=None):
        """Retain only bytes already fetched for an exact identity, never newest.

        Repeated calls for the same owner do not extend its existing deadline.
        Caller releases this owner on resume, disconnect, target change or logout.
        """
        if not isinstance(owner, str) or not owner:
            raise ValueError('Resource owner required')
        self._expire()
        item = self.items.get(key)
        if item is None:
            raise KeyError('Exact frozen resource is no longer cached')
        held = sum(len(value['data']) for value in self.items.values() if owner in value['pins'])
        if owner not in item['pins'] and held + len(item['data']) > self.owner_bytes:
            raise ValueError('Frozen resource owner byte budget exceeded')
        duration = self.freeze_ttl if ttl is None else min(ttl, self.freeze_ttl)
        if duration <= 0:
            raise ValueError('Positive frozen resource TTL required')
        item['pins'].setdefault(owner, time.monotonic() + duration)
        return item['pins'][owner]

    def release_owner(self, owner):
        for item in self.items.values():
            item['pins'].pop(owner, None)
        self._expire()

    def peek(self, key):
        """Read retained bytes without network fallback, or report explicit expiry."""
        self._expire()
        if key not in self.items:
            raise KeyError('Exact resource expired or unavailable')
        return self.items[key]['data']

    async def get(self, key, loader):
        """Coalesce concurrent authenticated reads; loader returns encoded bytes."""
        if self.closed:
            raise RuntimeError('Resource cache closed')
        key = self.key(*key)
        self._expire()
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]['data']
        if key not in self.pending:
            if len(self.pending) >= self.max_pending:
                raise ValueError('Resource request capacity reached')
            task = asyncio.create_task(self._load(key, loader))
            self.pending[key] = task
            task.add_done_callback(lambda done: self._done(key, done))
        # One disconnected browser must not cancel a read shared with others.
        return await asyncio.shield(self.pending[key])

    def _done(self, key, task):
        if self.pending.get(key) is task:
            self.pending.pop(key, None)
        if not task.cancelled():
            task.exception()  # consume errors even if every waiter disconnected

    async def _load(self, key, loader):
        async with self.gate:
            data = await loader()
            if not isinstance(data, bytes) or len(data) > min(self.max_bytes, self.max_item_bytes):
                raise ValueError('Encoded resource byte budget exceeded')
            if hashlib.sha256(data).hexdigest() != key[3]:
                raise ValueError('Resource SHA256 mismatch')
            self._expire()
            while self.items and self.bytes + len(data) > self.max_bytes:
                victim = next((item_key for item_key, value in self.items.items() if not value['pins']), None)
                if victim is None:
                    raise ValueError('Resource cache budget occupied by frozen references')
                evicted = self.items.pop(victim)
                self.bytes -= len(evicted['data'])
            self.items[key] = {'data': data, 'expires': time.monotonic() + self.ttl, 'pins': {}}
            self.bytes += len(data)
            return data

    async def close(self):
        self.closed = True
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.items.clear()
        self.bytes = 0
