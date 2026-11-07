"""Asset EOF must not wait for another asset's filesystem operation.

The gates place an ordinary adoption exactly between the last data batch and
reader retirement. They do not change the 500 ms asset RPC deadline.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import grpc
import pytest

from prototypes.runtime_pages_p0.contracts import BUDGET
from prototypes.runtime_pages_p0.network import CancelContext, IsolatedServer
from tests.p0.test_asset_batches import asset as asset  # Reuse the real store/ledger fixture.


def blockedAdoption(assets, tmp_path, monkeypatch):
    source = tmp_path / "next-asset.png"
    data = b"next asset"
    source.write_bytes(data)
    entered, release = threading.Event(), threading.Event()
    replace = Path.replace

    def gatedReplace(path, destination):
        if path == source:
            entered.set()
            assert release.wait(10), "test did not release the adoption I/O gate"
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", gatedReplace)

    def adopt():
        return assets.adopt("job", "next", source, len(data),
                            hashlib.sha256(data).hexdigest(), (1, 1, 3))

    return adopt, entered, release


def testReaderRetiresAfterItsLastBytesWhileAnotherAssetIsBeingAdopted(
        asset, tmp_path, monkeypatch):
    resources, assets, reference, data = asset
    before = set(resources.tokens)
    reader = assets.readBatches(reference, CancelContext())
    item = assets.entries[reference["asset_id"]]
    adopt, entered, release = blockedAdoption(assets, tmp_path, monkeypatch)
    try:
        received = b""
        while len(received) < len(data):
            received += b"".join(next(reader))
        assert received == data
        assert item.readers == 1
        readerTokens = set(resources.tokens) - before
        assert len(readerTokens) == 1

        with ThreadPoolExecutor(max_workers=2) as workers:
            adoption = workers.submit(adopt)
            try:
                assert entered.wait(5), "adoption did not reach Path.replace"
                retirement = workers.submit(list, reader)
                try:
                    assert retirement.result(timeout=2) == []
                except FutureTimeout:
                    pytest.fail("all bytes were delivered, but reader EOF waits "
                                "for an unrelated asset's Path.replace")
                assert not release.is_set()
                assert item.readers == 0
                assert readerTokens.isdisjoint(resources.tokens)
                assert item.path.exists()
            finally:
                release.set()
                adoption.result(timeout=5)
    finally:
        reader.close()


def testTwoActualAssetRpcsFinishWithinOriginalDeadlineDuringOtherAdoption(
        asset, tmp_path, monkeypatch):
    _resources, assets, reference, data = asset
    adopt, entered, release = blockedAdoption(assets, tmp_path, monkeypatch)
    tails = threading.Barrier(3)  # Two final batches, then the unrelated adopter.
    native = assets.readBatches

    def batches(request, context):
        reader = native(request, context)
        delivered = 0
        try:
            for batch in reader:
                delivered += sum(map(len, batch))
                yield batch
                if delivered == len(data):
                    tails.wait(timeout=5)
                    assert entered.wait(5), "adoption did not reach Path.replace"
        finally:
            reader.close()

    monkeypatch.setattr(assets, "readBatches", batches)

    class Legacy:
        def __getattr__(self, _name):
            return lambda *_args: None

    display = SimpleNamespace(assets=assets, snapshot_json=lambda *_: {},
                              follow=lambda *_: iter(()))
    server = IsolatedServer(Legacy(), display)
    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    try:
        grpc.channel_ready_future(channel).result(5)
        rpc = channel.unary_stream("/p0.Display/GetDisplayAsset",
                                   request_serializer=lambda value: json.dumps(value).encode())

        def receive():
            chunks = []
            try:
                chunks.extend(rpc(reference, timeout=BUDGET.read_seconds))
            except grpc.RpcError as error:
                return b"".join(chunks), error.code()
            return b"".join(chunks), None

        def adoptAfterTails():
            tails.wait(timeout=5)
            return adopt()

        with ThreadPoolExecutor(max_workers=3) as workers:
            adoption = workers.submit(adoptAfterTails)
            readers = [workers.submit(receive) for _ in range(2)]
            try:
                results = [reader.result(timeout=5) for reader in readers]
                assert [content for content, _status in results] == [data, data]
                statuses = [status for _content, status in results]
                assert statuses == [None, None], (
                    "both RPCs received every byte, but EOF waited for an "
                    f"unrelated asset's Path.replace: {statuses}")
                assert BUDGET.read_seconds == .5
                assert not release.is_set()
                assert assets.entries[reference["asset_id"]].readers == 0
            finally:
                release.set()
                adoption.result(timeout=5)
    finally:
        release.set()
        channel.close()
        server.close()
