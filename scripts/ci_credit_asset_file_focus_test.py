"""Hosted ReadAsset file-boundary diagnostic; not product Credit acceptance."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import zlib

import grpc
import numpy as np
import pytest

from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.presentation.assets import AssetStore
from scripts.r3_credit_file_subphases import FileSubphases


class NonDisplayRuntime:
    def __getattr__(self, name):
        return lambda _request, _context: None


def png(seed):
    pixels = np.random.default_rng(seed).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)
    data = b"".join(b"\0" + row.tobytes() for row in pixels)
    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body)))
    outcome = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1920, 1080, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(data, level=1)) + chunk(b"IEND", b""))
    assert 5 * 1024 * 1024 <= len(outcome) < 8 * 1024 * 1024
    return outcome


def testOriginalReadAssetFilePhasesWithoutClientPngDecode(tmp_path):
    destination = os.environ.get("EMO_CI_ASSET_FILE_PHASES")
    if not destination:
        pytest.skip("specific Windows focus runner only")
    assets = AssetStore(tmp_path / "assets")
    server = AioRuntimeServer(NonDisplayRuntime(), SimpleNamespace(runtimeInstanceId="file-focus", assets=assets))
    channels = [grpc.insecure_channel(f"127.0.0.1:{server.port}",
                options=[("grpc.max_receive_message_length", 9 * 1024 * 1024)]) for _ in range(2)]
    probe = FileSubphases(limit=256)
    traces = []
    meta = {}
    counts = {}
    sequence = threading.Lock()
    completed = 0
    original_read = Path.read_bytes

    def read_bytes(path):
        # Only objects installed by the real AssetStore's adopt operation are
        # observed. No other reads, filenames or contents are recorded.
        metadata = meta.get(id(path))
        if metadata is None:
            return original_read(path)
        with sequence:
            next_sequence = counts.get(metadata, 0) + 1
            counts[metadata] = next_sequence
        with probe.armed(path, f"{metadata[0]}-{metadata[1]}-{next_sequence}"):
            return original_read(path)

    def request(asset, job="file-focus-job"):
        return pb.DisplayAssetRequest(runtime_instance_id="file-focus",
                    job_id=job, resource_id=asset["resourceId"], ttl_ms=20_000)

    try:
        for ch in channels:
            grpc.channel_ready_future(ch).result(timeout=5)
        clients = [rpc.DisplayServiceStub(ch) for ch in channels]
        with patch.object(Path, "open", probe.wrap_open(Path.open)), patch.object(Path, "read_bytes", read_bytes):
            for asset_index in range(1, 4):
                content = png(20260926 + asset_index)
                stage = tmp_path / f"staging-{asset_index}"
                stage.write_bytes(content)
                asset = assets.adopt(stage, "file-focus-job", f"result-{asset_index}", {})
                saved_path = assets.items[asset["resourceId"]]["path"]
                req = request(asset)
                lease = clients[0].AcquireLease(req, timeout=.5)
                # Explicitly prove the exact same resource is not readable for another job.
                with pytest.raises(grpc.RpcError) as denied:
                    clients[0].ReadAsset(request(asset, job="other-job"), timeout=.5)
                assert denied.value.code() == grpc.StatusCode.NOT_FOUND
                assets.retain(set())
                assert assets.stats()["assets"] == 1
                try:
                    for repeat in (1, 2):
                        meta[id(saved_path)] = (asset_index, repeat)
                        gate = threading.Barrier(2)
                        def read(client_no):
                            gate.wait(timeout=3)
                            start = time.perf_counter_ns()
                            ok = False
                            try:
                                reply = clients[client_no].ReadAsset(req, timeout=.5)
                                elapsed = time.perf_counter_ns() - start
                                assert reply.sha256 == asset["sha256"] == hashlib.sha256(reply.content).hexdigest()
                                assert len(reply.content) == len(content) and reply.content == content
                                ok = True
                                return True
                            finally:
                                if not ok:
                                    elapsed = time.perf_counter_ns() - start
                                traces.append({"asset":asset_index,"repetition":repeat,"client":client_no,
                                               "bytes":len(content),"elapsed_ns":elapsed,"ok":ok})
                        with ThreadPoolExecutor(max_workers=2) as workers:
                            assert all(workers.map(read, (0, 1)))
                        completed += 2
                    meta.pop(id(saved_path))
                finally:
                    clients[0].ReleaseLease(pb.DisplayLease(lease_id=lease.lease_id), timeout=.5)
                assert assets.stats()["assets"] == 0
        assert completed == 12 and not probe.dropped and len(probe.rows) == 48
    finally:
        try:
            with Path(destination).open("x", encoding="ascii") as handle:
                json.dump({"schema":1,"diagnostic_only":True,"completed":completed,"calls":traces,
                           "phases":probe.rows,"dropped":probe.dropped},handle,separators=(",",":"))
        finally:
            for ch in channels:
                ch.close()
            server.close()
            assets.close()
