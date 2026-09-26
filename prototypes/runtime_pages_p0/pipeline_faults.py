"""Two real Runner Jobs sharing asynchronous exporters and a single ledger."""
import hashlib
import json
import multiprocessing as mp
import threading
import time

import cv2
import grpc
import numpy as np

from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator
from emo_master.apps.runtime.grpc_server.service import RuntimeService

from .continuous import NetworkConsumer
from .network import IsolatedServer
from .pipeline import Pipeline
from .runner_probe import Source, Mutator, image_project


def run(root):
    from .scenarios import wait_until
    image = np.random.default_rng(20260926).integers(0, 256, (1080, 1920, 3), np.uint8)
    path = root / "input.png"
    assert cv2.imwrite(str(path), image)
    registry = {"vision.io.image_loader": ImageLoaderOperator, "p0.source": Source, "p0.mutate": Mutator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(ProjectDocument.model_validate(image_project(path, False)))
    pipeline = Pipeline(root / "pipeline")
    runner = WorkflowRunner(compiled, registry, eventPublisher=pipeline.publish, previewSnapshotStore=pipeline)
    service = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs")
    # A read that is really in progress must retain asset and worker ownership.
    original_read = pipeline.assets.read
    read_entered, read_gate = threading.Event(), threading.Event()
    def slow_read(request, context):
        reader = original_read(request, context)
        try:
            yield next(reader)
            read_entered.set()
            read_gate.wait(10)
            yield from reader
        finally:
            reader.close()
    server = IsolatedServer(service, pipeline)
    clients = [NetworkConsumer(f"127.0.0.1:{server.port}", job, hashlib.sha256(image).hexdigest()) for job in ("one", "two")]
    phases = []
    channel = None
    def detect(job):
        begin = time.perf_counter()
        runner.run("main", {}, RunContext.root(job, "main"), CancellationToken())
        return (time.perf_counter()-begin)*1000
    try:
        for mode in ("hang", "hang", "ipc"):
            wait_until(lambda: not pipeline.exporter.dead)
            pipeline.modes.update(one=mode, two=mode)
            times = [detect("one"), detect("two")]
            if mode == "hang":
                wait_until(lambda: len(pipeline.exporter.active) == 2)
                detect("one")  # no third slot or queue, mark this result unavailable
                assert pipeline.rejected > 0
            pipeline.exporter.wait_idle()
            wait_until(lambda: not pipeline.results.open)
            assert all(pipeline.results.snapshot(pipeline.identity(job))[0].status == "INCOMPLETE" for job in ("one", "two"))
            phases.append(dict(mode=mode, detection_ms=times, stats=pipeline.stats()))
        wait_until(lambda: not pipeline.exporter.dead)
        detect("one")
        detect("two")
        pipeline.exporter.wait_idle()
        wait_until(lambda: all(c.rows and c.rows[-1]["decoded"] for c in clients))
        assert all(c.error is None for c in clients)
        assert len(mp.active_children()) == 2
        # A client that cannot finish its read in 500ms must invalidate that
        # product and keep consuming. The next product recovers without restart.
        previous = len(clients[0].rows)
        clients[0].slow = .02
        detect("one")
        wait_until(lambda: len(clients[0].rows) > previous)
        assert clients[0].rows[-1]["status"] == "UNAVAILABLE"
        assert not clients[0].rows[-1]["decoded"]
        assert clients[0].live["status"] == "INCOMPLETE"
        assert clients[0].error is None
        clients[0].slow = 0
        detect("one")
        wait_until(lambda: clients[0].rows[-1]["decoded"])
        # Freeze both successful asset references before stopping subscriptions.
        assets = [dict(pipeline.results.snapshot(pipeline.identity(job))[0].values)["image"][1]["asset"] for job in ("one", "two")]
        for client in clients:
            client.close()
        clients.clear()
        server.close()
        pipeline.assets.read = slow_read
        server = IsolatedServer(service, pipeline)
        channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
        pin = channel.unary_unary("/p0.Display/Pin", request_serializer=lambda v: json.dumps(v).encode(),
                                  response_deserializer=json.loads)
        unpin = channel.unary_unary("/p0.Display/Unpin", request_serializer=lambda v: json.dumps(v).encode(),
                                    response_deserializer=json.loads)
        lease = pin({"asset_id": assets[0]["asset_id"], "seconds": 1}, timeout=1)["lease"]
        assert pipeline.resources.used["lease"] == assets[0]["size"]
        unpin({"lease": lease}, timeout=1)
        assert pipeline.resources.used["lease"] == 0
        pin({"asset_id": assets[0]["asset_id"], "seconds": .02}, timeout=1)
        wait_until(lambda: pipeline.resources.used["lease"] == 0)
        call = channel.unary_stream("/p0.Display/GetDisplayAsset", request_serializer=lambda v: json.dumps(v).encode())(
            {"asset_id": assets[0]["asset_id"], "job": "one"}, timeout=3)
        assert next(call)
        wait_until(read_entered.is_set)
        call.cancel()
        time.sleep(.03)
        assert server.active["asset"] == 1
        assert pipeline.assets.entries[assets[0]["asset_id"]].readers == 1
        read_gate.set()
        wait_until(lambda: server.active["asset"] == 0)
        assert pipeline.assets.entries[assets[0]["asset_id"]].readers == 0
        # An expired read has an explicit error and releases its reservation.
        from .network import CancelContext
        reader = original_read({"asset_id": assets[1]["asset_id"], "job": "two"}, CancelContext())
        next(reader)
        time.sleep(.51)
        try:
            next(reader)
        except TimeoutError:
            pass
        else:
            raise AssertionError("read deadline")
        # close while export is running: callbacks finalize INCOMPLETE, workers
        # are killed/joined before shared memory/staging reservations disappear.
        pipeline.modes["one"] = "hang"
        detect("one")
        wait_until(lambda: bool(pipeline.exporter.active))
        pipeline.exporter.close()
        assert not mp.active_children()
        phases.append(dict(mode="running-close", stats=pipeline.stats()))
        return dict(correctness_status="PASS", phases=phases, two_jobs=True, two_network_decoders=True,
                    slow_read_cancel=True, read_deadline=True, children_after=0)
    finally:
        read_gate.set()
        for client in clients:
            client.close()
        if channel:
            channel.close()
        server.close()
        pipeline.close()
        service.close()
