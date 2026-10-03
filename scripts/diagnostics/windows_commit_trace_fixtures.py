"""Offline fixtures, explicitly invoked; never part of original pytest discovery."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from scripts import r3_windows_commit_trace as trace


def fixture():
    records = [
        dict(kind="meta", clock=1, frequency=1000000, processors=1, events_lost=0, buffers_lost=0, finalized=True),
        dict(kind="switch", qpc=10, cpu=0, old_tid=0, new_tid=1, old_state=5),
        dict(kind="process_start", qpc=20, pid=100, rundown=False),
        dict(kind="thread_start", qpc=21, pid=100, tid=101, rundown=False),
        dict(kind="ready", qpc=25, tid=101, header_pid=900, header_tid=999),
        dict(kind="switch", qpc=30, cpu=0, old_tid=1, new_tid=101, old_state=5),
        dict(kind="file_create", qpc=40, object=300, irp=400, name=r"\Device\Volume1\test\case.db"),
        dict(kind="file_end", qpc=41, irp=400, status=0),
        dict(kind="file_name", qpc=42, object=301, name=r"\Device\Volume1\test\case.db", remove=False),
        dict(kind="file_flush", qpc=60, object=300, key=301, irp=401, tid=101),
        dict(kind="switch", qpc=61, cpu=0, old_tid=101, new_tid=1, old_state=5),
        dict(kind="disk_flush", qpc=80, irp=499, tid=1, duration_ticks=20),
        dict(kind="file_end", qpc=90, irp=401, status=0, header_tid=999),
        dict(kind="ready", qpc=91, tid=101, header_pid=900),
        dict(kind="switch", qpc=95, cpu=0, old_tid=1, new_tid=101, old_state=1),
        dict(kind="switch", qpc=120, cpu=0, old_tid=101, new_tid=1, old_state=5),
        dict(kind="end", schema_errors=0, lost=0, process_status=0),
    ]
    observer = dict(pid=100, frequency=1000000, begin=22, cases=[dict(case=0, begin=35, end=130,
        invalid=False, omitted=0, retired=True, paths=[r"C:\test\case.db"], outcomes=dict(setup="passed", call="failed", teardown="passed"),
        calls=[dict(tid=101, store=0, phase="commit", begin=50, end=110, failed=False)])])
    control = dict(begin=5, stop_request=125, volumes={"c:": r"\Device\Volume1"})
    return records, observer, control


def testSchedulingReadyPayloadAndFlushCompletionHeader():
    result = trace.reduce_trace(*fixture())
    call = result["cases"][0]["calls"][0]
    assert call["state_us"] == dict(running=26, ready=4, blocked=30, unknown=0)
    assert call["flushes"] == [dict(file="db", begin_us=25, end_us=55, status="success")]
    assert call["returned_in_capture"] is True
    public = trace.public_summary(result, 1, True, "ok", "target_report", True)
    for secret in ("Volume1", "case.db", "header_pid", "100", "999", "401"):
        assert secret not in public
    assert json.loads(public)["original_pytest_exit"] == 1


@pytest.mark.parametrize("change,reason", [
    (lambda r,o,c: r[0].update(clock=2), "clock_mismatch"),
    (lambda r,o,c: r[0].update(frequency=10), "clock_mismatch"),
    (lambda r,o,c: r[0].update(events_lost=1), "event_loss_or_unfinalized"),
    (lambda r,o,c: r[0].update(buffers_lost=1), "event_loss_or_unfinalized"),
    (lambda r,o,c: r[0].update(finalized=False), "event_loss_or_unfinalized"),
    (lambda r,o,c: r[-1].update(schema_errors=1), "decode_incomplete"),
    (lambda r,o,c: r[-1].update(lost=1), "decode_incomplete"),
    (lambda r,o,c: r[0].update(processors=2), "circular_prefix_unproven"),
    (lambda r,o,c: r[2].update(rundown=True), "process_lifetime_missing"),
    (lambda r,o,c: r[3].update(rundown=True), "thread_reuse_or_missing_start"),
    (lambda r,o,c: r[10].update(old_tid=102), "scheduler_incoherent"),
    (lambda r,o,c: r[9].update(object=987), "unmatched_target_flush"),
    (lambda r,o,c: o["cases"][0].update(omitted=1), "observer_incomplete"),
    (lambda r,o,c: o["cases"][0]["calls"][0].update(end=40), "call_interval_uncovered"),
])
def testInvalidEvidenceCannotBecomeDurations(change, reason):
    args = fixture()
    change(*args)
    with pytest.raises(trace.Invalid, match=reason):
        trace.reduce_trace(*args)


def testIrpReuseBeforeCompletionInvalidates():
    records, observer, control = fixture()
    records.insert(10, dict(kind="file_other", qpc=60, object=300, key=301, irp=401, tid=101, opcode=67))
    with pytest.raises(trace.Invalid, match="ambiguous_identity"):
        trace.reduce_trace(records, observer, control)


def testFileRenameInvalidatesInsteadOfStaleDbAttribution():
    records, observer, control = fixture()
    records.insert(9, dict(kind="file_other", qpc=45, object=300, key=301, irp=402, tid=101, opcode=71))
    with pytest.raises(trace.Invalid, match="ambiguous_identity"):
        trace.reduce_trace(records, observer, control)


def testTidReuseInvalidates():
    records, observer, control = fixture()
    records.insert(9, dict(kind="thread_start", qpc=45, pid=100, tid=101, rundown=False))
    with pytest.raises(trace.Invalid, match="thread_reuse_or_missing_start"):
        trace.reduce_trace(records, observer, control)


def testStopBoundaryExplicitlyTruncatesOpenCall():
    records, observer, control = fixture()
    observer["cases"][0]["calls"][0]["end"] = None
    call = trace.reduce_trace(records, observer, control)["cases"][0]["calls"][0]
    assert call["end_us"] == 90
    assert call["returned_in_capture"] is False
    assert sum(call["state_us"].values()) == 75


def testUnknownInitialStateRemainsUnknown():
    records, observer, control = fixture()
    observer["cases"][0]["calls"][0]["begin"] = 22
    observer["cases"][0]["begin"] = 22
    call = trace.reduce_trace(records, observer, control)["cases"][0]["calls"][0]
    assert call["state_us"]["unknown"] == 3


def testOutputInjectionIsRejected():
    result = trace.reduce_trace(*fixture())
    result["cases"][0]["calls"][0]["path"] = "secret"
    with pytest.raises(trace.Invalid, match="summary_invalid"):
        trace.public_summary(result, 0, True, "ok", "suite_complete", True)


def testTimingFailureCannotReplaceNativeResultOrException(monkeypatch):
    owner = SimpleNamespace(lock=__import__("threading").Lock(), active=True, calls=[], omitted=0, invalid=False)
    phases = trace.Phases(owner, 0)
    monkeypatch.setattr(trace, "qpc", lambda: (_ for _ in ()).throw(OSError("private")))
    calls = []
    assert phases.connectionCall("commit", lambda: calls.append(1) or 7, None, {})[0] == 7
    assert calls == [1] and owner.invalid
    error = ValueError("original")
    def fail():
        calls.append(2)
        raise error
    with pytest.raises(ValueError) as raised:
        phases.connectionCall("commit", fail, None, {})
    assert raised.value is error and calls == [1, 2]


def testEndingClockFailurePreservesNativeException(monkeypatch):
    owner = SimpleNamespace(lock=__import__("threading").Lock(), active=True, calls=[], omitted=0, invalid=False)
    values = iter([1])
    monkeypatch.setattr(trace, "qpc", lambda: next(values))
    error = ValueError("original")
    with pytest.raises(ValueError) as raised:
        trace.Phases(owner, 0).connectionCall("commit", lambda: (_ for _ in ()).throw(error), None, {})
    assert raised.value is error and owner.invalid


def testProfileScopeAndCircularPool():
    root = Path(__file__).resolve().parents[2]
    profile = ET.parse(root / "scripts/windows_commit_trace.wprp")
    assert {e.attrib["Value"] for e in profile.findall(".//Keyword")} == {
        "ProcessThread", "CSwitch", "ReadyThread", "FileIO", "FileIOInit", "Filename", "DiskIO", "DiskIOInit"}
    assert profile.find(".//BufferSize").attrib == {"Value": "64"}
    assert profile.find(".//Buffers").attrib == {"Value": "4096", "PercentageOfTotalMemory": "false"}
    assert not profile.findall(".//Stack")
    assert profile.find(".//Profile").attrib["LoggingMode"] == "Memory"


def testOneShotWorkflowGuardAndNoRawPublication():
    root = Path(__file__).resolve().parents[2]
    source = (root / ".github/workflows/runtime-kernel-trace-diagnostics.yml").read_text()
    for expected in ("github.run_attempt == 1", "github.event.before == '" + trace.BASE + "'", trace.NONCE,
                     "persist-credentials: false", "branches: [agent/runtime-workflow-architecture-v1]"):
        assert expected in source
    assert "upload-artifact" not in source and "actions/cache" not in source and "workflow_dispatch" not in source


def testEqualTimestampCrossCpuMigrationIsOrderIndependent():
    records, observer, control = fixture()
    records[0]["processors"] = 2
    records.insert(2, dict(kind="switch", qpc=11, cpu=1, old_tid=0, new_tid=2, old_state=5))
    # The target moves from CPU0 to CPU1 at the same QPC. Destination can be
    # delivered before source by the merged consumer.
    at = next(i for i, e in enumerate(records) if e.get("qpc") == 60)
    migration = [dict(kind="switch", qpc=55, cpu=1, old_tid=2, new_tid=101, old_state=5),
                 dict(kind="switch", qpc=55, cpu=0, old_tid=101, new_tid=1, old_state=1)]
    records[at:at] = migration
    for event in records:
        if event.get("kind") == "switch" and event["qpc"] >= 61:
            event["cpu"] = 1
            if event["old_tid"] == 1:
                event["old_tid"] = 2
            if event["new_tid"] == 1:
                event["new_tid"] = 2
    first = trace.reduce_trace(records, observer, control)
    records[at:at+2] = list(reversed(migration))
    assert trace.reduce_trace(records, observer, control) == first


def testMissingCpuPrefixInvalidatesDespiteRetainedProcessStart():
    records, observer, control = fixture()
    records[0]["processors"] = 2
    records.insert(4, dict(kind="switch", qpc=23, cpu=1, old_tid=0, new_tid=2, old_state=5))
    with pytest.raises(trace.Invalid, match="circular_prefix_unproven"):
        trace.reduce_trace(records, observer, control)


def testChangedFileObjectCannotReuseOldPath():
    records, observer, control = fixture()
    records.insert(9, dict(kind="file_create", qpc=45, object=300, irp=402, name=r"\Device\Volume1\other.db"))
    with pytest.raises(trace.Invalid, match="ambiguous_identity"):
        trace.reduce_trace(records, observer, control)


def testUnrelatedKnownFileKeyConflictsWithDbObject():
    records, observer, control = fixture()
    records[8]["name"] = r"\Device\Volume1\unrelated.db"
    with pytest.raises(trace.Invalid, match="ambiguous_identity"):
        trace.reduce_trace(records, observer, control)


def testStoppedCasesCannotUsePostStopEvents():
    records, observer, control = fixture()
    control["stop_request"] = 92
    call = trace.reduce_trace(records, observer, control)["cases"][0]["calls"][0]
    assert call["returned_in_capture"] is False
    assert call["state_us"] == dict(running=11, ready=1, blocked=30, unknown=0)


def testRetiredProbeForwardsWithoutAttachingToLaterOwner(monkeypatch):
    owner = SimpleNamespace(lock=__import__("threading").Lock(), active=False, calls=[], omitted=0, invalid=False)
    monkeypatch.setattr(trace, "qpc", lambda: pytest.fail("retired probe read clock"))
    assert trace.Phases(owner, 0).connectionCall("commit", lambda: 19, None, {})[0] == 19
    assert owner.calls == []


def testControllerDeadlinesAndSingleStartAreStatic():
    import ast
    source = Path(trace.__file__).read_text()
    tree = ast.parse(source)
    starts = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "wr" and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "-start"]
    assert len(starts) == 1
    assert trace.STOP_AFTER + trace.STOP_SECONDS + trace.CANCEL_SECONDS + 30 <= 1800
    assert "parse_deadline - time.monotonic()" in source
    assert "if not started:\n                shutil.rmtree(root)" in source
    assert "start-attempted.private" in source


def testWrongEnvironmentCannotStartCaptureAndCleansOwnDirectory(monkeypatch, tmp_path, capsys):
    # This fixture must reject every host, including the real Windows CI runner,
    # before native capability probes or the runner's real GITHUB_OUTPUT access.
    monkeypatch.setattr(trace, "os", SimpleNamespace(name="fixture", environ={}))
    native_calls = []
    monkeypatch.setattr(trace, "checked_private", lambda *a, **kw: native_calls.append(a) or 0)
    root = tmp_path / "private"
    root.mkdir()
    reader = root / "reader.exe"
    reader.write_bytes(b"fixture")
    assert trace.control_run(root, reader) == 2
    message = json.loads(capsys.readouterr().out)
    assert message["reason"] == "wrong_environment"
    assert message["original_pytest_exit"] is None and not message["suite_complete"]
    assert message["cleanup_confirmed"] is True and not root.exists()
    assert native_calls == []


def controller_fixture(monkeypatch, tmp_path, *, start_error=False, cancel_error=False, suite_timeout=False):
    import subprocess
    root = tmp_path / "private"
    root.mkdir()
    reader = root / "reader.exe"
    reader.write_bytes(b"fixture")
    windows = tmp_path / "windows" / "System32"
    windows.mkdir(parents=True)
    (windows / "wpr.exe").write_bytes(b"fixture")
    environment = dict(GITHUB_ACTIONS="true", RUNNER_OS="Windows", GITHUB_RUN_ATTEMPT="1",
                       GITHUB_RUN_ID="1234", SystemRoot=str(windows.parent), R3_JOB_DEADLINE_UNIX=str(__import__("time").time()+1200))
    monkeypatch.setattr(trace, "os", SimpleNamespace(name="nt", environ=environment))
    monkeypatch.setattr(trace, "qpc", lambda: 1)
    monkeypatch.setattr(trace, "frequency", lambda: 1000000)
    monkeypatch.setattr(trace, "volume_map", lambda: {})
    monkeypatch.setattr(trace, "storage_ok", lambda root: True)
    source_checks = []
    monkeypatch.setattr(trace, "source_preflight", lambda repo, root: source_checks.append(1))
    calls = []
    reduced = trace.reduce_trace(*fixture())

    def checked(command, owned, timeout=15):
        calls.append(command)
        if "-start" in command and start_error:
            raise trace.Invalid("preflight_failed")
        if "-cancel" in command and cancel_error:
            raise trace.Invalid("cancel_failed")
        if "-status" in command:
            return 0xc5583000
        if "--session-check" in command:
            Path(command[-1]).write_text('{"matched":1,"valid":true}')
        if "--reduce" in command:
            trace.private_write(root / "reduced.private", {"reason": "ok", "result": reduced})
        return 0
    monkeypatch.setattr(trace, "checked_private", checked)

    class Suite:
        returncode = None
        killed = False
        def __init__(self, *args, **kwargs):
            original = fixture()[1]
            original.update(exit=1, finished=True, invalid=False, collected=[0,1])
            other = deepcopy(original["cases"][0])
            other["case"] = 1
            original["cases"].append(other)
            trace.private_write(root / "observer.private", original)
        def poll(self):
            return self.returncode if self.killed else (None if suite_timeout else 1)
        def wait(self, timeout):
            if suite_timeout and not self.killed:
                raise subprocess.TimeoutExpired("fixture", timeout)
            self.returncode = -9 if self.killed else 1
            return self.returncode
        def kill(self):
            self.killed = True
            self.returncode = -9
    monkeypatch.setattr(trace.subprocess, "Popen", Suite)
    # Trigger stop immediately even for the deliberately hung suite fixture.
    (root / "target.failed").write_bytes(b"1")
    return root, reader, calls, source_checks


def testUncertainStartCancelsOnlyOwnedInstance(monkeypatch, tmp_path, capsys):
    root, reader, calls, _ = controller_fixture(monkeypatch, tmp_path, start_error=True)
    assert trace.control_run(root, reader) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["reason"] == "preflight_failed" and summary["cleanup_confirmed"]
    assert sum("-start" in c for c in calls) == 1
    cancel = next(c for c in calls if "-cancel" in c)
    assert cancel[-2:] == ["-instancename", "R3CommitTrace_1234_1"]


def testFailedCancelKeepsOwnershipForIndependentCleanup(monkeypatch, tmp_path, capsys):
    root, reader, calls, _ = controller_fixture(monkeypatch, tmp_path, start_error=True, cancel_error=True)
    assert trace.control_run(root, reader) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["reason"] == "cancel_failed" and not summary["cleanup_confirmed"]
    assert (root / "start-attempted.private").read_bytes() == b"1"
    assert sum("-start" in c for c in calls) == 1


def testOriginalFailureExitSurvivesSuccessfulDiagnostics(monkeypatch, tmp_path, capsys):
    root, reader, calls, checks = controller_fixture(monkeypatch, tmp_path)
    assert trace.control_run(root, reader) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["original_pytest_exit"] == 1 and summary["suite_complete"]
    assert summary["validity"] == "VALID" and summary["cleanup_confirmed"]
    assert len(checks) == 2 and sum("-start" in c for c in calls) == 1


def testKilledSuiteNeverInventsOriginalExit(monkeypatch, tmp_path, capsys):
    root, reader, calls, _ = controller_fixture(monkeypatch, tmp_path, suite_timeout=True)
    assert trace.control_run(root, reader) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["reason"] == "suite_incomplete"
    assert summary["original_pytest_exit"] is None and not summary["suite_complete"]
    assert summary["cleanup_confirmed"]


def testTargetsCompleteStopsOnlyTraceAndStillWaitsForSuite(monkeypatch, tmp_path, capsys):
    root, reader, calls, checks = controller_fixture(monkeypatch, tmp_path)
    (root / "target.failed").unlink()
    (root / "targets-complete.private").write_bytes(b"1")
    assert trace.control_run(root, reader) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["stop_reason"] == "targets_complete"
    assert summary["suite_complete"] and summary["original_pytest_exit"] == 1
    assert sum("-start" in c for c in calls) == 1
    assert sum("-stop" in c for c in calls) == 1


def testPreflightOnlyWorkflowHasNoCaptureInvocation():
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github/workflows/runtime-kernel-preflight-only.yml"
    source = workflow.read_text()
    assert "github.run_attempt == 1" in source
    assert "github.event.before == 'ecf8bcfcf193e05807556340d670f05c0f3d9e15'" in source
    assert "fix: notify idle display streams; preflight-only 20261002" in source
    assert "& $reader --preflight" in source
    assert "python -m pytest -q scripts/diagnostics/windows_commit_trace_fixtures.py" in source
    for forbidden in ("--control", "--session-check", "wpr", "-start", "-stop", "-cancel", "upload-artifact", "actions/cache"):
        assert forbidden not in source
    assert "capture_attempted=$false" in source and "capture_attempted=$true" not in source
    assert "native_exit=$nativeExit" in source
