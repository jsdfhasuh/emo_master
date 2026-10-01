"""Bounded normal-StartJob ALL/NONE policy comparison on synthetic 1080p input.

Three rotating groups contain ALL/capture-off, ALL/capture-on/two Qt views and
NONE/capture-on/two Qt views. The original P2/P3/R3 scripts remain unchanged.
The policy is sent through RuntimeService.StartJob, never patched into a spec.
Raw all-window age thresholds include the terminal tail. Phase attribution is
additional evidence, never a replacement or denominator filter for that verdict.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "scripts")]

import r3_measure as base  # noqa: E402
from scripts.r3_sampling import BoundedSampler  # noqa: E402

ARM_SPECS = {
    "all_off": {"policy": "ALL", "capture": False},
    "all_qt": {"policy": "ALL", "capture": True},
    "none_qt": {"policy": "NONE", "capture": True},
}
OBSERVATION_SECONDS = 45
FINAL_OBSERVATION_SECONDS = .5
RAW_BYTES = 24 * 1024 * 1024


def armOrder(group):
    arms = list(ARM_SPECS)
    shift = group % len(arms)
    return arms[shift:] + arms[:shift]


def buildStartRequest(projectId, arm, generation, token):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    if not generation or not token:
        raise ValueError("normal measurement requires caller-owned generation and start token")
    config = ARM_SPECS[arm]
    return pb.StartJobRequest(project_id=projectId, workflow_id="main", inputs_json="{}",
        capture_presentation=config["capture"], legacy_snapshot_policy=config["policy"],
        start_request_id=token, expected_runtime_instance_id=generation)




def measuredJob(spec, cancelEvent, eventQueue):
    """Uniform inclusive detect-workflow timing, including capture-off."""
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    trace = base.Trace("execution")
    try:
        with base.patches() as patch:
            base.wrap(trace, patch, WorkflowRunner, "run", "workflow.detect",
                lambda _self, workflowId, inputs, context, cancellation: base.contextDetails(context),
                lambda _self, workflowId, *_args, **_kwargs: workflowId == "detect")
            # Retain identical old attribution wrappers, but never change spec,
            # collector measure flags, snapshot policy, or capture configuration.
            base.measuredJob(spec, cancelEvent, eventQueue)
    finally:
        trace.save()


def executionRows(traces):
    return sorted([{"ordinal": row.get("ordinal"), "startNs": row["start_ns"],
        "endNs": row["end_ns"], "outcome": row["outcome"], "invocation": row.get("invocation")}
        for trace in traces if trace["role"] == "execution" for row in trace["rows"]
        if row["stage"] == "workflow.detect"], key=lambda row: row["startNs"])


def _maxAge(rows, key):
    values = [value for row in rows for value in row[key] if value is not None]
    return max(values) if values else None


def _ageSummary(rows):
    return {"samples": len(rows), "max_model_ms": _maxAge(rows, "model_ms"),
        "max_ui_ms": _maxAge(rows, "ui_ms"),
        "model_unavailable_samples": sum(value is None for row in rows for value in row["model_ms"]),
        "ui_unavailable_samples": sum(not value for row in rows for value in row.get("ui_available", []))}


def _deliveryComplete(result, expected):
    if not result["capture_enabled"]:
        return None
    if len(result["consumers"]) != 2 or len(result["ui"]) != 2:
        return None
    stamps = []
    keyOrdinal = {}
    for consumer in result["consumers"]:
        byOrdinal = {}
        for row in consumer["rows"]:
            keyOrdinal[row["key"]] = row["ordinal"]
            if row["ordinal"] in expected and row.get("model_ns") and row["applied_to_live"] and "image" in row["decoded"] and not row["failures"]:
                byOrdinal.setdefault(row["ordinal"], row["model_ns"])
        if set(byOrdinal) != expected:
            return None
        stamps.extend(byOrdinal.values())
    for window in result["ui"]:
        byOrdinal = {}
        for row in window["raw_rows"]:
            ordinal = keyOrdinal.get(row["key"])
            if ordinal in expected and row.get("paint_ns"):
                byOrdinal.setdefault(ordinal, row["paint_ns"])
        if set(byOrdinal) != expected:
            return None
        stamps.extend(byOrdinal.values())
    return max(stamps) if stamps else None


def addAccounting(result, count, warmup, timings):
    """Retain all rows, gaps and failed execution outcomes; classify after run."""
    expected = set(range(warmup + 1, count + 1))
    measured = [row for row in timings if row.get("ordinal") in expected]
    seen = Counter(row.get("ordinal") for row in timings)
    completed = {row["ordinal"] for row in measured if row["outcome"] == "OK"}
    result["raw_timings"] = timings
    result["expected"] = len(expected)
    result["executed"] = len(completed)
    durations = [(row["endNs"] - row["startNs"]) / 1e6 for row in measured]
    result["p95_execution_ms"] = base.percentile(durations)
    result["mean_execution_ms"] = statistics.mean(durations) if durations else None
    result["actual_intervals_ms"] = [(measured[i]["startNs"] - measured[i-1]["startNs"])/1e6 for i in range(1, len(measured))]
    span = measured[-1]["startNs"]-measured[0]["startNs"] if len(measured)>1 else 0
    result["achieved_hz"] = (len(measured)-1)*1e9/span if span > 0 else 0
    result["denominators"] = {"planned_inputs": count, "warmup_inputs": warmup,
        "measured_inputs": len(expected), "executed_total": len(timings),
        "missing_execution_ordinals": sorted(set(range(1, count+1))-set(seen)),
        "missing_measured_execution_ordinals": sorted(expected-completed),
        "duplicate_execution_ordinals": {str(k): v for k, v in seen.items() if v > 1},
        "out_of_range_execution_rows": [r for r in timings if r.get("ordinal") not in range(1, count+1)],
        "failed_execution_rows": [r for r in timings if r["outcome"] != "OK"]}
    for consumer in result["consumers"]:
        observed = {row["ordinal"] for row in consumer["rows"]}
        consumer["missing_measured_ordinals"] = sorted(expected-observed)
        consumer["received_not_applied"] = sum(not row["applied_to_live"] for row in consumer["rows"])
        decoded = {r["ordinal"] for r in consumer["rows"] if "image" in r["decoded"] and not r["failures"]}
        applied = {r["ordinal"] for r in consumer["rows"] if r["applied_to_live"] and r.get("model_ns")}
        consumer["missing_decoded_ordinals"] = sorted(expected-decoded)
        consumer["missing_applied_ordinals"] = sorted(expected-applied)
        consumer["unexpected_ordinals"] = sorted(observed-expected)
        keys = {}
        for row in consumer["rows"]:
            keys.setdefault(row["ordinal"], set()).add(row["key"])
        consumer["duplicate_ordinal_keys"] = {str(k): sorted(v) for k, v in keys.items() if len(v) > 1}
    keyOrdinal = {r["key"]: r["ordinal"] for c in result["consumers"] for r in c["rows"]}
    for window in result["ui"]:
        committed = {keyOrdinal.get(r["key"]) for r in window["raw_rows"]}
        painted = {keyOrdinal.get(r["key"]) for r in window["raw_rows"] if r.get("paint_ns")}
        window["missing_committed_ordinals"] = sorted(expected-committed)
        window["missing_painted_ordinals"] = sorted(expected-painted)
        window["unexpected_committed_ordinals"] = sorted(str(v) for v in committed-expected)
        window["unexpected_painted_ordinals"] = sorted(str(v) for v in painted-expected)
    pace = result.get("pace", [])
    indices = Counter(row.get("index") for row in pace)
    result["pace_denominators"] = {
        "expected": count, "observed": len(pace),
        "missing_indices": sorted(set(range(1, count+1))-set(indices)),
        "duplicate_indices": {str(k): v for k, v in indices.items() if v > 1},
        "unexpected_indices": [index for index in indices if index not in range(1, count+1)],
    }
    lateness = [(row["actual"]-row["due"])*1000 for row in pace if row.get("index") in expected]
    result["max_schedule_lateness_ms"] = max(lateness) if lateness else None
    result["record_retention"] = {"client_capacity": 128, "qt_capacity": 256,
        "capacity_reached": any(c.get("total_record_count", len(c["rows"])) >= 128 for c in result["consumers"]) or
            any(w.get("total_record_count", len(w["raw_rows"])) >= 256 for w in result["ui"])}
    firstRows = [row for row in timings if row.get("ordinal") == 1]
    finalRows = [row for row in timings if row.get("ordinal") == count]
    first = firstRows[0]["startNs"] if len(firstRows) == 1 and firstRows[0]["outcome"] == "OK" else None
    final = finalRows[0]["endNs"] if len(finalRows) == 1 and finalRows[0]["outcome"] == "OK" else None
    delivery = _deliveryComplete(result, expected)
    stamps = result["timestamps"]
    terminal = stamps.get("terminal_observed_ns")
    observationEnd = stamps["observation_end_ns"]
    buckets = {}
    for row in result["age_samples"]:
        stamp = row["time_ns"]
        if first is None:
            phase = "execution_boundary_unknown"
        elif stamp < first:
            phase = "startup"
        elif final is None:
            phase = "active_input_unresolved"
        elif stamp < final:
            phase = "active_input"
        elif result["capture_enabled"] and (delivery is None or stamp < delivery):
            phase = "final_delivery" if delivery is not None else "final_delivery_unresolved"
        elif terminal is None or stamp < terminal:
            phase = "terminal_wait"
        else:
            phase = "post_terminal_observation"
        row["phase"] = phase
        buckets.setdefault(phase, []).append(row)
    result["phases"] = {"first_input_start_ns": first, "final_input_end_ns": final,
        "all_measured_delivery_complete_ns": delivery, "terminal_observed_ns": terminal,
        "observation_end_ns": observationEnd,
        "active_input_ms": (final-first)/1e6 if final is not None and first is not None else None,
        "final_delivery_after_input_ms": max(0, delivery-final)/1e6 if delivery is not None and final is not None else None,
        "input_end_to_terminal_ms": (terminal-final)/1e6 if terminal is not None and final is not None else None,
        "terminal_wait_after_delivery_ms": max(0, terminal-max(final, delivery if delivery is not None else final))/1e6
            if terminal is not None and final is not None and (not result["capture_enabled"] or delivery is not None) else None,
        "cleanup_ms": (stamps["cleanup_end_ns"]-stamps["cleanup_start_ns"])/1e6,
        "age_by_phase": {name: _ageSummary(rows) for name, rows in buckets.items()},
        "raw_all_window": _ageSummary(result["age_samples"]),
        "observation_deadline_seconds": OBSERVATION_SECONDS, "post_terminal_observation_seconds": FINAL_OBSERVATION_SECONDS,
        "export_deadline_seconds": .5, "asset_rpc_deadline_seconds": .5,
        "definition": "Retrospective same-host timestamps. Missing final input/delivery never creates an inferred end. Input-to-terminal includes delivery overlap; do not sum overlapping intervals. Raw all-window verdict is unchanged."}
    result["age_samples_ms"] = [row["model_ms"] for row in result["age_samples"]]
    result["ui_age_samples_ms"] = [row["ui_ms"] for row in result["age_samples"]]


def _consumerResult(client, warmup):
    with client.lock:
        allRows = list(client.records)
        rows = [dict(row) for row in allRows if row["ordinal"] > warmup]
        stats, errors = dict(client.stats), list(client.errors)
    latency = [(r["model_ns"]-r["scope_ended_ns"])/1e6 for r in rows if r["model_ns"] and r["scope_ended_ns"]]
    return {"rows": rows, "raw_records": allRows, "total_record_count": len(allRows), "stats": stats, "errors": errors,
        "unique_received": len({r["key"] for r in rows}),
        "unique_decoded": len({r["key"] for r in rows if "image" in r["decoded"] and not r["failures"]}),
        "unique_applied": len({r["key"] for r in rows if r["applied_to_live"]}),
        "p95_model_ms": base.percentile(latency)}


def _windowResult(window, consumers, warmup):
    keyOrdinal = {r["key"]: r["ordinal"] for c in consumers for r in c.get("raw_records", c["rows"])}
    allRows = list(window.records)
    rows = [dict(r) for r in allRows if r["key"] not in keyOrdinal or keyOrdinal[r["key"]] > warmup]
    first = {}
    for row in rows:
        item = first.setdefault(row["key"], dict(row))
        if row.get("paint_ns"):
            item["paint_ns"] = min(item.get("paint_ns", row["paint_ns"]), row["paint_ns"])
    values = list(first.values())
    return {"raw_rows": rows, "raw_records": allRows, "total_record_count": len(allRows), "unique_committed": len(values),
        "unique_painted": sum(bool(r.get("paint_ns")) for r in values),
        "p95_scope_to_gui_ms": base.percentile([(r["gui_ns"]-r["scope_end_ns"])/1e6 for r in values if r["scope_end_ns"]]),
        "p95_ready_to_gui_ms": base.percentile([(r["gui_ns"]-r["ready_ns"])/1e6 for r in values]),
        "p95_scope_to_paint_ms": base.percentile([(r["paint_ns"]-r["scope_end_ns"])/1e6 for r in values if r.get("paint_ns") and r["scope_end_ns"]])}


def retireGui(windows, hub, app):
    from PySide2.QtCore import QCoreApplication, QEvent
    for window in windows:
        window.close()
        window.deleteLater()
    if hub is not None:
        hub.timer.stop()
        hub.timer.timeout.disconnect(hub.tick)
        if hub.windows or hub.pins:
            raise RuntimeError("Qt hub still owns attached windows or pins")
        hub.cache.clear()
        hub.cacheBytes = 0
        hub.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def normalTrial(root, arm, count, warmup, app):
    import grpc
    from examples.runtime_pages_p2 import pacedProject, pluginRoots
    from examples.runtime_pages_p3 import pageConfiguration
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
    from emo_master.apps.runtime.presentation.service import PresentationService
    from emo_master.core.presentation.models import Presentation
    from emo_master.ui.presentation.hub import DisplayHub
    from emo_master.ui.presentation.renderer import RuntimePages

    root.mkdir()
    clients, windows, ages, resourceRows, uiResources = [], [], [], [], []
    runtime = presentation = server = hub = channel = sampler = None
    result = None
    stamps = {"trial_start_ns": time.perf_counter_ns(), "terminal_observed_ns": None}
    cleanupErrors = []
    try:
        sampler = BoundedSampler()
        ownerBefore = sampler.sampleNow({"owner": os.getpid()})
        project = pacedProject(root, count=count, large=True)
        # Prepared debug resolves bindings; normal Run uses the normal loader.
        # An explicit absolute file parameter is identical across all arms.
        project.workflows["detect"].nodes[1].params["imagePath"] = str(root / "input.png")
        config = pageConfiguration(project.presentation.model_dump()["dataSources"], project.presentation.model_dump()["resultScopes"])
        config["pages"]["detail"]["components"] = [c for c in config["pages"]["detail"]["components"] if c["type"] != "table"]
        project.presentation = Presentation.model_validate(config)
        (root / "project.json").write_text(project.model_dump_json(), encoding="utf-8")
        runtime = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs", pluginRootPaths=pluginRoots(root))
        presentation = PresentationService(runtime, root / "display")
        server = AioRuntimeServer(runtime, presentation)
        address = f"127.0.0.1:{server.port}"
        channel = grpc.insecure_channel(address)
        stub, display = rpc.RuntimeServiceStub(channel), rpc.DisplayServiceStub(channel)
        capabilities = display.Capabilities(pb.DisplayEmpty(), timeout=5)
        required = {"normal_start_capture", "legacy_snapshot_policy_v1", "preview_snapshot_origin_v1", "start_request_lookup"}
        if not required.issubset(capabilities.capabilities):
            raise RuntimeError("normal policy benchmark capabilities unavailable")
        loaded = stub.LoadProject(pb.LoadProjectRequest(project_path=str(root)), timeout=15)
        if not loaded.ok:
            raise RuntimeError(loaded.message)
        request = buildStartRequest(project.project.projectId, arm, capabilities.runtime_instance_id, uuid4().hex)
        stamps["start_request_ns"] = time.perf_counter_ns()
        reply = stub.StartJob(request, timeout=10)
        stamps["start_reply_ns"] = time.perf_counter_ns()
        if not reply.ok or not reply.job_id or reply.legacy_snapshot_policy != ARM_SPECS[arm]["policy"]:
            raise RuntimeError("normal StartJob did not accept exact requested policy: " + str(reply))
        job = reply.job_id
        if ARM_SPECS[arm]["capture"]:
            clients = [base.MeasuredSession(address, job) for _ in range(2)]
            hub = DisplayHub(clients[0])
            windows = [RuntimePages(project.presentation, hub=hub, label=f"Normal Run / {request.legacy_snapshot_policy} / 1080p 5Hz") for _ in range(2)]
            screen = app.primaryScreen().availableGeometry()
            width = max(460, min(1060, screen.width()//2-20))
            for index, window in enumerate(windows):
                window.resize(width, min(720, screen.height()-60))
                window.move(screen.x()+index*(width+10), screen.y()+20)
                window.show()
            windows[1].navigate("detail")
        lastData, lastUi = [None]*len(clients), [None]*len(windows)
        nextSample = 0
        nextNative = 0
        stamps["observation_start_ns"] = time.perf_counter_ns()
        deadline = time.perf_counter()+OBSERVATION_SECONDS

        def observe(stamp):
            for index, client in enumerate(clients):
                with client.lock:
                    values = [r.timing.scopeEndedNs for r, _images, _failures in client.latest.values()
                        if r.identity.resultOrdinal > warmup and r.timing]
                if values:
                    lastData[index] = max(values)
            available = []
            for index, window in enumerate(windows):
                values = [s.result.timing.scopeEndedNs for s in window.displayed.values()
                    if s.result.timing and s.result.identity.resultOrdinal > warmup]
                available.append(bool(values))
                if values:
                    lastUi[index] = max(values)
            ages.append({"time_ns": stamp, "model_ms": [(stamp-v)/1e6 if v else None for v in lastData],
                "ui_ms": [(stamp-v)/1e6 if v else None for v in lastUi], "ui_available": available})

        while time.perf_counter() < deadline:
            app.processEvents()
            stamp = time.perf_counter_ns()
            if stamp >= nextSample:
                nextSample = stamp+40_000_000
                resourceRows.append({"time_ns": stamp, **presentation.resourceStats()})
                if hub is not None:
                    uiResources.append({"time_ns": stamp, **hub.stats()})
                observe(stamp)
                if stamp >= nextNative:
                    nextNative = stamp+400_000_000
                    pids = {"owner": os.getpid()}
                    record = runtime.jobRepository.get(job)
                    if record.pid:
                        pids["job"] = record.pid
                    if presentation.exporter:
                        pids.update({f"exporter-{slot['index']}": slot["process"].pid
                            for slot in presentation.exporter.slots if slot["process"] is not None})
                    base.OWNER_TRACE.call("observer.enqueue_resource_sample", sampler.request, pids, stamp)
                if runtime.jobRepository.get(job).isTerminal:
                    stamps["terminal_observed_ns"] = time.perf_counter_ns()
                    break
            time.sleep(.003)
        endObservation = time.perf_counter()+FINAL_OBSERVATION_SECONDS
        while time.perf_counter() < endObservation:
            app.processEvents()
            observe(time.perf_counter_ns())
            time.sleep(.01)
        stamps["observation_end_ns"] = time.perf_counter_ns()
        consumerRows = [_consumerResult(c, warmup) for c in clients]
        pace = [json.loads(event.payloadJson)["metrics"] for event in runtime.eventStore.read(job)
            if event.eventType == "node.completed" and event.nodeId == "pace"]
        result = {"arm": arm, "legacy_snapshot_policy": request.legacy_snapshot_policy,
            "capture_enabled": request.capture_presentation, "enabled": request.capture_presentation,
            "expected_source_ids": sorted(project.presentation.dataSources),
            "job": job, "job_status": runtime.jobRepository.get(job).status,
            "owner_resources_before": ownerBefore,
            "request": {"project_id": request.project_id, "workflow_id": request.workflow_id,
                "legacy_snapshot_policy": request.legacy_snapshot_policy, "capture_presentation": request.capture_presentation,
                "start_request_id": request.start_request_id, "expected_runtime_instance_id": request.expected_runtime_instance_id},
            "accepted_policy": reply.legacy_snapshot_policy,
            "mode": runtime.jobRepository.get(job).executionMode, "timestamps": stamps,
            "pace": pace, "max_schedule_lateness_ms": max([(p["actual"]-p["due"])*1000 for p in pace[warmup:]] or [0]),
            "consumers": consumerRows, "ui": [_windowResult(w, consumerRows, warmup) for w in windows],
            "age_samples": ages, "resource_samples": resourceRows, "ui_resources": uiResources,
            "export": dict(presentation.exporter.stats) if presentation.exporter else {},
            "export_errors": list(presentation.exporter.errors) if presentation.exporter else [],
            "supervisor_errors": list(runtime.jobSupervisor.presentationErrors),
            "qt_platform": app.platformName(), "control_transport": "normal RuntimeService RPC; idle presentation owner in all arms; no exporter/capture allocation for all_off",
            "input_sha256": hashlib.sha256((root/"input.png").read_bytes()).hexdigest()}
    finally:
        stamps["cleanup_start_ns"] = time.perf_counter_ns()
        def attempt(label, action):
            try:
                base.OWNER_TRACE.call("cleanup."+label, action)
            except BaseException as error:
                cleanupErrors.append({"owner": label, "error": repr(error)})
        attempt("qt", lambda: retireGui(windows, hub, app))
        for index, client in enumerate(clients):
            attempt(f"client_{index}", client.close)
        if channel:
            attempt("control_channel", channel.close)
        if server:
            attempt("server", server.close)
        if runtime:
            attempt("runtime", runtime.close)
        if sampler:
            def finalSample():
                sampled = sampler.sampleNow({"owner": os.getpid()})
                if result is not None:
                    result["owner_resources_after_cleanup"] = sampled
            attempt("post_cleanup_native_sample", finalSample)
            attempt("sampler", sampler.close)
        stamps["cleanup_end_ns"] = time.perf_counter_ns()
        if result is not None:
            result["sampler"] = sampler.report() if sampler else {}
            result["cleanup_errors"] = cleanupErrors
        if cleanupErrors:
            raise RuntimeError("normal policy trial cleanup incomplete: " + json.dumps(cleanupErrors))
    return result


def executeTrial(args):
    os.environ["QT_QPA_PLATFORM"] = args.qt_platform
    os.environ["EMO_R3_TRACE_DIR"] = str(args.output)
    from PySide2.QtWidgets import QApplication
    from emo_master.apps.runtime.jobs import supervisor
    from emo_master.apps.runtime.presentation import service
    from emo_master.apps.runtime.preview.store import PreviewAssetStore
    from emo_master.clients.runtime import display_session
    from emo_master.ui.presentation.renderer import RuntimePages
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    base.OWNER_TRACE = base.Trace("owner")
    observed = {}
    overflow = [0]
    result = None
    assetTrace = None
    assetTraceSummary = None
    creditTrace = None
    creditTraceSummary = None
    if getattr(args, "asset_split_trace", False):
        from scripts.r3_asset_split_trace import AssetSplitTrace, installed
        assetTrace = AssetSplitTrace(passive_markers=getattr(args, "passive_rpc_markers", False))
    if getattr(args, "export_credit_trace", False):
        from scripts.r3_export_credit_trace import ExportCreditTrace, installed as creditInstalled
        creditTrace = ExportCreditTrace()
    try:
        with base.patches() as patch:
            patch(supervisor, "runJobProcess", measuredJob)
            patch(service, "ExportPool", base.MeasuredPool)
            original = display_session.decodeResult
            def decode(wire):
                item = original(wire)
                key = item.identity.resultKey
                if key in observed or len(observed) < 128:
                    observed[key] = {"ordinal": item.identity.resultOrdinal, "status": item.status,
                        "mode": item.identity.mode, "source_outcomes": {s.sourceId: {"state": s.state,
                            "reason": s.reasonCode, "detail": (s.reason or "")[:256],
                            "image_present": s.image is not None} for s in item.sources}}
                else:
                    overflow[0] += 1
                for source in item.sources:
                    if source.image:
                        identity = source.image.resourceId
                        if identity in base.RESOURCE_IDENTITIES or len(base.RESOURCE_IDENTITIES) < 256:
                            base.RESOURCE_IDENTITIES[identity] = {"ordinal": item.identity.resultOrdinal,
                                "result_key": key, "source": source.sourceId}
                        else:
                            overflow[0] += 1
                return item
            patch(display_session, "decodeResult", decode)
            base.wrap(base.OWNER_TRACE, patch, display_session, "decodePng", "client.png_decode")
            base.wrap(base.OWNER_TRACE, patch, RuntimePages, "submit", "gui.apply", base.guiDetails)
            base.wrap(base.OWNER_TRACE, patch, PreviewAssetStore, "promote", "terminal.legacy_promotion")
            root = args.output / "work-trial"
            with installed(assetTrace, patch, ROOT) if assetTrace is not None else nullcontext():
                with creditInstalled(creditTrace, patch, ROOT) if creditTrace is not None else nullcontext():
                    result = normalTrial(root, args.arm, args.count, args.warmup, app)
            (args.output / "owners-closed.json").write_text(json.dumps({
                "all_original_trial_close_calls_returned": True}), encoding="utf-8")
    finally:
        if creditTrace is not None:
            try:
                creditTraceSummary = creditTrace.save(args.output / "export-credit.json")
            except Exception as error:
                creditTraceSummary = {"enabled": True, "complete": False,
                    "save_error_type": type(error).__name__, "performance_verdict": "NOT_EVALUATED"}
        if assetTrace is not None:
            try:
                assetTraceSummary = assetTrace.save(args.output / "asset-split.json")
            except Exception as error:
                # Optional evidence must not replace a real trial/cleanup error.
                assetTraceSummary = {"enabled": True, "complete": False,
                    "save_error_type": type(error).__name__, "performance_verdict": "NOT_EVALUATED"}
        base.OWNER_TRACE.save()
        for pid, trace in base.ENCODER_TRACES.items():
            content = json.dumps(trace, separators=(",", ":"))
            if len(content.encode()) > base.TRACE_BYTES:
                raise RuntimeError("exporter trace file budget exceeded")
            (args.output/f"trace-exporter-{pid}.json").write_text(content, encoding="utf-8")
    if result is not None:
        traces = [json.loads(path.read_text(encoding="utf-8")) for path in args.output.glob("trace-*.json")]
        addAccounting(result, args.count, args.warmup, executionRows(traces))
        result["observed_result_outcomes"] = observed
        result["outcome_overflow"] = overflow[0]
        result["instrumentation"] = "measurement-only inclusive wrappers, identical detect timing in every arm; native resource enumeration on bounded background owner"
        if creditTraceSummary is not None:
            result["export_credit_trace"] = creditTraceSummary
        if assetTraceSummary is not None:
            # This harness freezes one image source per result. Keep its original
            # counters/denominators and cross-check the optional trace against them.
            expectedReads = sum(c["stats"]["decoded"] + c["stats"]["read_failed"] for c in result["consumers"])
            assetTraceSummary["read_attempts_from_original_single_image_stats"] = expectedReads
            assetTraceSummary["calls_match_original_stats"] = all(
                assetTraceSummary.get(key) == expectedReads for key in ("client_calls_started", "client_calls_finished"))
            assetTraceSummary["complete"] = assetTraceSummary["complete"] and assetTraceSummary["calls_match_original_stats"]
            result["asset_split_trace"] = assetTraceSummary
        content = json.dumps(result, ensure_ascii=True)
        if len(content.encode()) > RAW_BYTES:
            raise RuntimeError("raw trial evidence exceeds 24 MiB")
        (args.output/"trial.json").write_text(content, encoding="utf-8")


def exactCoverage(result):
    denominator = result["denominators"]
    execution = not any(denominator[key] for key in (
        "missing_execution_ordinals", "missing_measured_execution_ordinals", "duplicate_execution_ordinals",
        "out_of_range_execution_rows", "failed_execution_rows"))
    schedule = not any(result["pace_denominators"][key] for key in (
        "missing_indices", "duplicate_indices", "unexpected_indices"))
    execution = execution and schedule
    if not result["capture_enabled"]:
        return execution and not result["consumers"] and not result["ui"]
    consumers = len(result["consumers"]) == 2 and all(not any(c[key] for key in (
        "missing_measured_ordinals", "missing_decoded_ordinals", "missing_applied_ordinals",
        "unexpected_ordinals", "duplicate_ordinal_keys")) for c in result["consumers"])
    windows = len(result["ui"]) == 2 and all(not any(w[key] for key in (
        "missing_committed_ordinals", "missing_painted_ordinals", "unexpected_committed_ordinals",
        "unexpected_painted_ordinals")) for w in result["ui"])
    expectedOrdinals = set(range(denominator["warmup_inputs"]+1, denominator["planned_inputs"]+1))
    outcomes = [row for row in result.get("observed_result_outcomes", {}).values()
                if row["ordinal"] > denominator["warmup_inputs"]]
    outcomeCounts = Counter(row["ordinal"] for row in outcomes)
    sources = (set(outcomeCounts) == expectedOrdinals and all(value == 1 for value in outcomeCounts.values())
        and all(row["status"] == "COMPLETE" and row["mode"] == "runtime"
            and set(row["source_outcomes"]) == set(result["expected_source_ids"])
            and all(source["state"] == "AVAILABLE" for source in row["source_outcomes"].values())
            for row in outcomes))
    return execution and consumers and windows and sources


def groupAssessment(rows):
    if set(rows) != set(ARM_SPECS):
        return {"pass": False, "reason": "missing arm; denominator retained", "missing_arms": sorted(set(ARM_SPECS)-set(rows))}
    baseline, allQt, noneQt = rows["all_off"], rows["all_qt"], rows["none_qt"]
    def compare(baseline, display):
        # Keep missing schedule evidence missing in raw rows. The reused strict
        # numeric predicate receives infinity only to make that comparison fail.
        checked = dict(display)
        if checked.get("max_schedule_lateness_ms") is None:
            checked["max_schedule_lateness_ms"] = float("inf")
        return base.pairAssessment(baseline, checked, True)
    comparisons = {"all_qt_vs_all_off": compare(baseline, allQt),
        "none_qt_vs_all_off": compare(baseline, noneQt),
        "none_qt_vs_all_qt": compare(allQt, noneQt)}
    coverage = {arm: exactCoverage(result) for arm, result in rows.items()}
    for name, pair in (("all_qt_vs_all_off", ("all_off", "all_qt")),
                       ("none_qt_vs_all_off", ("all_off", "none_qt")),
                       ("none_qt_vs_all_qt", ("all_qt", "none_qt"))):
        comparisons[name]["exact_ordinal_coverage"] = all(coverage[arm] for arm in pair)
        comparisons[name]["pass"] = comparisons[name]["pass"] and comparisons[name]["exact_ordinal_coverage"]
    return {"comparisons": comparisons, "exact_ordinal_coverage": coverage,
        "pass": all(row["pass"] for row in comparisons.values()),
        "interpretation": "ALL+Qt vs ALL-off is incremental page cost; NONE+Qt vs ALL+Qt isolates explicit snapshot policy; NONE+Qt vs ALL-off is combined configuration. Raw all-window age gates apply to every comparison."}


def trialEvidence(directory, output, warmup, arm):
    """The unchanged per-trial accounting shared by both supervised runners."""
    path = directory / "trial.json"
    if not path.exists():
        return None, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    traces = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("trace-*.json"))]
    ordinals = {event["result_key"]: event["ordinal"] for trace in traces for event in trace["rows"]
        if event.get("result_key") and event.get("ordinal") is not None}
    for trace in traces:
        for event in trace["rows"]:
            if event.get("ordinal") is None and event.get("result_key") in ordinals:
                event["ordinal"] = ordinals[event["result_key"]]
    roles = dict(Counter(t["role"] for t in traces))
    requiredRoles = {"execution": 1, "job": 1, "owner": 1}
    if ARM_SPECS[arm]["capture"]:
        requiredRoles["exporter"] = 2
    dropped = sum(t["dropped_rows"] for t in traces)
    fields = dict(stage_summary=base.summaryStages(traces, warmup), trace_roles=roles,
        trace_dropped_rows=dropped, raw_evidence=str(path.relative_to(output)),
        trace_complete=roles == requiredRoles and not dropped and not payload["outcome_overflow"]
            and not payload["record_retention"]["capacity_reached"] and not payload["sampler"]["overflow"]
            and not payload["sampler"]["errors"] and payload["sampler"]["retired"]
            and set(payload["sampler"]["observed_roles"]) >= ({"owner", "job", "exporter-0", "exporter-1"} if ARM_SPECS[arm]["capture"] else {"owner", "job"}),
        phases=payload["phases"])
    return payload, fields


def run(args):
    from p2_validate import identity, git
    before = identity()
    manifest = {"started_ns": time.perf_counter_ns(), "head": git("rev-parse", "HEAD"),
        "dirty": git("status", "--short"), "source_before": before,
        "original_script_hashes": {name: hashlib.sha256((ROOT/"scripts"/name).read_bytes()).hexdigest()
            for name in ("p2_measure.py", "p3_measure.py", "r3_measure.py")},
        "python": sys.version, "os": platform.platform(), "configuration": vars(args)|{"output": str(args.output)},
        "runtime_route": "LoadProject and normal StartJob RPC, explicit ALL/NONE policy, no Prepare/Start and no spec mutation",
        "load": "fixed seed 20260926; 1920x1080x3; scheduled 5 Hz; 96 inputs/8 warmup; three cyclic arm groups",
        "clock": "same-host perf_counter_ns; inclusive detect-workflow timing in all arms; nested stages overlap",
        "historical_results": "UNCHANGED, including original Windows all-window age and Qt missing-frame FAIL",
        "native_claim": "Qt paint callbacks only; offscreen/minimal never pass native acceptance",
        "not_proven": ["actual user project", "field SLA", "five pages/fifty controls", "long-run steady state"],
        "disk_preflight": base.diskPreflight(args.output),
        "bounds": {"trace_rows_per_process": base.TRACE_LIMIT, "trace_file_bytes": base.TRACE_BYTES,
            "log_bytes_per_trial": base.LOG_BYTES, "raw_trial_bytes": RAW_BYTES, "maximum_trials": 9,
            "sampler_rows": 256, "sampler_pending_requests": 1, "observation_seconds": OBSERVATION_SECONDS,
            "watchdog_seconds": 90, "export_deadline_seconds": .5, "asset_rpc_deadline_seconds": .5},
        "measurement_status": "RUNNING", "performance_status": "NOT_ASSESSED", "trials": [], "groups": []}
    base.writeManifest(args.output, manifest)
    for group in range(args.groups):
        rows = {}
        for position, arm in enumerate(armOrder(group)):
            directory = args.output/f"group-{group}-{position}-{arm}"
            directory.mkdir()
            command = [sys.executable, str(Path(__file__).resolve()), "--child", "--output", str(directory),
                "--arm", arm, "--count", str(args.count), "--warmup", str(args.warmup), "--qt-platform", args.qt_platform]
            if getattr(args, "asset_split_trace", False):
                command.append("--asset-split-trace")
            if getattr(args, "passive_rpc_markers", False):
                command.append("--passive-rpc-markers")
            if getattr(args, "export_credit_trace", False):
                command.append("--export-credit-trace")
            entry = {"group": group, "position": position, "arm": arm, "disk_preflight": base.diskPreflight(directory),
                "watchdog": base.supervisedTrial(command, directory)}
            payload, fields = trialEvidence(directory, args.output, args.warmup, arm)
            if payload is not None:
                entry.update(fields)
                if getattr(args, "asset_split_trace", False):
                    entry["asset_split_trace"] = payload.get("asset_split_trace", {"enabled": True, "complete": False})
                if getattr(args, "export_credit_trace", False):
                    entry["export_credit_trace"] = payload.get("export_credit_trace", {"enabled": True, "complete": False})
                rows[arm] = payload
            manifest["trials"].append(entry)
            base.writeManifest(args.output, manifest)
            print(json.dumps({"trial": entry}), flush=True)
        manifest["groups"].append({"group": group, "order": armOrder(group), **groupAssessment(rows)})
        base.writeManifest(args.output, manifest)
    manifest["source_after"] = identity()
    manifest["source_stable"] = before == manifest["source_after"]
    formal = args.count == 96 and args.warmup == 8 and args.groups == 3
    valid = manifest["source_stable"] and all(t["watchdog"]["status"] == "PASS" and t.get("trace_complete") for t in manifest["trials"])
    if getattr(args, "asset_split_trace", False):
        manifest["asset_split_status"] = "COMPLETE" if all(
            t.get("asset_split_trace", {}).get("complete") for t in manifest["trials"]) else "INCOMPLETE"
        valid = valid and manifest["asset_split_status"] == "COMPLETE"
    if getattr(args, "export_credit_trace", False):
        manifest["export_credit_status"] = "COMPLETE" if all(
            t.get("export_credit_trace", {}).get("complete") for t in manifest["trials"]) else "INCOMPLETE"
        valid = valid and manifest["export_credit_status"] == "COMPLETE"
    instrumented = getattr(args, "asset_split_trace", False) or getattr(args, "export_credit_trace", False)
    manifest["measurement_status"] = "VALID" if valid else "INVALID"
    manifest["performance_status"] = (("PASS" if all(g["pass"] for g in manifest["groups"]) else "FAIL")
        if formal and valid and not instrumented else "NOT_ASSESSED")
    manifest["finished_ns"] = time.perf_counter_ns()
    base.writeManifest(args.output, manifest)
    print(json.dumps({"measurement_status": manifest["measurement_status"], "performance_status": manifest["performance_status"], "groups": manifest["groups"]}), flush=True)
    return int(not valid or (formal and not instrumented and manifest["performance_status"] != "PASS"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=96)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--groups", type=int, default=3)
    parser.add_argument("--qt-platform", default="windows" if os.name == "nt" else "offscreen")
    parser.add_argument("--asset-split-trace", action="store_true",
        help="opt-in loopback RPC attribution; preserves original arms/denominators and never reports performance PASS")
    parser.add_argument("--passive-rpc-markers", action="store_true",
        help="optional peer/next-loop-turn/RPC-task-done observations; requires --asset-split-trace")
    parser.add_argument("--export-credit-trace", action="store_true",
        help="opt-in existing exporter reply/callback/credit boundaries; never changes ownership or reports performance PASS")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--arm", choices=tuple(ARM_SPECS), default="all_off", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.passive_rpc_markers and not args.asset_split_trace:
        parser.error("--passive-rpc-markers requires --asset-split-trace")
    if not 2 <= args.count <= 96 or not 0 <= args.warmup < args.count or not 1 <= args.groups <= 3:
        parser.error("count 2..96, warmup 0..count-1, groups 1..3; reduced runs are diagnostic only")
    args.output = args.output.resolve()
    if any(args.output.is_relative_to(ROOT/name) for name in ("src", "tests", "scripts", "proto", "examples", "prototypes")):
        parser.error("evidence must be outside source identity directories")
    if args.child:
        executeTrial(args)
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        return run(args)
    except BaseException as error:
        (args.output/"failure.json").write_text(json.dumps({"measurement_status": "INVALID", "error": repr(error), "at_ns": time.perf_counter_ns()}), encoding="utf-8")
        raise


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
