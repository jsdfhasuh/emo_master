"""Paired native Qt incremental measurement, fixed 1080p input, 5Hz, old preview retained.

All raw trials are printed, including failed/missing results. Run under
p2_validate's process-tree watchdog. Actual native paint events are observed; not physical screen presentation or field-PC acceptance.
"""
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from examples.runtime_pages_p2 import pacedProject, pluginRoots  # noqa: E402
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from emo_master.apps.runtime.presentation.service import PresentationService  # noqa: E402
from emo_master.clients.runtime.display_session import DisplaySession  # noqa: E402
from p2_resources import resources as processResources  # noqa: E402
from examples.runtime_pages_p3 import pageConfiguration  # noqa: E402
from emo_master.core.presentation.models import Presentation  # noqa: E402
from emo_master.ui.presentation.hub import DisplayHub  # noqa: E402
from emo_master.ui.presentation.renderer import RuntimePages  # noqa: E402
from PySide2.QtWidgets import QApplication  # noqa: E402


def percentile(values):
    return sorted(values)[min(len(values) - 1, int(len(values) * .95))] if values else None


def trial(root, enabled, count=96, warmup=8):
    root.mkdir()
    runtime = RuntimeService(dbPath=root / "runtime.db", workspaceRoot=root / "jobs", pluginRootPaths=pluginRoots(root))
    presentation = PresentationService(runtime, root / "display")
    server = AioRuntimeServer(runtime, presentation)
    clients = []
    windows = []
    hub = None
    uiResources = []
    resources = []
    ages = []
    processes = []
    try:
        project = pacedProject(root, count=count, large=True)
        config = pageConfiguration(project.presentation.model_dump()['dataSources'], project.presentation.model_dump()['resultScopes'])
        # Keep the exact P2 image/count capture workload in both paired arms.
        # The table is covered by separate real low-rate functional tests.
        config['pages']['detail']['components'] = [c for c in config['pages']['detail']['components'] if c['type'] != 'table']
        project.presentation = Presentation.model_validate(config)
        record = presentation.prepare(project, root)
        job = presentation.start(record.snapshot.snapshotId, capture=True, measure=True)
        clients = [DisplaySession(f"127.0.0.1:{server.port}", job) for _ in range(2)]
        if enabled:
            hub = DisplayHub(clients[0])
            windows = [RuntimePages(project.presentation, hub=hub, label='1080p/5Hz 测量 · 本地固定输入') for _ in range(2)]
            screen = QApplication.primaryScreen().availableGeometry()
            width = max(460, min(1060, screen.width() // 2 - 20))
            for index, window in enumerate(windows):
                window.resize(width, min(720, screen.height()-60))
                window.move(screen.x()+index*(width+10), screen.y()+20)
                window.show()
            windows[1].navigate('detail')
        deadline = time.perf_counter() + 45
        nextSample = 0
        uiAges = []
        lastData = [None for _ in clients]
        lastUi = [None for _ in windows]
        uiAvailable = []
        def observeAges(stamp):
            for index, client in enumerate(clients):
                with client.lock:
                    values = [value[0].timing.scopeEndedNs for value in client.latest.values()
                              if value[0].identity.resultOrdinal > warmup and value[0].timing]
                if values:
                    lastData[index] = max(values)
            ages.append([(stamp - value) / 1e6 if value else None for value in lastData])
            availability = []
            for index, window in enumerate(windows):
                values = [s.result.timing.scopeEndedNs for s in window.displayed.values()
                          if s.result.timing and s.result.identity.resultOrdinal > warmup]
                availability.append(bool(values))
                if values:
                    lastUi[index] = max(values)
            uiAvailable.append(availability)
            # A loading/invalid view doesn't erase the age of the last commit.
            uiAges.append([(stamp-value)/1e6 if value else None for value in lastUi])
        while time.perf_counter() < deadline:
            QApplication.processEvents()
            stamp = time.perf_counter_ns()
            if stamp < nextSample:
                time.sleep(.003)
                continue
            nextSample = stamp + 40_000_000
            if hub:
                uiResources.append({'time_ns': stamp, **hub.stats()})
            resources.append({"time_ns": stamp, **presentation.resourceStats()})
            observeAges(stamp)
            if len(resources) % 10 == 0:
                samples = {"owner": processResources()}
                for index, slot in enumerate(presentation.exporter.slots):
                    if slot["process"] is not None:
                        samples[f"exporter{index}"] = processResources(slot["process"].pid)
                try:
                    samples["job"] = processResources(runtime.jobRepository.get(job).pid)
                except OSError:
                    samples["job"] = {"status": "EXITED"}
                processes.append({"time_ns": stamp, "processes": samples})
            if runtime.jobRepository.get(job).isTerminal:
                break
            time.sleep(.01)
        # Bounded final delivery observation; task export/read deadlines stay 500ms.
        endObservation = time.perf_counter() + .5
        while time.perf_counter() < endObservation:
            QApplication.processEvents()
            observeAges(time.perf_counter_ns())
            time.sleep(.01)
        timings = list(presentation.timings[job])
        measured = timings[warmup:]
        execution = [(r["endNs"] - r["startNs"]) / 1e6 for r in measured]
        pace = [json.loads(event.payloadJson)["metrics"] for event in runtime.eventStore.read(job)
                if event.eventType == "node.completed" and event.nodeId == "pace"]
        consumerRows = []
        for client in clients:
            rows = [row for row in client.records if row["ordinal"] > warmup]
            latency = [(row["model_ns"] - row["scope_ended_ns"]) / 1e6 for row in rows if row["scope_ended_ns"] and row["model_ns"]]
            consumerRows.append({"stats": dict(client.stats), "rows": rows, "errors": list(client.errors),
                "unique_received": len({row["key"] for row in rows}),
                "unique_decoded": len({row["key"] for row in rows if "image" in row["decoded"] and not row["failures"]}),
                "unique_applied": len({row["key"] for row in rows if row["applied_to_live"]}),
                "p95_model_ms": percentile(latency)})
        scopeDeltas = [(measured[i]["startNs"] - measured[i-1]["startNs"]) / 1e6 for i in range(1, len(measured))]
        ui = []
        for window in windows:
            rows = [r for r in window.records if next((t['ordinal'] for t in clients[0].records if t['key']==r['key']), 0) > warmup]
            first = {}
            for row in rows:
                item = first.setdefault(row['key'], dict(row))
                if row.get('paint_ns'):
                    item['paint_ns'] = row['paint_ns']
            values = list(first.values())
            ui.append({'raw_rows': rows, 'unique_committed': len(values),
                'unique_painted': sum('paint_ns' in r for r in values),
                'p95_scope_to_gui_ms': percentile([(r['gui_ns']-r['scope_end_ns'])/1e6 for r in values if r['scope_end_ns']]),
                'p95_ready_to_gui_ms': percentile([(r['gui_ns']-r['ready_ns'])/1e6 for r in values]),
                'p95_scope_to_paint_ms': percentile([(r['paint_ns']-r['scope_end_ns'])/1e6 for r in values if r.get('paint_ns') and r['scope_end_ns']]),
                'p95_gui_to_paint_ms': percentile([(r['paint_ns']-r['gui_ns'])/1e6 for r in values if r.get('paint_ns')])})
        return {"enabled": enabled, "ui": ui, "ui_resources": uiResources, "ui_age_samples_ms": uiAges,
                "ui_available_samples": uiAvailable, "expected": count - warmup, "job": job,
                "job_status": runtime.jobRepository.get(job).status, "raw_timings": timings,
                "pace": pace, "actual_intervals_ms": scopeDeltas, "executed": len(measured),
                "mean_execution_ms": statistics.mean(execution) if execution else None,
                "p95_execution_ms": percentile(execution), "consumers": consumerRows,
                "achieved_hz": (len(measured) - 1) * 1e9 / (measured[-1]["startNs"] - measured[0]["startNs"]) if len(measured) > 1 else 0,
                "max_schedule_lateness_ms": max([(p["actual"] - p["due"]) * 1000 for p in pace[warmup:]] or [0]),
                "age_samples_ms": ages, "resource_samples": resources,
                "process_resource_samples": processes,
                "export": dict(presentation.exporter.stats), "export_errors": list(presentation.exporter.errors),
                "supervisor_errors": list(runtime.jobSupervisor.presentationErrors)}
    finally:
        for window in windows:
            window.close()
        for client in clients:
            client.close()
        server.close()
        runtime.close()
        presentation.close()


def main():
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    app = QApplication.instance() or QApplication([])
    trials = []
    with tempfile.TemporaryDirectory(prefix="emo-p3-measure-") as directory:
        for pair in range(3):
            for enabled in ([False, True] if pair % 2 == 0 else [True, False]):
                result = trial(Path(directory) / f"{pair}-{enabled}", enabled)
                result["pair"] = pair
                trials.append(result)
                print(json.dumps({"trial": result}), flush=True)
    pairs = []
    for pair in range(3):
        baseline = next(row for row in trials if row["pair"] == pair and not row["enabled"])
        display = next(row for row in trials if row["pair"] == pair and row["enabled"])
        regression = (display["p95_execution_ms"] / baseline["p95_execution_ms"] - 1) * 100
        complete = all(c["unique_received"] == c["unique_decoded"] == c["unique_applied"] == display["expected"] for c in display["consumers"])
        latency = all(c["p95_model_ms"] is not None and c["p95_model_ms"] <= 200 for c in display["consumers"])
        age = max([n for row in display["age_samples_ms"] for n in row if n is not None] or [float("inf")])
        uiComplete = all(c['unique_committed'] == c['unique_painted'] == display['expected'] for c in display['ui'])
        uiLatency = all(c['p95_scope_to_gui_ms'] is not None and c['p95_scope_to_gui_ms'] <= 200 for c in display['ui'])
        uiAge = max([n for row in display['ui_age_samples_ms'] for n in row if n is not None] or [float('inf')])
        pairs.append({"pair": pair, "max_sampled_ui_age_ms": uiAge, "ui_complete": uiComplete, "ui_latency_pass": uiLatency, "regression_percent": regression, "complete": complete,
                      "p95_model_pass": latency, "max_sampled_age_ms": age,
                      "achieved_hz": display["achieved_hz"], "schedule_lateness_ms": display["max_schedule_lateness_ms"],
                      "pass": uiComplete and uiLatency and uiAge <= 500 and complete and latency and age <= 500 and regression <= 5 and display["achieved_hz"] >= 4.95 and display["max_schedule_lateness_ms"] <= 200})
    passed = all(row["pass"] for row in pairs)
    print(json.dumps({"summary": pairs, "performance_status": "PASS" if passed else "FAIL",
                      "qt_platform": app.platformName(), "five_pages_fifty_controls": "NOT_RUN; two pages/two windows only", "rss_handles_steady_state": "NOT_RUN (short-window process samples retained; no soak acceptance)", "clock": "same-host perf_counter_ns; scope entry/end; age includes terminal-drain tail; no missing-result denominator removal",
                      "load": "fixed seed 20260926 1920x1080x3; 5Hz; 96 runs, first 8 warmup; 3 alternating pairs; existing PreviewSnapshotWriter enabled; both arms retain P2 capture and two clients, UI arm adds two windows sharing client0"}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
