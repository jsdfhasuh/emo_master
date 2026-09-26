"""Fixed-cadence real Runner and long-lived independent loopback clients."""
import hashlib
import json
import threading
import time

import cv2
import grpc
import numpy as np

from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator

from .benchmark import resources
from .contracts import BUDGET
from .network import IsolatedServer
from .pipeline import Pipeline
from .runner_probe import Source, Mutator, image_project


class NetworkConsumer:
    def __init__(self, target, job, digest, slow=0):
        self.channel = grpc.insecure_channel(target)
        self.job, self.digest, self.slow = job, digest, slow
        self.rows = []
        self.live = None
        self.max_age_ms = 0
        self.error = None
        self.ready = threading.Event()
        self.stop = threading.Event()
        def serializer(value):
            return json.dumps(value).encode()
        self.updates = self.channel.unary_stream("/p0.Display/StreamDisplayUpdates", request_serializer=serializer,
                                                response_deserializer=json.loads)
        self.assets = self.channel.unary_stream("/p0.Display/GetDisplayAsset", request_serializer=serializer)
        self.call = self.asset_call = None
        self.thread = threading.Thread(target=self._receive, name="p0-network-consumer")
        self.thread.start()
        assert self.ready.wait(5)

    def _receive(self):
        try:
            grpc.channel_ready_future(self.channel).result(5)
            self.call = self.updates({"job": self.job})
            self.ready.set()
            for message in self.call:
                snapshot = message["latest"]
                if self.live and snapshot["ordinal"] <= self.live["ordinal"]:
                    continue
                received = time.perf_counter_ns()
                row = dict(key=snapshot["key"], ordinal=snapshot["ordinal"], status=snapshot["status"],
                           scope_end_ns=snapshot["scope_end_ns"], received_ns=received, decoded=False)
                values = dict(snapshot["values"])
                if snapshot["status"] == "COMMITTED" and values["image"][0] == "VALID":
                    asset = values["image"][1]["asset"]
                    assert asset["result_key"] == snapshot["key"] and asset["job"] == self.job
                    assert 0 < asset["size"] <= BUDGET.image_bytes * 2
                    # Client local budget, separate from server accounting:
                    # one <=16MiB bytearray + decoder buffer + one 8MiB image.
                    content = bytearray()
                    self.asset_call = self.assets({"asset_id": asset["asset_id"], "job": self.job}, timeout=BUDGET.read_seconds)
                    for chunk in self.asset_call:
                        assert len(content) + len(chunk) <= asset["size"]
                        content.extend(chunk)
                        if self.slow:
                            time.sleep(self.slow)
                    transferred = time.perf_counter_ns()
                    assert len(content) == asset["size"] and hashlib.sha256(content).hexdigest() == asset["sha256"]
                    decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_UNCHANGED)
                    assert decoded is not None and list(decoded.shape) == asset["shape"]
                    assert hashlib.sha256(decoded).hexdigest() == self.digest == values["image"][1]["raw_sha256"]
                    assert values["items"] == ["VALID", [[["count", 7]]]]
                    del decoded, content
                    row.update(decoded=True, asset_id=asset["asset_id"], transfer_ms=(transferred-received)/1e6,
                               decode_verify_ms=(time.perf_counter_ns()-transferred)/1e6)
                committed = time.perf_counter_ns()
                if self.live:
                    self.max_age_ms = max(self.max_age_ms, (committed-self.live["scope_end_ns"])/1e6)
                self.live = snapshot
                row.update(commit_ns=committed, latency_ms=(committed-snapshot["scope_end_ns"])/1e6)
                self.rows.append(row)
        except grpc.RpcError as error:
            if not self.stop.is_set():
                self.error = str(error)
        except BaseException as error:
            self.error = repr(error)
        finally:
            self.ready.set()

    def close(self):
        self.stop.set()
        if self.call:
            self.call.cancel()
        if self.asset_call:
            self.asset_call.cancel()
        self.channel.close()
        self.thread.join(3)
        assert not self.thread.is_alive()


def window(root, service, image_path, digest, count=96, viewers=2, enabled=True, warmup=5):
    root.mkdir(parents=True, exist_ok=True)
    registry = {"vision.io.image_loader": ImageLoaderOperator, "p0.source": Source, "p0.mutate": Mutator}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(ProjectDocument.model_validate(image_project(image_path, False)))
    pipeline = Pipeline(root) if enabled else None
    server = IsolatedServer(service, pipeline) if enabled else None
    consumers = []
    preview = PreviewSnapshotWriter(root / "legacy-preview")
    class Tee:
        def capture(self, node, outputs, context):
            preview.capture(node, outputs, context)  # same legacy cost in every group
            if pipeline:
                pipeline.capture(node, outputs, context)
    runner = WorkflowRunner(compiled, registry, eventPublisher=pipeline.publish if pipeline else None,
                            previewSnapshotStore=Tee())
    rows, commits, samples = [], [], []
    if pipeline:
        pipeline.commit_sink = lambda snapshot: commits.append({"key": snapshot.key, "ordinal": snapshot.ordinal,
                                                               "status": snapshot.status, "scope_end_ns": snapshot.scope_end_ns,
                                                               "commit_ns": snapshot.commit_ns,
                                                               "image": dict(snapshot.values)["image"]})
    before = resources()
    try:
        for index in range(viewers):
            consumers.append(NetworkConsumer(f"127.0.0.1:{server.port}", "measurement", digest))
        scheduled_origin = time.perf_counter_ns()
        for index in range(count + warmup):
            scheduled = scheduled_origin + int(index * 1e9 / 5)
            remaining = (scheduled-time.perf_counter_ns())/1e9
            if remaining > 0:
                time.sleep(remaining)
            begin = time.perf_counter_ns()
            runner.run("main", {}, RunContext.root("measurement", "main"), CancellationToken())
            end = time.perf_counter_ns()
            rows.append(dict(index=index, ordinal=index+1, warmup=index < warmup, scheduled_ns=scheduled,
                             start_ns=begin, end_ns=end, execution_ms=(end-begin)/1e6,
                             lateness_ms=max(0, (begin-scheduled)/1e6)))
            if pipeline and (index % 8 == 0 or index == count + warmup - 1):
                samples.append(dict(index=index, pipeline=pipeline.stats(), host=resources(),
                                    threads=threading.active_count(),
                                    exporters=[resources(s[0].pid) for s in pipeline.exporter.slots]))
        if pipeline:
            pipeline.exporter.wait_idle()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and any(len(c.rows) < count+warmup and c.error is None for c in consumers):
                time.sleep(.01)
            assert not pipeline.results.open and not pipeline.runs
            assert pipeline.final == count + warmup
            stats = pipeline.stats()
        else:
            stats = None
        measured = rows[warmup:]
        client_rows = [[r for r in consumer.rows if r["ordinal"] > warmup] for consumer in consumers]
        latency = [r["latency_ms"] for client in client_rows for r in client if r["decoded"]]
        measured_commits = [c for c in commits if c["ordinal"] > warmup]
        if not consumers and pipeline:
            latency = [(c["commit_ns"]-c["scope_end_ns"])/1e6 for c in measured_commits]
        observed_end = time.perf_counter_ns()
        for consumer in consumers:
            if consumer.live:
                consumer.max_age_ms = max(consumer.max_age_ms, (observed_end-consumer.live["scope_end_ns"])/1e6)
        correct = (not pipeline or (len(measured_commits) == count and all(c["status"] == "COMMITTED" for c in measured_commits)))
        correct = correct and all(not c.error for c in consumers) and all(len(c) == count and all(r["decoded"] for r in c) for c in client_rows)
        execution_p95 = float(np.percentile([r["execution_ms"] for r in measured], 95))
        throughput = (count - 1) * 1e9 / (measured[-1]["start_ns"]-measured[0]["start_ns"])
        report = dict(enabled=enabled, viewers=viewers, count=count, warmup=warmup, rows=rows,
                    commits=commits, clients=client_rows, samples=samples, stats=stats,
                    before=before, after=resources(), execution_p95_ms=execution_p95,
                    client_errors=[c.error for c in consumers],
                    max_schedule_lateness_ms=max(r["lateness_ms"] for r in measured),
                    incomplete=sum(c["status"] != "COMMITTED" for c in measured_commits),
                    model_p95_ms=float(np.percentile(latency, 95)) if latency else None,
                    achieved_hz=throughput, coverage=[sum(r["decoded"] for r in client)/count for client in client_rows],
                    max_live_age_ms=[consumer.max_age_ms for consumer in consumers],
                    correctness_status="PASS" if correct else "FAIL")
        return report
    finally:
        for consumer in consumers:
            consumer.close()
        if server:
            server.close()
        if pipeline:
            pipeline.close()
            if "report" in locals():
                report["cleanup"] = pipeline.resources.snapshot()
                report["after_close"] = resources()
