"""Pure append-control lifecycle checks; no runtime, database, or child workload."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import weakref

import pytest

from scripts import r3_sqlite_append_controls as controls


class FakeConnection:
    def __init__(self, close_error=None):
        self.owner = threading.current_thread()
        self.close_error = close_error
        self.close_threads = []

    def close(self):
        self.close_threads.append(threading.current_thread())
        assert threading.current_thread() is self.owner
        if self.close_error is not None:
            raise self.close_error


class FakeStore:
    def __init__(self, factory=FakeConnection):
        self.factory = factory

    def _connect(self):
        return self.factory()


def on_other_thread(action):
    """Return worker results/errors to pytest instead of losing thread exceptions."""
    results, errors = [], []

    def run():
        try:
            results.append(action())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, name="append-control-test")
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    return results, errors


@pytest.fixture(autouse=True)
def no_real_workload(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("pure controls test attempted a real database or child workload")

    monkeypatch.setattr(controls.sqlite3, "connect", forbidden)
    monkeypatch.setattr(controls, "run_worker", forbidden)
    monkeypatch.setattr(controls.child_tools, "run_child", forbidden)


def test_manifest_has_exactly_sixteen_ordered_events_and_two_terminals():
    manifest = controls.event_manifest()
    expected = [
        ("job.started", ""), ("workflow.started", ""),
        ("node.started", "input"), ("node.completed", "input"),
        ("node.started", "output"), ("node.completed", "output"),
        ("workflow.completed", ""), ("job.completed", ""),
    ]
    assert len(manifest) == controls.EVENT_COUNT == 16
    assert [item["jobId"] for item in manifest] == ["append-A"] * 8 + ["append-B"] * 8
    for offset, job in ((0, "append-A"), (8, "append-B")):
        events = manifest[offset:offset + 8]
        assert [(item["eventType"], item["nodeId"]) for item in events] == expected
        assert [item["timestampMs"] for item in events] == list(range(1790841600000, 1790841600008))
        assert all(item["workflowRunId"] == f"run-{job}" for item in events)
    assert [(index, item["jobId"]) for index, item in enumerate(manifest)
            if item["eventType"] == "job.completed"] == [(7, "append-A"), (15, "append-B")]
    assert controls.manifest_hash(manifest) == controls.manifest_hash(controls.event_manifest())


def test_natural_hook_returns_exact_connection_without_retaining_or_closing_it():
    references, closes = [], []

    class NaturalConnection:
        def close(self):
            closes.append(self)

    def create():
        connection = NaturalConnection()
        references.append(weakref.ref(connection))
        return connection

    store = FakeStore(create)
    original = FakeStore.__dict__["_connect"]
    cohort = controls.ConnectCohort(store, hooked=True, retained=False)
    with cohort:
        connection = store._connect()
        assert connection is references[0]()
        assert type(connection) is NaturalConnection
        assert cohort.return_count == 1
        del connection
        assert references[0]() is None
        assert cohort.slots == []
    cohort.close_retained()
    assert closes == []
    assert FakeStore.__dict__["_connect"] is original
    assert cohort.report()["natural_finalization"] == "UNOBSERVED"


def test_connect_counts_only_successful_returns_and_preserves_original_exception():
    failure = OSError("factory failed")
    connection = FakeConnection()
    calls = []

    def create():
        calls.append(True)
        if len(calls) == 2:
            raise failure
        return connection

    store = FakeStore(create)
    with controls.ConnectCohort(store, hooked=True, retained=False) as cohort:
        assert store._connect() is connection
        with pytest.raises(OSError) as caught:
            store._connect()
        assert caught.value is failure
        assert store._connect() is connection
        assert cohort.return_count == 2
        assert cohort.unexpected_returns == 0
    assert len(calls) == 3
    assert connection.close_threads == []


def test_append_exception_restores_exact_original_descriptor():
    store = FakeStore()
    original = FakeStore.__dict__["_connect"]
    failure = RuntimeError("append failed after connect")
    cohort = controls.ConnectCohort(store, hooked=True, retained=False)
    with pytest.raises(RuntimeError) as caught:
        with cohort:
            store._connect()
            raise failure
    assert caught.value is failure
    assert FakeStore.__dict__["_connect"] is original
    assert cohort.report()["hook_restored"]
    assert cohort.report()["connection_count"] == 1


def test_retained_sixteen_handles_close_once_on_their_actual_creator_thread():
    store = FakeStore()
    cohort = controls.ConnectCohort(store, hooked=True, retained=True)
    with cohort:
        connections = [store._connect() for _ in range(16)]
        assert all(actual is expected for actual, expected in zip(cohort.slots, connections))
        with pytest.raises(RuntimeError, match="restore"):
            cohort.close_retained()
        assert all(connection.close_threads == [] for connection in connections)
    cohort.close_retained()
    cohort.close_retained()
    assert all(connection.close_threads == [threading.current_thread()] for connection in connections)
    assert cohort.slots == [None] * 16
    report = cohort.report()
    assert report["connection_count"] == 16
    assert report["overflow"] == 0
    assert [item["index"] for item in report["close_results"]] == list(range(16))
    assert all(item["closed"] and item["owner_ident"] == threading.get_ident()
               for item in report["close_results"])


def test_wrong_actual_thread_cannot_close_even_with_matching_recorded_ident():
    store = FakeStore()
    with controls.ConnectCohort(store, hooked=True, retained=True) as cohort:
        connections = [store._connect() for _ in range(16)]

    def foreign_close():
        # A numeric identifier alone must not establish ownership.
        cohort.owner_ident = threading.get_ident()
        cohort.close_retained()

    results, errors = on_other_thread(foreign_close)
    assert results == []
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert "actual creating thread" in str(errors[0])
    assert all(actual is expected for actual, expected in zip(cohort.slots, connections))
    assert all(connection.close_threads == [] for connection in connections)
    assert cohort.close_results == []
    cohort.close_retained()
    assert all(connection.close_threads == [threading.current_thread()] for connection in connections)


def test_close_fault_attempts_every_retained_handle_without_retrying_failed_close():
    connections = [FakeConnection(OSError("close failed") if index in {0, 7, 15} else None)
                   for index in range(16)]
    pending = iter(connections)
    store = FakeStore(lambda: next(pending))
    with controls.ConnectCohort(store, hooked=True, retained=True) as cohort:
        for _ in connections:
            store._connect()
    cohort.close_retained()
    report = cohort.report()
    assert len(report["close_results"]) == 16
    assert [item["index"] for item in report["close_results"] if not item["closed"]] == [0, 7, 15]
    assert all(item["error"] == {"type": "OSError", "message": "close failed"}
               for item in report["close_results"] if not item["closed"])
    assert cohort.slots == [None] * 16
    cohort.close_retained()
    assert cohort.report() == report
    assert all(connection.close_threads == [threading.current_thread()] for connection in connections)


def test_unexpected_store_and_background_returns_are_separate_from_cohort_overflow():
    store, other = FakeStore(), FakeStore()
    with controls.ConnectCohort(store, hooked=True, retained=True) as cohort:
        connections = [store._connect() for _ in range(17)]
        other_connection = other._connect()
        background, errors = on_other_thread(store._connect)
        assert errors == []
        assert type(other_connection) is FakeConnection
        assert len(background) == 1 and type(background[0]) is FakeConnection
        assert cohort.return_count == 17
        assert cohort.unexpected_returns == 2
        assert cohort.overflow == 1
        assert all(actual is expected for actual, expected in zip(cohort.slots, connections[:16]))
    cohort.close_retained()
    assert connections[16].close_threads == []
    assert other_connection.close_threads == []
    assert background[0].close_threads == []
    assert len(cohort.close_results) == 16


def test_raw_connection_count_is_unobserved_and_descriptor_is_never_changed():
    store = FakeStore()
    original = FakeStore.__dict__["_connect"]
    with controls.ConnectCohort(store, hooked=False, retained=False) as cohort:
        assert FakeStore.__dict__["_connect"] is original
        connection = store._connect()
    cohort.close_retained()
    report = cohort.report()
    assert report["connection_count"] == "UNOBSERVED"
    assert report["unexpected_returns"] == "UNOBSERVED"
    assert report["natural_finalization"] == "UNOBSERVED"
    assert report["close_results"] == []
    assert report["hook_restored"]
    assert connection.close_threads == []


def test_writer_records_only_two_first_terminal_flushes_and_forwards_duplicate_once(monkeypatch):
    ticks = iter((10, 20, 30, 40))
    monkeypatch.setattr(controls.time, "perf_counter_ns", lambda: next(ticks))
    observation = controls.WriterObservation()
    writer, result = object(), object()
    events = [SimpleNamespace(jobId=job, eventType="job.completed")
              for job in ("append-A", "append-B")]
    calls = []

    def original(target, **kwargs):
        calls.append((target, kwargs))
        return result

    for event in events:
        assert observation.flush(original, writer, sync=True, sourceEvent=event) is result
    observation.cleanup_started = True
    assert observation.flush(original, writer, sync=True, sourceEvent=events[1]) is result
    report = observation.report({"wall_start_ns": 15, "wall_end_ns": 35, "cpu_start_ns": 0, "cpu_end_ns": 0}, True)
    assert calls == [(writer, {"sync": True, "sourceEvent": event})
                     for event in (events[0], events[1], events[1])]
    assert report["first_terminal_flushes"] == [
        {"job": "append-A", "entry_ns": 10, "exit_ns": 20, "returned": result},
        {"job": "append-B", "entry_ns": 30, "exit_ns": 40, "returned": result},
    ]
    assert report["duplicate_terminal_flushes"] == 1
    assert report["cleanup_duplicate_terminal_flushes"] == 1
    assert report["other_sync_flushes"] == 0
    assert report["overlap"] == "OBSERVED"


@pytest.mark.parametrize("job,event_type,sync,other_sync", [
    ("append-A", "job.started", True, 1),
    ("another-job", "job.completed", True, 1),
    ("append-A", "job.completed", False, 0),
    (None, None, True, 1),
])
def test_nonterminal_flushes_forward_once_without_terminal_records(job, event_type, sync, other_sync):
    observation = controls.WriterObservation()
    event = SimpleNamespace(jobId=job, eventType=event_type) if job is not None else None
    writer, result = object(), object()
    calls = []

    def original(target, **kwargs):
        calls.append((target, kwargs))
        return result

    assert observation.flush(original, writer, sync=sync, sourceEvent=event) is result
    assert calls == [(writer, {"sync": sync, "sourceEvent": event})]
    assert observation.first == {}
    assert observation.duplicate_terminal_flushes == 0
    assert observation.other_sync_flushes == other_sync


def test_terminal_flush_exception_preserved_and_exit_endpoint_recorded(monkeypatch):
    ticks = iter((10, 20))
    monkeypatch.setattr(controls.time, "perf_counter_ns", lambda: next(ticks))
    observation = controls.WriterObservation()
    event = SimpleNamespace(jobId="append-A", eventType="job.completed")
    failure = OSError("flush failed")
    calls = []

    def original(writer, **kwargs):
        calls.append((writer, kwargs))
        raise failure

    writer = object()
    with pytest.raises(OSError) as caught:
        observation.flush(original, writer, sync=True, sourceEvent=event)
    assert caught.value is failure
    assert calls == [(writer, {"sync": True, "sourceEvent": event})]
    assert observation.first == {"append-A": {"job": "append-A", "entry_ns": 10, "exit_ns": 20}}


@pytest.mark.parametrize("fails", [False, True])
def test_failure_callback_counts_and_forwards_once_even_when_original_raises(fails):
    observation = controls.WriterObservation()
    runtime, event, result = object(), object(), object()
    failure = RuntimeError("callback failed")
    calls = []

    def original(target, source_event, message):
        calls.append((target, source_event, message))
        if fails:
            raise failure
        return result

    messages = [str(index) + "x" * 250 for index in range(6)]
    for message in messages:
        if fails:
            with pytest.raises(RuntimeError) as caught:
                observation.failure(original, runtime, event, message)
            assert caught.value is failure
        else:
            assert observation.failure(original, runtime, event, message) is result
    assert calls == [(runtime, event, message) for message in messages]
    assert observation.callback_count == 6
    assert observation.callback_samples == [message[:200] for message in messages[:4]]


@pytest.mark.parametrize("interval,enabled,expected", [
    ({"wall_start_ns": 20, "wall_end_ns": 30}, True, "BACKGROUND_OVERLAP_NOT_OBSERVED"),
    ({"wall_start_ns": 0, "wall_end_ns": 10}, True, "BACKGROUND_OVERLAP_NOT_OBSERVED"),
    ({"wall_start_ns": 30, "wall_end_ns": 40}, True, "BACKGROUND_OVERLAP_NOT_OBSERVED"),
    ({"wall_start_ns": 15, "wall_end_ns": 30}, True, "OBSERVED"),
    ({"wall_start_ns": 15, "wall_end_ns": 30}, False, "CONTROLLED_JSONL_DISABLED"),
    (None, True, "TIMING_INVALID"),
])
def test_writer_overlap_requires_intersecting_observed_endpoints(interval, enabled, expected):
    observation = controls.WriterObservation()
    observation.first["append-A"] = {"job": "append-A", "entry_ns": 10, "exit_ns": 20}
    if interval is not None:
        interval = dict(interval, cpu_start_ns=0, cpu_end_ns=0)
    assert observation.report(interval, enabled)["overlap"] == expected


def test_empty_writer_observation_does_not_claim_background_overlap():
    report = controls.WriterObservation().report({"wall_start_ns": 0, "wall_end_ns": 100, "cpu_start_ns": 0, "cpu_end_ns": 0}, True)
    assert report["overlap"] == "BACKGROUND_OVERLAP_NOT_OBSERVED"
    assert report["first_terminal_flushes"] == []


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / controls.child_tools.SOURCE_PATH
    source.parent.mkdir(parents=True)
    source.write_text("test source marker", encoding="utf-8")
    identity = {"commit": "test", "dirty": "", "source_sha256": {"source": "same"}}
    monkeypatch.setattr(controls, "source_identity", lambda path: identity)
    return repo


@pytest.fixture
def temp_roots(monkeypatch):
    created, arguments = [], []
    original = tempfile.mkdtemp

    def create(*args, **kwargs):
        arguments.append((args, kwargs))
        path = Path(original(*args, **kwargs))
        created.append(path)
        return str(path)

    monkeypatch.setattr(controls.tempfile, "mkdtemp", create)
    yield created, arguments
    for path in created:
        if path.exists():
            shutil.rmtree(path)


def execution(status="EXITED", *, reaped=True, joined=True):
    return {"status": status, "returncode": 0 if status == "EXITED" else -9,
            "hard_work_deadline_seconds": controls.CHILD_SECONDS, "output_truncated": False,
            "capture_error": None, "captured_log_bytes": 0,
            "cleanup": {"process_reaped": reaped, "output_reader_joined": joined, "reader_errors": []}}


def worker_result(repo, case, data_root):
    """A complete fake report so validation can check the same worker contract."""
    enabled = case in {"R1", "N1"}
    retained = case in {"R0", "R1"}
    settings = {"journal_mode": "wal", "synchronous": 2, "busy_timeout": 5000,
                "wal_autocheckpoint": 1000, "page_size": 4096}
    pairs = [[job, index] for job in ("append-A", "append-B") for index in range(1, 9)]
    manifest = controls.event_manifest()
    inactive = {"process_handles": 0, "bridges": 0, "preview_sessions": 0,
                "preview_workers_started": 0, "synthetic_job_resources": False}
    return {
        "schema_version": 1, "case": case, "data_root": str(data_root),
        "db_path": str(data_root / "runtime.sqlite3"),
        "status": "COMPLETED", "eligible": True, "retained": retained,
        "controlled_jsonl_enabled": enabled, "append_calls_returned": 16,
        "setup_job_inserts_returned": 2, "sink_matches_original": True,
        "inactive_owners_before": inactive.copy(), "inactive_owners_after": inactive.copy(),
        "fixed_foreground_append_calls": 16, "manifest_sha256": controls.manifest_hash(manifest),
        "build": {"fresh_native_autocheckpoint_default": 1000, "sqlite_version": "fake"},
        "temp_root": str(Path(tempfile.gettempdir()).resolve(strict=True)),
        "temp_root_raw": tempfile.gettempdir(), "source_stable": True,
        "source_before": controls.source_identity(repo), "source_after": controls.source_identity(repo),
        "cohort": {"wall_start_ns": 100, "wall_end_ns": 200, "cpu_start_ns": 20, "cpu_end_ns": 50},
        "background": {
            "timing_valid": True,
            "first_terminal_flushes": [
                {"job": "append-A", "entry_ns": 110, "exit_ns": 120, "returned": True},
                {"job": "append-B", "entry_ns": 210, "exit_ns": 220, "returned": True},
            ] if enabled else [],
            "duplicate_terminal_flushes": 1 if enabled else 0, "other_sync_flushes": 0,
            "cleanup_duplicate_terminal_flushes": 1 if enabled else 0,
            "callback_count": 0, "callback_samples": [],
            "overlap": "OBSERVED" if enabled else "CONTROLLED_JSONL_DISABLED",
        },
        "connections": {
            "connection_count": "UNOBSERVED" if "raw" in case else 16,
            "unexpected_returns": "UNOBSERVED" if "raw" in case else 0,
            "retained": retained, "overflow": 0, "hook_restored": True, "creator_ident": 1,
            "natural_finalization": "UNOBSERVED",
            "close_results": [{"index": index, "closed": True, "owner_ident": 1}
                              for index in range(16)] if retained else [],
        },
        "settings_before": settings.copy(), "keeper_settings": settings.copy(),
        "settings_after": settings.copy(), "keeper_ready_during_cohort": True,
        "writer": {"retired": True, "enabled": enabled, "enqueue_order": 16 if enabled else 0,
                   "processed": {"append-A": 8, "append-B": 8} if enabled else {},
                   "dropped": {}, "failure_message": "", "queues_empty": True, "handle_reference_cleared": True,
                   "jsonl_records": 16 if enabled else 0, "jsonl_pairs": pairs if enabled else []},
        "verification": {"whole_table_count": 16, "exact_manifest_rows": True, "error_rows": 0,
                         "rows": [[item["jobId"], index % 8 + 1, item["eventType"], "INFO", item["timestampMs"]]
                                  for index, item in enumerate(manifest)]},
        "errors": [], "cleanup_errors": [], "threads_before": [], "threads_after": [],
        "cleanup": {"runtime_closed": True, "keeper_released": True, "maintenance_retired": True,
                    "keeper_not_ready": True, "writer_retired": True, "data_lock_released": True},
    }


def successful_runner(repo, calls):
    def run(command, directory, *, timeout, output_cap):
        case = command[command.index("--worker-case") + 1]
        data_root = Path(command[command.index("--data-root") + 1])
        assert data_root.is_dir()
        assert list(data_root.iterdir()) == []
        if calls:
            assert not calls[-1]["data_root"].exists(), "next case started before prior data cleanup"
            assert (calls[-1]["directory"] / "result.json").is_file()
        calls.append({"command": command, "case": case, "directory": directory,
                      "data_root": data_root, "timeout": timeout, "output_cap": output_cap})
        (data_root / "owned-marker").write_text(case, encoding="utf-8")
        result = worker_result(repo, case, data_root)
        (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
        (directory / "worker.log").write_text("", encoding="utf-8")
        return execution()
    return run


@pytest.mark.parametrize("case", controls.CASES)
def test_complete_fake_report_is_eligible_without_running_a_worker(source_repo, tmp_path, case):
    report = worker_result(source_repo, case, tmp_path / "fake-data")
    assert controls.comparison_eligible(report)


@pytest.mark.parametrize("case,path,value", [
    ("N0-hook-1", ("connections", "connection_count"), 15),
    ("N0-hook-1", ("connections", "unexpected_returns"), 1),
    ("N0-hook-1", ("connections", "overflow"), 1),
    ("N0-hook-1", ("connections", "hook_restored"), False),
    ("N0-raw-1", ("connections", "connection_count"), 16),
    ("R0", ("connections", "close_results", 7, "closed"), False),
    ("R0", ("connections", "close_results"), []),
    ("N0-hook-1", ("verification", "whole_table_count"), 17),
    ("N0-hook-1", ("verification", "error_rows"), 1),
    ("N0-hook-1", ("verification", "exact_manifest_rows"), False),
    ("N1", ("writer", "failure_message"), "writer failed"),
    ("N1", ("writer", "dropped"), {"append-A": 1}),
    ("N1", ("writer", "queues_empty"), False),
    ("N1", ("writer", "handle_reference_cleared"), False),
    ("N1", ("writer", "processed", "append-B"), 7),
    ("N1", ("background", "callback_count"), 1),
    ("N1", ("background", "first_terminal_flushes"), []),
    ("R1", ("cleanup", "keeper_released"), False),
    ("R1", ("cleanup_errors",), [{"owner": "writer", "type": "OSError"}]),
    ("N0-hook-1", ("append_calls_returned",), 15),
    ("N0-hook-1", ("setup_job_inserts_returned",), 1),
    ("N0-hook-1", ("sink_matches_original",), False),
    ("N0-hook-1", ("inactive_owners_before", "process_handles"), 1),
    ("N0-hook-1", ("inactive_owners_after", "bridges"), 1),
    ("N0-hook-1", ("settings_after", "synchronous"), 1),
    ("N0-hook-1", ("settings_after", "page_size"), 8192),
    ("N0-hook-1", ("source_stable",), False),
    ("N0-hook-1", ("threads_after",), [{"name": "unretired", "ident": 2, "daemon": True}]),
])
def test_eligibility_rejects_count_cleanup_writer_and_policy_gaps(source_repo, tmp_path, case, path, value):
    report = worker_result(source_repo, case, tmp_path / "fake-data")
    assert controls.comparison_eligible(report)
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not controls.comparison_eligible(report)


def test_seven_cases_are_sequential_and_use_same_unmodified_default_temp_root(source_repo, tmp_path, temp_roots):
    calls = []
    output = tmp_path / "append-controls"
    report = controls.run_controls(source_repo, output, runner=successful_runner(source_repo, calls))
    assert [call["case"] for call in calls] == [
        "N0-raw-1", "N0-hook-1", "N0-hook-2", "N0-raw-2", "R0", "R1", "N1",
    ]
    assert report["fixed_order"] == list(controls.CASES)
    assert report["events_per_cohort"] == 16
    assert report["planned_foreground_transactions"] == 112
    assert len({call["data_root"] for call in calls}) == 7
    assert len({call["directory"] for call in calls}) == 7
    assert all(call["data_root"].parent == Path(tempfile.gettempdir()).resolve(strict=True) for call in calls)
    assert all(call["directory"].parent == output for call in calls)
    assert all(call["command"][0] == sys.executable for call in calls)
    assert all(call["timeout"] == 30 and call["output_cap"] == controls.LOG_CAP for call in calls)
    assert temp_roots[1] == [((), {"prefix": "runtime-append-control-"})] * 7
    assert all(not path.exists() for path in temp_roots[0])
    assert all(item["child_retirement_confirmed"] and item["temp_data_removed"] for item in report["results"])
    assert report["same_build_manifest_temp_root"] and report["source_stable"] and report["eligible"]
    assert json.loads((output / "summary.json").read_text(encoding="utf-8")) == report


def test_timeout_stops_every_next_case_and_removes_data_only_after_confirmed_reap(source_repo, tmp_path, temp_roots):
    calls = []

    def run(command, directory, **kwargs):
        calls.append(command)
        data_root = Path(command[command.index("--data-root") + 1])
        (data_root / "partial").write_text("partial fake work", encoding="utf-8")
        return execution("TIMEOUT")

    report = controls.run_controls(source_repo, tmp_path / "timeout", runner=run)
    assert len(calls) == len(temp_roots[0]) == 1
    first = report["results"][0]
    assert first["execution"]["status"] == "TIMEOUT"
    assert first["child_retirement_confirmed"] and first["temp_data_removed"]
    assert not temp_roots[0][0].exists()
    assert [item["status"] for item in report["results"][1:]] == ["SKIPPED"] * 6
    assert not report["eligible"]


@pytest.mark.parametrize("reaped,joined", [(False, True), (True, False), (False, False)])
def test_unconfirmed_retirement_preserves_data_and_skips_remaining_cases(source_repo, tmp_path, temp_roots, reaped, joined):
    calls = []

    def run(command, directory, **kwargs):
        calls.append(command)
        return execution("TIMEOUT", reaped=reaped, joined=joined)

    report = controls.run_controls(source_repo, tmp_path / "unfinished", runner=run)
    assert len(calls) == len(temp_roots[0]) == 1
    assert temp_roots[0][0].is_dir()
    first = report["results"][0]
    assert not first["child_retirement_confirmed"]
    assert not first["temp_data_removed"]
    assert not first["eligible"]
    assert [item["status"] for item in report["results"][1:]] == ["SKIPPED"] * 6
    assert not report["eligible"]


def test_failed_launch_preserves_unconfirmed_data_and_stops(source_repo, tmp_path, temp_roots):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise OSError("launch failed")

    report = controls.run_controls(source_repo, tmp_path / "launch-failure", runner=run)
    assert len(calls) == 1
    assert report["results"][0]["error"] == {"type": "OSError", "message": "launch failed"}
    assert not report["results"][0]["child_retirement_confirmed"]
    assert temp_roots[0][0].is_dir()
    assert [item["status"] for item in report["results"][1:]] == ["SKIPPED"] * 6
    assert not report["eligible"]


def test_combined_bounded_outputs_fit_one_mib():
    assert len(controls.CASES) * (controls.LOG_CAP + controls.RESULT_CAP) + controls.SUMMARY_CAP <= controls.TOTAL_CAP
    assert controls.TOTAL_CAP == 1024 * 1024


def test_file_validator_checks_all_seven_fake_reports(source_repo, tmp_path, temp_roots):
    output = tmp_path / "validated"
    report = controls.run_controls(source_repo, output, runner=successful_runner(source_repo, []))
    assert controls.validate_output(output, expected_source=controls.source_identity(source_repo)) == report


@pytest.mark.parametrize("path,value", [
    (("connections", "connection_count"), 15),
    (("connections", "close_results", 2, "owner_ident"), 99),
    (("verification", "whole_table_count"), 17),
    (("verification", "rows", 0, 2), "runtime.logfile.failed"),
    (("cleanup", "keeper_released"), False),
    (("settings_after", "synchronous"), 1),
])
def test_validator_rejects_worker_gaps_despite_green_booleans(source_repo, tmp_path, temp_roots, path, value):
    output = tmp_path / "forged"
    controls.run_controls(source_repo, output, runner=successful_runner(source_repo, []))
    result_path = output / "R0" / "result.json"
    result = json.loads(result_path.read_text())
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result_path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="invalid cohort"):
        controls.validate_output(output)


def test_validator_keeps_negative_wall_minus_cpu_and_no_background_overlap(source_repo, tmp_path, temp_roots):
    output = tmp_path / "negative"
    controls.run_controls(source_repo, output, runner=successful_runner(source_repo, []))
    summary = json.loads((output / "summary.json").read_text())
    for entry in summary["results"]:
        result_path = output / entry["result_path"]
        result = json.loads(result_path.read_text())
        if entry["case"] == "N0-raw-1":
            result["cohort"].update(wall_end_ns=110, cpu_end_ns=50)
            entry["cohort"] = result["cohort"]
        if entry["case"] in {"N1", "R1"}:
            for flush in result["background"]["first_terminal_flushes"]:
                flush.update(entry_ns=300, exit_ns=310)
            result["background"]["overlap"] = "BACKGROUND_OVERLAP_NOT_OBSERVED"
            entry["background"] = result["background"]
            summary["background_overlap_evidence"][entry["case"]] = "BACKGROUND_OVERLAP_NOT_OBSERVED"
        result_path.write_text(json.dumps(result))
    (output / "summary.json").write_text(json.dumps(summary))
    assert controls.validate_output(output) == summary


def test_validator_rejects_unexpected_output_and_large_capture(source_repo, tmp_path, temp_roots):
    output = tmp_path / "extra"
    controls.run_controls(source_repo, output, runner=successful_runner(source_repo, []))
    extra = output / "unexpected.txt"
    extra.write_text("unexpected")
    with pytest.raises(ValueError, match="unexpected/missing"):
        controls.validate_output(output)
    extra.unlink()
    (output / controls.CASES[0] / "worker.log").write_bytes(b"x" * (controls.LOG_CAP + 1))
    with pytest.raises(ValueError, match="capture size"):
        controls.validate_output(output)


@pytest.mark.parametrize("field,value", [("wall_end_ns", 90), ("cpu_end_ns", 10)])
def test_reverse_monotonic_endpoints_are_preserved_but_unusable(source_repo, tmp_path, field, value):
    report = worker_result(source_repo, "N1", tmp_path / "fake")
    report["cohort"][field] = value
    observation = controls.WriterObservation()
    observation.first = {row["job"]: row for row in report["background"]["first_terminal_flushes"]}
    report["background"] = observation.report(report["cohort"], True)
    assert report["cohort"][field] == value
    assert report["background"]["overlap"] == "TIMING_INVALID"
    assert not report["background"]["timing_valid"]
    assert not controls.comparison_eligible(report)


def test_reverse_flush_clock_cannot_establish_overlap(source_repo, tmp_path):
    report = worker_result(source_repo, "N1", tmp_path / "fake")
    observation = controls.WriterObservation()
    observation.first["append-A"] = {"job": "append-A", "entry_ns": 150, "exit_ns": 140, "returned": True}
    report["background"] = observation.report(report["cohort"], True)
    assert report["background"]["overlap"] == "TIMING_INVALID"
    assert report["background"]["first_terminal_flushes"][0]["exit_ns"] == 140
    assert not controls.comparison_eligible(report)


def test_file_validator_rejects_reversed_clock_even_if_report_claims_valid(source_repo, tmp_path, temp_roots):
    output = tmp_path / "reverse"
    controls.run_controls(source_repo, output, runner=successful_runner(source_repo, []))
    result_path = output / "R0" / "result.json"
    result = json.loads(result_path.read_text())
    result["cohort"]["cpu_end_ns"] = result["cohort"]["cpu_start_ns"] - 1
    result_path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="invalid cohort"):
        controls.validate_output(output)


def test_bounded_json_read_rejects_overflow_without_stat(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_bytes(b'{"a": 1}' + b" " * 100)
    monkeypatch.setattr(Path, "stat", lambda *args, **kwargs: pytest.fail("stat race"))
    with pytest.raises(ValueError, match="oversized"):
        controls.read_json_bounded(path, 16)


@pytest.mark.parametrize("duplicates,cleanup", [(2, 1), (-1, -1), (True, True), (1, -1)])
def test_cohort_terminal_duplicates_or_invalid_counters_reject_comparison(source_repo, tmp_path, duplicates, cleanup):
    report = worker_result(source_repo, "N1", tmp_path / "fake")
    report["background"].update(duplicate_terminal_flushes=duplicates, cleanup_duplicate_terminal_flushes=cleanup)
    assert not controls.comparison_eligible(report)


def test_parse_failure_after_green_result_update_stops_next_child(source_repo, tmp_path, temp_roots):
    calls = []
    runner = successful_runner(source_repo, calls)

    def malformed(*args, **kwargs):
        execution_result = runner(*args, **kwargs)
        path = args[1] / "result.json"
        result = json.loads(path.read_text())
        del result["source_before"]
        path.write_text(json.dumps(result))
        return execution_result

    report = controls.run_controls(source_repo, tmp_path / "bad-source", runner=malformed)
    assert len(calls) == 1
    assert report["results"][0]["status"] == "INVALID"
    assert not report["results"][0]["eligible"]
    assert all(item["status"] == "SKIPPED" for item in report["results"][1:])


@pytest.fixture
def simulated_temp_alias(tmp_path, monkeypatch):
    """Model two directory spellings; this does not exercise Windows 8.3 APIs."""
    canonical = tmp_path / "canonical-temp"
    canonical.mkdir()
    raw = tmp_path / "RUNNER~1"
    mappings = {str(raw): canonical}
    original_resolve = Path.resolve
    original_mkdtemp = tempfile.mkdtemp

    def resolve(path, strict=False):
        if str(path) in mappings:
            assert strict
            return mappings[str(path)]
        return original_resolve(path, strict=strict)

    def create(*args, **kwargs):
        assert args == () and kwargs == {"prefix": "runtime-append-control-"}
        real = Path(original_mkdtemp(dir=canonical, **kwargs))
        alias = raw / real.name
        mappings[str(alias)] = real
        return str(alias)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(controls.tempfile, "gettempdir", lambda: str(raw))
    monkeypatch.setattr(controls.tempfile, "mkdtemp", create)
    return raw, canonical, mappings


def test_equivalent_alias_is_canonicalized_before_parent_passes_child_argument(
    source_repo, tmp_path, simulated_temp_alias,
):
    raw, canonical, _ = simulated_temp_alias
    calls = []
    output = tmp_path / "alias-reports"
    report = controls.run_controls(source_repo, output, runner=successful_runner(source_repo, calls))
    assert report["eligible"] and len(calls) == 7
    for call, entry in zip(calls, report["results"]):
        assert call["data_root"].parent == canonical
        assert entry["data_root"] == str(call["data_root"])
        assert entry["data_root_raw"] == str(raw / call["data_root"].name)
        assert entry["data_root"] != entry["data_root_raw"]
        assert entry["temp_root"] == str(canonical)
        assert entry["temp_root_raw"] == entry["parent_temp_root_raw"] == str(raw)
        assert entry["temp_data_removed"] and not call["data_root"].exists()
    assert canonical.is_dir()
    assert controls.validate_output(output) == report


def test_child_main_receives_canonical_owned_directory_without_running_workload(
    source_repo, tmp_path, simulated_temp_alias, monkeypatch,
):
    raw, canonical, _ = simulated_temp_alias
    alias = Path(controls.tempfile.mkdtemp(prefix="runtime-append-control-"))
    expected = canonical / alias.name
    output = tmp_path / "child-evidence"
    output.mkdir()
    calls = []

    def fake_worker(repo, data_root, case):
        calls.append((repo, data_root, case))
        assert data_root == expected
        paths = controls.canonical_temp_paths(data_root)
        assert paths == {"data_root": str(expected), "temp_root": str(canonical), "temp_root_raw": str(raw)}
        return {"eligible": True, **paths}

    monkeypatch.setattr(controls, "run_worker", fake_worker)
    monkeypatch.setattr(sys, "argv", ["r3_sqlite_append_controls.py", "--repo", str(source_repo),
        "--output", str(output), "--worker-case", "N0-raw-1", "--data-root", str(alias)])
    assert controls.main() == 0
    assert calls == [(source_repo.resolve(), expected, "N0-raw-1")]
    assert json.loads((output / "result.json").read_text())["data_root"] == str(expected)
    assert expected.is_dir()  # Only the parent owns experiment data cleanup.


def test_actual_different_child_directory_is_rejected_even_under_same_temp_root(
    source_repo, tmp_path, temp_roots,
):
    calls = []
    other_directories = []
    runner = successful_runner(source_repo, calls)

    def different_directory(*args, **kwargs):
        result = runner(*args, **kwargs)
        path = args[1] / "result.json"
        worker = json.loads(path.read_text())
        other = Path(controls.tempfile.mkdtemp(prefix="runtime-append-control-")).resolve(strict=True)
        other_directories.append(other)
        assert other.parent == Path(worker["data_root"]).parent
        assert other != Path(worker["data_root"])
        worker["data_root"] = str(other)
        path.write_text(json.dumps(worker))
        return result

    report = controls.run_controls(source_repo, tmp_path / "different", runner=different_directory)
    assert len(calls) == 1
    assert not report["eligible"]
    assert report["results"][0]["error"]["message"] == "child identity mismatch"
    assert report["results"][0]["temp_data_removed"]
    assert other_directories[0].is_dir()  # Rejecting identity must not remove another directory.
    assert all(entry["status"] == "SKIPPED" for entry in report["results"][1:])
    with pytest.raises(ValueError, match="incomplete cohort evidence"):
        controls.validate_output(tmp_path / "different")


def test_canonical_default_root_must_match_exact_directory(tmp_path, monkeypatch):
    default = tmp_path / "temp"
    different = tmp_path / "temp-other"
    default.mkdir()
    different.mkdir()
    owned = different / "runtime-append-control-owned"
    owned.mkdir()
    monkeypatch.setattr(controls.tempfile, "gettempdir", lambda: str(default))
    with pytest.raises(ValueError, match="outside the canonical default temp root"):
        controls.canonical_temp_paths(owned)
    assert owned.is_dir() and default.is_dir() and different.is_dir()


def test_resolve_failure_before_runner_cleans_only_created_raw_owner(
    source_repo, tmp_path, temp_roots, monkeypatch,
):
    def fail(_):
        raise OSError("directory resolve failed")

    monkeypatch.setattr(controls, "canonical_temp_paths", fail)
    report = controls.run_controls(source_repo, tmp_path / "resolve-failed",
        runner=lambda *args, **kwargs: pytest.fail("child must not start"))
    first = report["results"][0]
    assert len(temp_roots[0]) == 1
    assert first["child_not_started"] and not first["child_retirement_confirmed"]
    assert first["temp_data_removed"] and not temp_roots[0][0].exists()
    assert temp_roots[0][0].parent.is_dir()
    assert first["data_root_raw"] == str(temp_roots[0][0])
    assert first["error"]["type"] == "OSError"
    assert not report["eligible"]
