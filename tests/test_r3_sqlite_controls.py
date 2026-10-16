"""Supervisor safety checks are stdlib-only; no runtime/Qt import or database."""
import sys
import threading

import pytest

from scripts import r3_sqlite_controls as controls


@pytest.fixture
def source_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    implementation = repo / controls.SOURCE_PATH
    implementation.parent.mkdir(parents=True)
    implementation.write_text("source marker", encoding="utf-8")
    monkeypatch.setattr(controls, "source_identity", lambda path: {"commit": "test", "source_sha256": {"source": "same"}})
    return repo


def execution(status="EXITED", *, pid=1, reaped=True, joined=True):
    return {"status": status, "pid": pid, "returncode": 0,
            "cleanup": {"process_reaped": reaped, "output_reader_joined": joined}}


def successful_runner(calls):
    def run(command, directory, *, timeout):
        case = command[-1]
        calls.append((command, directory, timeout))
        assert not (directory / "control.sqlite3").exists()
        # Each completed worker must have returned before the next is invoked.
        if len(calls) > 1:
            assert (calls[-2][1] / "result.json").exists()
        result = {"case": case, "db_path": str(directory / "control.sqlite3"),
                  "status": "COMPLETED", "sqlite_build": {"source_id": "same"}}
        controls.write_json_bounded(directory / "result.json", result)
        return execution(pid=100 + len(calls))
    return run


def test_exactly_five_sequential_isolated_cases(source_repo, tmp_path):
    calls = []
    output = tmp_path / "controls"
    report = controls.run_controls(source_repo, output, runner=successful_runner(calls))
    assert [item[0][-1] for item in calls] == list(controls.CASES)
    assert len({item[1] for item in calls}) == 5
    assert all(item[1].parent == output for item in calls)
    assert all(item[0][0] == sys.executable for item in calls)
    assert all(item[2] == 30 for item in calls)
    assert report["status"] == "COMPLETED"
    assert report["same_sqlite_build_across_completed_controls"]
    assert report["source_stable"]


def test_existing_output_is_preserved_without_running(source_repo, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "original.sqlite3"
    marker.write_bytes(b"do not touch")
    with pytest.raises(FileExistsError):
        controls.run_controls(source_repo, output, runner=lambda *a, **k: pytest.fail("ran worker"))
    assert marker.read_bytes() == b"do not touch"
    assert list(output.iterdir()) == [marker]


@pytest.mark.parametrize("value", [0, -1, 30.1, float("inf"), float("nan")])
def test_invalid_deadline_creates_no_output(source_repo, tmp_path, value):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError):
        controls.run_controls(source_repo, output, timeout=value)
    assert not output.exists()


def test_timeout_kills_and_reaps_child_and_joins_reader(tmp_path):
    result = controls.run_child([sys.executable, "-c", "import time; time.sleep(30)"],
                               tmp_path, timeout=0.1)
    assert result["status"] == "TIMEOUT"
    assert result["returncode"] is not None
    assert result["cleanup"] == {"process_reaped": True, "output_reader_joined": True, "reader_errors": []}
    assert result["elapsed_seconds"] < 3
    assert not any(thread.name == "control-output" for thread in threading.enumerate())


def test_output_cap_limits_memory_log_and_kills_writer(tmp_path):
    command = [sys.executable, "-c", "import os, time; os.write(1, b'x' * 65536); time.sleep(30)"]
    result = controls.run_child(command, tmp_path, timeout=2, output_cap=4096)
    assert result["status"] == "OUTPUT_LIMIT"
    assert result["output_truncated"]
    assert result["captured_log_bytes"] == 4096
    assert (tmp_path / "worker.log").stat().st_size == 4096
    assert result["cleanup"]["process_reaped"]
    assert result["cleanup"]["output_reader_joined"]


def test_fast_exit_overflow_is_still_output_limit(tmp_path):
    command = [sys.executable, "-c", "import os; os.write(1, b'x' * 10000)"]
    result = controls.run_child(command, tmp_path, timeout=2, output_cap=100)
    assert result["status"] == "OUTPUT_LIMIT"
    assert (tmp_path / "worker.log").stat().st_size == 100


def test_timeout_preserves_artifacts_and_continues_new_cases(source_repo, tmp_path):
    calls = []
    success = successful_runner(calls)
    first = True

    def run(command, directory, *, timeout):
        nonlocal first
        if first:
            first = False
            (directory / "control.sqlite3").write_bytes(b"partial control")
            return execution("TIMEOUT")
        return success(command, directory, timeout=timeout)

    output = tmp_path / "timeout-controls"
    report = controls.run_controls(source_repo, output, runner=run)
    assert report["status"] == "INCOMPLETE"
    assert report["cases"][0]["status"] == "TIMEOUT"
    assert report["cases"][0]["connection_cleanup"] == "not_confirmed_after_forced_worker_exit"
    assert len(calls) == 4
    assert (output / controls.CASES[0] / "control.sqlite3").read_bytes() == b"partial control"


def test_incomplete_cleanup_skips_every_remaining_case(source_repo, tmp_path):
    calls = []

    def run(command, directory, *, timeout):
        calls.append(command)
        return execution("CLEANUP_INCOMPLETE", reaped=False)

    report = controls.run_controls(source_repo, tmp_path / "stuck", runner=run)
    assert len(calls) == 1
    assert [item["status"] for item in report["cases"]] == ["CLEANUP_INCOMPLETE"] + ["SKIPPED"] * 4


def test_wrong_case_or_database_report_is_rejected(source_repo, tmp_path):
    def run(command, directory, *, timeout):
        controls.write_json_bounded(directory / "result.json", {"case": command[-1],
            "db_path": str(tmp_path / "original.sqlite3"), "status": "COMPLETED"})
        return execution()

    report = controls.run_controls(source_repo, tmp_path / "wrong", runner=run)
    assert all(item["status"] == "FAILED" for item in report["cases"])


def test_source_change_marks_whole_run_incomplete(source_repo, tmp_path, monkeypatch):
    identities = iter(({"hash": "before"}, {"hash": "after"}))
    monkeypatch.setattr(controls, "source_identity", lambda repo: next(identities))
    report = controls.run_controls(source_repo, tmp_path / "changed", runner=successful_runner([]))
    assert not report["source_stable"]
    assert report["status"] == "INCOMPLETE"


def test_oversized_json_refuses_to_create_file(tmp_path):
    target = tmp_path / "result.json"
    with pytest.raises(OverflowError):
        controls.write_json_bounded(target, {"large": "x" * 100}, cap=32)
    assert not target.exists()


def test_existing_worker_database_is_never_opened(source_repo, tmp_path):
    directory = tmp_path / "worker"
    directory.mkdir()
    db = directory / "control.sqlite3"
    db.write_bytes(b"original database")
    with pytest.raises(FileExistsError):
        controls.run_worker(source_repo, directory, controls.CASES[0])
    assert db.read_bytes() == b"original database"


def test_unexpected_worker_failure_stops_other_launches(source_repo, tmp_path):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise OSError("launch failed")

    report = controls.run_controls(source_repo, tmp_path / "failed", runner=run)
    assert len(calls) == 1
    assert report["cases"][0]["error_type"] == "OSError"
    assert all(item["status"] == "SKIPPED" for item in report["cases"][1:])


def test_json_logs_fit_combined_one_mib():
    assert controls.RESULT_CAP + controls.LOG_CAP == 1024 * 1024
    assert controls.CASE_TIMEOUT_SECONDS == 30
    assert controls.TIMER_SECONDS >= 0.05


def test_embedded_sqlite_build_without_file_has_interpreter_hash():
    from types import SimpleNamespace
    module = SimpleNamespace(__spec__=SimpleNamespace(origin="built-in"))
    identity = controls.sqlite_extension_identity(module)
    assert identity["module_origin"] == "built-in"
    assert identity["extension_path"] is None
    assert identity["extension_sha256"] is None
    assert len(identity["python_executable_sha256"]) == 64


def test_cleanup_only_closes_connections_on_their_owner_thread():
    from types import SimpleNamespace
    recorder = controls.Recorder()
    closed = []
    current = threading.get_ident()
    owned = SimpleNamespace(owner_thread=current, retired=False)
    foreign = SimpleNamespace(owner_thread=current + 1, retired=False,
                              close=lambda: pytest.fail("closed foreign connection"))

    def close():
        closed.append(threading.get_ident())
        owned.retired = True

    owned.close = close
    recorder.connections.extend([owned, foreign])
    assert recorder.close_owned() == []
    assert closed == [current]
    assert owned.retired and not foreign.retired
    assert recorder.close_owned() == []
    assert closed == [current]


def test_cleanup_failure_is_reported_without_claiming_retirement():
    from types import SimpleNamespace
    recorder = controls.Recorder()

    def failed_close():
        raise OSError("close failed")

    connection = SimpleNamespace(owner_thread=threading.get_ident(), retired=False,
                                 close=failed_close)
    recorder.connections.append(connection)
    assert recorder.close_owned() == ["OSError"]
    assert not connection.retired


def test_held_reader_transaction_and_cleanup_stay_on_one_thread():
    from types import SimpleNamespace
    recorder = controls.Recorder()
    calls = []

    class FakeConnection:
        def __init__(self):
            self.owner_thread = threading.get_ident()
            self.retired = False
            recorder.connections.append(self)

        def execute(self, sql):
            calls.append((sql, threading.get_ident()))
            return SimpleNamespace(fetchone=lambda: (0,))

        def rollback(self):
            calls.append(("rollback", threading.get_ident()))

        def close(self):
            assert threading.get_ident() == self.owner_thread
            self.retired = True
            calls.append(("close", threading.get_ident()))

    holder = controls.HeldConnection(SimpleNamespace(_connect=FakeConnection), recorder, "reader")
    holder.start()
    result = holder.close()
    assert result["thread_joined"] and result["errors"] == []
    assert [sql for sql, ident in calls] == ["BEGIN", "SELECT count(*) FROM jobEvents", "rollback", "close"]
    assert len({ident for sql, ident in calls}) == 1
    assert calls[0][1] != threading.get_ident()
    assert recorder.connections[0].retired


def test_oversized_worker_result_is_rejected(source_repo, tmp_path):
    def run(command, directory, *, timeout):
        (directory / "result.json").write_bytes(b"x" * (controls.RESULT_CAP + 1))
        return execution()

    report = controls.run_controls(source_repo, tmp_path / "oversized-results", runner=run)
    assert all(case["status"] == "FAILED" for case in report["cases"])
    assert all(case["error_type"] == "OverflowError" for case in report["cases"])


class FakePipe:
    closed = False

    def close(self):
        self.closed = True

    def fileno(self):
        return 987654


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stdout = FakePipe()
        self.pid = 1234
        self.killed = self.reaped = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, *, timeout):
        assert timeout == 1
        self.reaped = True
        return self.returncode


def test_reader_start_failure_reaps_child_closes_pipe_and_preserves_error(tmp_path, monkeypatch):
    process = FakeProcess()
    monkeypatch.setattr(controls.subprocess, "Popen", lambda *a, **k: process)

    def failed_start(self):
        raise RuntimeError("injected thread start failure")

    monkeypatch.setattr(controls.threading.Thread, "start", failed_start)
    result = controls.run_child(["fake-worker"], tmp_path)
    assert result["status"] == "CAPTURE_FAILED"
    assert result["capture_error"] == {"error_type": "RuntimeError", "message": "injected thread start failure"}
    assert process.killed and process.reaped and process.stdout.closed
    assert result["cleanup"]["process_reaped"]
    assert result["cleanup"]["output_reader_joined"]


def test_reader_failure_downgrades_successful_exit(tmp_path, monkeypatch):
    process = FakeProcess(returncode=0)
    monkeypatch.setattr(controls.subprocess, "Popen", lambda *a, **k: process)

    def failed_read(*args):
        raise OSError("injected capture failure")

    monkeypatch.setattr(controls.os, "read", failed_read)
    result = controls.run_child(["fake-worker"], tmp_path)
    assert result["returncode"] == 0
    assert result["status"] == "CAPTURE_FAILED"
    assert result["cleanup"]["reader_errors"] == ["OSError"]
    assert process.reaped and process.stdout.closed
    assert result["cleanup"]["output_reader_joined"]


def test_valid_result_file_does_not_override_capture_failure(source_repo, tmp_path):
    successful = successful_runner([])

    def run(command, directory, *, timeout):
        result = successful(command, directory, timeout=timeout)
        result["status"] = "CAPTURE_FAILED"
        result["cleanup"]["reader_errors"] = ["OSError"]
        return result

    report = controls.run_controls(source_repo, tmp_path / "capture-failed", runner=run)
    assert report["status"] == "INCOMPLETE"
    assert all(case["status"] == "CAPTURE_FAILED" for case in report["cases"])
    assert all("result" not in case for case in report["cases"])


def test_unstarted_timer_and_holder_cleanup_is_safe(monkeypatch):
    from types import SimpleNamespace

    def failed_start(self):
        raise RuntimeError("start failed")

    monkeypatch.setattr(controls.threading.Thread, "start", failed_start)
    recorder = controls.Recorder()
    with pytest.raises(RuntimeError, match="start failed"):
        recorder.start_timer()
    assert recorder.stop_timer()
    holder = controls.HeldConnection(SimpleNamespace(), recorder, "reader")
    with pytest.raises(RuntimeError, match="start failed"):
        holder.start()
    assert holder.close()["thread_joined"]
