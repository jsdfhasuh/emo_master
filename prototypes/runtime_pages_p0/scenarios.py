"""Fault scenarios: invoke only through watchdog.py (outer process deadline)."""
import json
from dataclasses import replace
from itertools import permutations
import multiprocessing as mp
from multiprocessing import shared_memory
from pathlib import Path
import tempfile
import threading
import time

import cv2
import grpc
import numpy as np

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.main import createRuntimeServer

from .contracts import BUDGET, Results, Unavailable
from .exporter import Exporters
from .network import DisplayFeed, IsolatedServer
from .runner_probe import image_project


def wait_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise TimeoutError("scenario condition")


def export_scenario(root):
    image = np.full((1080, 1920, 3), 127, np.uint8)
    rows = []
    with Exporters(root / "export") as exporter:
        assert exporter.execute(image)["status"] == "VALID"
        for mode in ["hang", "hang", "hang", "ipc"]:
            started_count = len(exporter.started)
            start = time.perf_counter()
            result = exporter.execute(image, mode)
            elapsed = time.perf_counter() - start
            assert result["status"] == "UNAVAILABLE"
            assert len(exporter.started) == started_count + 1
            assert elapsed < BUDGET.export_seconds + BUDGET.reap_seconds + .5
            assert exporter.ledger.used == 0
            name = exporter.started[-1][2]
            try:
                shared_memory.SharedMemory(name=name)
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("shared memory leaked")
            assert len(mp.active_children()) <= BUDGET.export_slots
            assert not list((root / "export").iterdir())
            rows.append({"mode": mode, "elapsed_ms": elapsed * 1000, "started": True,
                         "status": result["status"], "memory_after": exporter.ledger.used})
            exporter.replenish()
            assert exporter.execute(image)["status"] == "VALID"
        # Both actual executing tasks block. No queued work or growing threads.
        results = []
        threads = [threading.Thread(target=lambda: results.append(exporter.execute(image, "hang"))) for _ in range(2)]
        previous = len(exporter.started)
        for thread in threads:
            thread.start()
        wait_until(lambda: len(exporter.started) == previous + 2)
        try:
            exporter.execute(image)
        except Unavailable:
            pass
        else:
            raise AssertionError("quota not enforced")
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        assert len(results) == 2 and exporter.ledger.used == 0
        peak = exporter.ledger.peak
        clocks = exporter.clock_samples
        exporter.replenish()
        # Closing while a task is actually blocked must reclaim its owner too.
        previous = len(exporter.started)
        closing_result = []
        closing_thread = threading.Thread(target=lambda: closing_result.append(exporter.execute(image, "hang")))
        closing_thread.start()
        wait_until(lambda: len(exporter.started) == previous + 1)
        exporter.close()
        closing_thread.join(2)
        assert not closing_thread.is_alive() and exporter.ledger.used == 0
        assert closing_result[0]["status"] == "UNAVAILABLE"
    assert not mp.active_children()
    return {"rows": rows, "peak_reserved_bytes": peak, "clock_samples": clocks,
            "children_after": 0, "staging_files_after": 0, "quota_rejected": True}


def write_plugins(root):
    plugin_root = root / "plugins"
    for name, class_name, inputs, outputs in [
        ("source", "Source", {"image": "image"}, {"image": "image", "items": "json"}),
        ("mutate", "Mutator", {"image": "image", "items": "json"}, {})]:
        directory = plugin_root / name
        directory.mkdir(parents=True)
        manifest = {"operatorId": f"p0.{name}", "displayName": name, "version": "1.0.0",
                    "entry": f"prototypes.runtime_pages_p0.runner_probe:{class_name}",
                    "category": "test", "iconKey": "default", "summary": "P0 controlled operator",
                    "inputPorts": inputs, "outputPorts": outputs, "paramSchema": {"type": "object"},
                    "minCoreVersion": "0.1.0", "maxCoreVersion": "1.x"}
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return (str(Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"), str(plugin_root))


class SimulatedPreviewService(RuntimeService):
    """Only camera frame production is simulated; RPC/socket/stream are real."""
    def StreamOperatorPreviewFrames(self, request, context):
        assert request.session_id == "P0-SIMULATED-NO-DEVICE"
        ok, jpeg = cv2.imencode(".jpg", np.zeros((480, 640, 3), np.uint8))
        assert ok
        sequence = 0
        while context.is_active():
            sequence += 1
            yield pb.OperatorPreviewFrame(session_id=request.session_id, jpeg=jpeg.tobytes(),
                                          sequence=sequence, width=640, height=480)
            context.cancelled.wait(.1)


def network_scenario(root):
    roots = write_plugins(root)
    image_path = root / "input.png"
    assert cv2.imwrite(str(image_path), np.full((1080, 1920, 3), 127, np.uint8))
    # Long-running controlled operator lets streams remain live until force stop.
    payload = image_project(image_path, repeated=True, delay=30)
    (root / "project.json").write_text(json.dumps(payload), encoding="utf-8")
    service = SimulatedPreviewService(dbPath=root / "runtime.db", pluginRootPaths=roots)
    # Original four-worker entry, same RuntimeService, no duplicate device owner.
    baseline_server, baseline_port, _ = createRuntimeServer(port=0, runtimeService=service)
    baseline_server.start()
    baseline_channel = grpc.insecure_channel(f"127.0.0.1:{baseline_port}")
    baseline_status = []
    try:
        grpc.channel_ready_future(baseline_channel).result(5)
        baseline_stub = rpc.RuntimeServiceStub(baseline_channel)
        for _ in range(20):
            before = time.perf_counter_ns()
            baseline_stub.GetJobStatus(pb.GetJobStatusRequest(job_id="baseline-no-job"), timeout=1)
            baseline_status.append((time.perf_counter_ns()-before)/1e6)
    finally:
        baseline_channel.close()
        baseline_server.stop(0).wait()
    feed = DisplayFeed()
    lifecycle = Results()
    pending = {}
    def event_sink(event):
        if event.eventType == "workflow.started":
            identity = ("runtime", 1, "p0-project", event.jobId, event.workflowId, (event.nodeId,))
            pending[(event.jobId, event.workflowRunId)] = lifecycle.begin(identity, ("final",))
        if event.eventType == "node.completed":
            feed.publish(event.jobId, {"sequence": event.sequence, "node": event.nodeId,
                                       "workflow_run": event.workflowRunId})
    service.eventStore.addSink(event_sink)
    original_terminal = service.jobSupervisor.terminalCallback
    def terminal(job, status):
        lifecycle.terminate(("runtime", 1, "p0-project", job))
        if original_terminal:
            original_terminal(job, status)
    service.jobSupervisor.terminalCallback = terminal
    server = IsolatedServer(service, feed)
    channel = grpc.insecure_channel(f"127.0.0.1:{server.port}")
    stub = rpc.RuntimeServiceStub(channel)
    calls = []
    rows = []
    display_rpc = channel.unary_stream("/p0.Display/StreamDisplayUpdates",
                                      request_serializer=lambda x: json.dumps(x).encode(), response_deserializer=json.loads)
    snapshot_rpc = channel.unary_unary("/p0.Display/GetDisplaySnapshot",
                                      request_serializer=lambda x: json.dumps(x).encode(), response_deserializer=json.loads)
    try:
        grpc.channel_ready_future(channel).result(5)
        service.sqliteStore.setGlobalCounter("p0-project", "release-parts", 100)
        # Server latest and two true network subscribers obey detection start
        # order, independently of the RPC publication cursor.
        order_cases = 0
        for status in ("COMMITTED", "INCOMPLETE", "FAILED"):
            for order in permutations(range(3)):
                feed.results = Results(replace(BUDGET, seal_seconds=0))
                identity = ("order-runtime", order_cases, "p0-project", "order-job", "scope", ())
                feed.results.ordinals[identity] = 100
                keys = [feed.results.begin(identity, ("count",)) for _ in range(3)]
                subscribers = [display_rpc({"identity": identity}, timeout=5) for _ in range(2)]
                calls.extend(subscribers)
                for subscriber in subscribers:
                    next(subscriber)
                high = 0
                for index in order:
                    high = max(high, 101 + index)
                    if status != "INCOMPLETE" or index != 2:
                        feed.results.value(keys[index], "count", ("VALID", index))
                    feed.results.seal(keys[index], failed=status == "FAILED" and index == 2)
                    feed.results.seal(keys[index])
                    assert snapshot_rpc({"identity": identity}, timeout=1)["latest"]["ordinal"] == high
                    for subscriber in subscribers:
                        assert next(subscriber)["latest"]["ordinal"] == high
                assert snapshot_rpc({"identity": identity}, timeout=1)["latest"]["status"] == status
                for subscriber in subscribers:
                    subscriber.cancel()
                calls.clear()
                wait_until(lambda: server.active["display"] == 0)
                order_cases += 1
        assert service.jobRepository.all() == []  # simulated read-only preview starts no operator
        assert service.sqliteStore.getGlobalCounter("p0-project", "release-parts").value == 100
        reply = stub.LoadProject(pb.LoadProjectRequest(project_path=str(root)), timeout=5)
        assert reply.ok, reply.message
        jobs = []
        for label, job_count, display_count, preview_count in [("C1", 1, 1, 0), ("C2", 1, 1, 1),
                                                               ("C3", 1, 2, 1), ("C4", 2, 2, 1)]:
            for _ in range(job_count - len(jobs)):
                started_at = time.perf_counter_ns()
                started = stub.StartJob(pb.StartJobRequest(project_id="p0-project", workflow_id="main"), timeout=5)
                replied_at = time.perf_counter_ns()
                assert started.ok, started.message
                jobs.append(started.job_id)
                wait_until(lambda: service.jobRepository.get(started.job_id).status == "RUNNING")
                rows.append({"phase": label, "start_reply_ms": (replied_at - started_at) / 1e6,
                             "start_running_ms": (time.perf_counter_ns() - started_at) / 1e6})
            event_calls = [stub.StreamJobEvents(pb.StreamJobEventsRequest(job_id=job, follow=True), timeout=30) for job in jobs]
            calls.extend(event_calls)
            for call in event_calls:
                assert next(call).job_id in jobs
            display_calls = [display_rpc({"job": jobs[min(i, len(jobs)-1)]}, timeout=30) for i in range(display_count)]
            calls.extend(display_calls)
            for call in display_calls:
                next(call)
            if preview_count:
                camera = stub.StreamOperatorPreviewFrames(pb.StreamOperatorPreviewFramesRequest(session_id="P0-SIMULATED-NO-DEVICE"), timeout=30)
                calls.append(camera)
                assert next(camera).width == 640
            wait_until(lambda: server.active["events"] == job_count and server.active["display"] == display_count)
            latencies = []
            for _ in range(20):
                begin = time.perf_counter_ns()
                assert stub.GetJobStatus(pb.GetJobStatusRequest(job_id=jobs[0]), timeout=1).ok
                latencies.append((time.perf_counter_ns() - begin) / 1e6)
            assert max(latencies) < BUDGET.control_seconds * 1000
            rows.append({"phase": label, "get_status_ms": latencies, "active": dict(server.active)})
            if label == "C4":
                # Export failure must not stop either detection Job or starve
                # controls, even after the export actually entered its task.
                with Exporters(root / "concurrent-export") as exporter:
                    export_results = []
                    thread = threading.Thread(target=lambda: export_results.append(
                        exporter.execute(np.zeros((1080, 1920, 3), np.uint8), "hang")))
                    thread.start()
                    wait_until(lambda: len(exporter.started) == 1)
                    fault_start = time.perf_counter_ns()
                    for job in jobs:
                        assert stub.GetJobStatus(pb.GetJobStatusRequest(job_id=job), timeout=1).status == "RUNNING"
                    fault_rtt = (time.perf_counter_ns()-fault_start)/1e6
                    thread.join(3)
                    assert not thread.is_alive() and export_results[0]["status"] == "UNAVAILABLE"
                    assert all(service.jobRepository.get(job).status == "RUNNING" for job in jobs)
                    rows.append({"phase": "export-hang-with-C4", "two_status_rtt_ms": fault_rtt,
                                 "jobs_still_running": 2, "export_memory_after": exporter.ledger.used})
                for excess in [display_rpc({"job": jobs[0]}, timeout=1),
                               stub.StreamJobEvents(pb.StreamJobEventsRequest(job_id=jobs[0], follow=True), timeout=1),
                               stub.StreamOperatorPreviewFrames(pb.StreamOperatorPreviewFramesRequest(session_id="P0-SIMULATED-NO-DEVICE"), timeout=1)]:
                    try:
                        next(excess)
                    except grpc.RpcError as error:
                        assert error.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
                    else:
                        raise AssertionError("stream quota not enforced")
                # Poll recovers latest even without any new event/push afterward.
                feed.publish(jobs[0], {"sequence": 999, "last": True})
                assert snapshot_rpc({"job": jobs[0]}, timeout=1)["latest"]["last"]
                for job in jobs:
                    # Ensure child scope really started before killing the worker.
                    wait_until(lambda: any(e.workflowId == "leaf" and e.eventType == "node.started" and e.nodeId == "mutate"
                                           for e in service.eventStore.read(job)))
                    begin = time.perf_counter_ns()
                    reply = stub.StopJob(pb.StopJobRequest(job_id=job, mode="force"), timeout=5)
                    end = time.perf_counter_ns()
                    accepted = [stamp for name, stamp in server.accepted if name == "StopJob"][-1]
                    assert reply.ok
                    wait_until(lambda: service.jobRepository.get(job).isTerminal)
                    assert (accepted - begin) / 1e9 < BUDGET.control_seconds
                    rows.append({"phase": label, "stop_accept_ms": (accepted-begin)/1e6,
                                 "stop_reply_ms": (end-begin)/1e6,
                                 "stop_terminal_ms": (time.perf_counter_ns()-begin)/1e6})
            for call in calls:
                call.cancel()
            calls.clear()
            wait_until(lambda: all(server.active[k] == 0 for k in ("events", "display", "preview")), 5)
        assert not lifecycle.open
        assert len(lifecycle.history) == 4
        assert all(s.status == "INCOMPLETE" for s in lifecycle.history.values())
        for key in lifecycle.history:
            assert not lifecycle.value(key, "final", ("VALID", 1))
        return {"rows": rows, "peak": dict(server.peak), "spawn_jobs": len(jobs),
                "network_order_cases": order_cases,
                "original_server_idle_status_ms": baseline_status,
                "supervisor_incomplete_scopes": len(lifecycle.history), "quota_rejected": True,
                "simulated_preview": "640x480 JPEG, 10 fps, no hardware"}
    finally:
        for call in calls:
            call.cancel()
        channel.close()
        try:
            server.close()
        finally:
            service.close()
        assert not mp.active_children()


def run(name):
    with tempfile.TemporaryDirectory(prefix="emo-p0-") as temporary:
        root = Path(temporary)
        if name == "exports":
            return export_scenario(root)
        if name == "network":
            return network_scenario(root)
        if name == "benchmark":
            from .benchmark import benchmark
            return benchmark(root)
        raise ValueError(name)


if __name__ == "__main__":
    import sys
    mp.freeze_support()
    if sys.stdin.readline().strip() != "GO":
        raise RuntimeError("invoke via watchdog.supervised")
    print(json.dumps(run(sys.argv[1]), ensure_ascii=True))
