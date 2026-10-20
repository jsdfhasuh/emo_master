"""Bounded wire batching keeps the original chunks, deadline and ownership."""
import hashlib
from types import SimpleNamespace

import grpc
import json
import pytest

from prototypes.runtime_pages_p0.contracts import BUDGET
from prototypes.runtime_pages_p0.network import CancelContext, IsolatedServer
from prototypes.runtime_pages_p0.resources import Assets, Resources


@pytest.fixture
def asset(tmp_path):
    resources = Resources()
    assets = Assets(tmp_path / "cache", resources)
    data = bytes(range(256)) * (6 * 256) + b"tail"
    path = tmp_path / "source.bin"
    path.write_bytes(data)
    reference = assets.adopt("job", "result", path, len(data), hashlib.sha256(data).hexdigest(), (1, 1, 3))
    try:
        yield resources, assets, reference, data
    finally:
        assets.close()
    assert not resources.tokens and not any(resources.used.values())


def testBatchRetainsReaderAndChargedMemoryThroughTheFinalPartialYield(asset):
    resources, assets, reference, data = asset
    before = resources.used["memory"]
    reader = assets.readBatches(reference, CancelContext())
    try:
        first = next(reader)
        assert isinstance(first, tuple) and len(first) == 4
        assert all(len(chunk) == 64 * 1024 for chunk in first)
        assert resources.used["memory"] - before == 3 * 4 * 64 * 1024
        tail = next(reader)
        assert b"".join(first + tail) == data and len(tail) == 3
        assert assets.entries[reference["asset_id"]].readers == 1
        assert resources.used["memory"] - before == 3 * 4 * 64 * 1024
        with pytest.raises(StopIteration):
            next(reader)
    finally:
        reader.close()
    assert resources.used["memory"] == before
    assert assets.entries[reference["asset_id"]].readers == 0


def testBatchChecksTheOriginalDeadlineBeforeEveryChunk(asset, monkeypatch):
    from prototypes.runtime_pages_p0 import resources as module
    resources, assets, reference, _ = asset
    item = assets.entries[reference["asset_id"]]
    native = item.path
    elapsed = [0.0]
    reads = []

    class Handle:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size):
            reads.append(size)
            elapsed[0] += .3
            return self.stream.read(size)

    def openPath(mode):
        handle = Handle()
        handle.stream = native.open(mode)
        return handle

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    item.path = SimpleNamespace(open=openPath)
    before = resources.used["memory"]
    reader = assets.readBatches(reference, CancelContext())
    try:
        with pytest.raises(TimeoutError, match="asset read deadline"):
            next(reader)
        assert reads == [64 * 1024, 64 * 1024]
        assert elapsed[0] > BUDGET.read_seconds == .5
        assert resources.used["memory"] == before
        assert item.readers == 0
    finally:
        reader.close()
        item.path = native


def testBatchCancellationReleasesItsReaderWithoutReadingAgain(asset):
    resources, assets, reference, _ = asset
    context = CancelContext()
    before = resources.used["memory"]
    reader = assets.readBatches(reference, context)
    assert len(next(reader)) == 4
    context.cancelled.set()
    with pytest.raises(StopIteration):
        next(reader)
    assert resources.used["memory"] == before
    assert assets.entries[reference["asset_id"]].readers == 0


@pytest.mark.parametrize("custom", [None, "instance", "class"])
def testActualRpcKeeps64KiBChunksAndUsesBoundedAssetBatches(asset, monkeypatch, custom):
    resources, assets, reference, data = asset
    calls = []
    native = assets.readBatches

    def batches(*args):
        calls.append("batches")
        yield from native(*args)

    if custom == "instance":
        nativeRead = assets.read

        def read(*args):
            calls.append("custom-read")
            yield from nativeRead(*args)

        monkeypatch.setattr(assets, "read", read)
    elif custom == "class":
        nativeRead = Assets.read

        def read(self, *args):
            calls.append("custom-read")
            yield from nativeRead(self, *args)

        monkeypatch.setattr(Assets, "read", read)
    else:
        monkeypatch.setattr(assets, "readBatches", batches)

    class Legacy:
        def __getattr__(self, _name):
            return lambda *_args: None

    display = SimpleNamespace(assets=assets, snapshot_json=lambda *_: {}, follow=lambda *_: iter(()))
    server = IsolatedServer(Legacy(), display)
    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        grpc.channel_ready_future(channel).result(5)
        rpc = channel.unary_stream("/p0.Display/GetDisplayAsset", request_serializer=lambda value: json.dumps(value).encode())
        chunks = list(rpc(reference, timeout=BUDGET.read_seconds))
        assert b"".join(chunks) == data
        assert [len(chunk) for chunk in chunks] == [64 * 1024] * 6 + [4]
        assert calls == (["custom-read"] if custom else ["batches"])
    finally:
        channel.close()
        server.close()
    assert assets.entries[reference["asset_id"]].readers == 0
