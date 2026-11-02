"""Read-only synthetic probes; no devices, field projects, or live Runtime."""
from pathlib import Path
import json
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from emo_master.apps.designer.services.runtime_worker import _followJobEvents
from emo_master.apps.designer.page_designer.runtime_logs import PreviewRuntimeLogs
from emo_master.apps.runtime.devices.diagnostics import DeviceDiagnostics
from emo_master.apps.runtime.events.event_store import EventStore
from emo_master.apps.runtime.events.jsonl_writer import RuntimeJsonlLogWriter
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.presentation.collector import unavailable
from emo_master.apps.runtime.presentation.operational_log import PresentationLog
from tests.runtime.presentation.test_closure_failures import assembler, opened, seal


def late_failure():
    store = EventStore()
    service, credits = assembler()
    credits.semaphore = threading.BoundedSemaphore(2)
    service.logs = {"job": PresentationLog(store.append, "audit")}
    older = opened(service, credits, 1)
    pending = seal(older, {"sourceId": "n", "pendingImage": True})
    pending["sealedAt"] = time.monotonic()
    service._consume("job", pending)
    newer = opened(service, credits, 2)
    service._consume("job", seal(newer, unavailable(
        "n", "BUDGET_EXCEEDED", "source image lane is busy; capture never waits")))
    service._exported({"key": older, "sourceId": "n"}, None, "EXPORT_FAILED")
    service.logs["job"].finish()
    return {
        "closed_results": [{"ordinal": r.identity.resultOrdinal,
                            "reason": r.sources[0].reasonCode}
                           for r in service.store.history],
        "operational_events": [{"level": e.level, "code": e.code}
                               for e in store.read("job")],
        "credits_returned": credits.released,
    }


def file_filter():
    with tempfile.TemporaryDirectory(prefix="emo-log-audit-") as temporary:
        directory = Path(temporary)
        writer = RuntimeJsonlLogWriter(directory)
        store = EventStore()
        events = [store.append("audit", name, "synthetic audit", level=level)
                  for name, level in [("preview.snapshot.failed", "WARN"),
                                      ("node.failed", "ERROR"),
                                      ("presentation.source.unavailable", "ERROR")]]
        accepted = {e.eventType: writer.enqueue(e) for e in events}
        close_error = writer.close()
        rows = [json.loads(line) for path in directory.rglob("*.jsonl")
                for line in path.read_text(encoding="utf-8").splitlines()]
        return {"event_history": [e.eventType for e in store.read("audit")],
                "writer_accepted": accepted,
                "file_events": [r["eventType"] for r in rows],
                "writer_close_error": close_error}


def stream_disconnect():
    emissions = []
    calls = []

    class Signal:
        def __init__(self, name):
            self.name = name

        def emit(self, value):
            emissions.append({"signal": self.name,
                              "eventType": getattr(value, "eventType", ""),
                              "status": getattr(value, "status", "")})

    class Client:
        def iterJobEvents(self, jobId, afterSequence=0, follow=True):
            calls.append({"job": jobId, "after": afterSequence})
            if len(calls) == 1:
                yield SimpleNamespace(sequence=1, eventType="job.started")
                raise ConnectionError("synthetic stream disconnect")
            if len(calls) == 2:
                yield SimpleNamespace(sequence=2, eventType="job.completed")

        def getJobStatus(self, jobId):
            return SimpleNamespace(status="RUNNING" if len(calls) == 1 else "COMPLETED")

    stop = threading.Event()
    worker = SimpleNamespace(runtimeClient=Client(), _stopEvent=stop,
                             stopRequested=stop.is_set,
                             setActiveStream=lambda _: None, clearActiveStream=lambda: None,
                             **{name: Signal(name) for name in
                                ("eventReceived", "statusChanged", "failed", "uncertain")})
    _followJobEvents(worker, "audit")
    return {"stream_calls": calls, "emissions": emissions}


def observed_job_errors():
    class Stream(list):
        def cancel(self):
            pass

    stream = Stream(pb.JobEvent(job_id="external", sequence=index, event_type=kind,
                                level="ERROR", payload_json="{}", iteration_path_json="[]")
                    for index, kind in enumerate(("node.failed", "job.failed",
                                                   "presentation.source.unavailable"), 1))
    observer = object.__new__(PreviewRuntimeLogs)
    observer.stop = threading.Event()
    observer.lock = threading.Lock()
    observer.events = queue.Queue(maxsize=128)
    observer.streams = {}
    observer.cursors = {"external": 0}
    observer.stub = SimpleNamespace(StreamJobEvents=lambda _: stream)
    observer._read("external")
    return {"received": [e.event_type for e in stream],
            "forwarded": [value["eventType"] for kind, value in observer.take()
                          if kind == "event"], "cursor": observer.cursors["external"]}


def device_failure_level():
    events = []
    diagnostics = DeviceDiagnostics()
    diagnostics.writer = SimpleNamespace(enqueue=events.append)
    diagnostics.append({"projectId": "audit", "projectLoadGeneration": "load-1"},
                       "simulated", "device.connect.failed", "synthetic connect failure",
                       code="E_DEVICE_CONNECT")
    event, = events
    return {"eventType": event.eventType, "level": event.level, "code": event.code,
            "jobId": event.jobId, "payload_code": json.loads(event.payloadJson)["code"]}


result = {
    "scope": "Synthetic probes against current dirty source; no field verification",
    "late_failure": late_failure(),
    "jsonl_filter": file_filter(),
    "stream_disconnect": stream_disconnect(),
    "observed_job_errors": observed_job_errors(),
    "device_failure_level": device_failure_level(),
}
output = Path(__file__).with_name("probe-results.json")
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
