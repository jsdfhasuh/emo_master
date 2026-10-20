from collections import deque
from ctypes import c_ulonglong
import json
from queue import Queue
import threading
from types import SimpleNamespace

import pytest

from tests.runtime import runtime_test_utils
from tests.runtime.presentation import first_result_diagnostics as evidence


def raw(kind, **fields):
    value = object.__new__(kind)
    object.__getattribute__(value, "__dict__").update(fields)
    return value


@pytest.fixture
def pipeline(monkeypatch):
    # Pure in-memory owners: no Runtime constructors, subprocess, RPC, or SQL.
    record = raw(evidence.JobRecord, jobId="job", projectId="project", status="STARTING")
    runtime = raw(evidence.RuntimeService, runtimeInstanceId="runtime",
                  jobRepository=raw(evidence.JobRepository, _jobs={"job": record}))
    store = raw(evidence.ResultStore, lock=threading.RLock(), cursor=0,
                latest={}, high={}, closedHigh={}, expired={}, open={})
    owner = raw(evidence.PresentationService, runtime=runtime, runtimeInstanceId="runtime",
                lock=threading.RLock(), store=store, pending={}, jobs={"job": {
                    "identity": {"runtimeInstanceId": "runtime", "jobId": "job"},
                    "scopeIds": ["root"], "ordinals": (c_ulonglong * 16)()}})
    runtime.__dict__["_presentationOwner"] = owner
    backend = SimpleNamespace(runtime=runtime, presentation=owner, jobId="job",
        project=raw(evidence.ProjectDocument, project=raw(evidence.ProjectMetadata, projectId="project")))
    session = raw(evidence.DisplaySession, lock=threading.RLock(), jobId="job", instanceId="runtime",
        expectedRuntimeInstanceId="", projectId="", _scheduled=set(),
        connection="CONNECTED", connectionDetail="", generation=0, cursor=0, _acceptedCursor=0,
        stats={"received": 0, "dropped": 0, "resets": 0}, high={}, loading={}, latest={},
        pending=Queue(maxsize=8), errors=deque(maxlen=64), records=deque(maxlen=128))
    monkeypatch.setattr(evidence, "_jobFailureDetails", lambda *_: '{}')
    return backend, session


def closed():
    return raw(evidence.ClosedResult, identity=raw(evidence.ResultIdentity,
        runtimeInstanceId="runtime", jobId="job", resultScopeId="root", resultKey="result", resultOrdinal=1),
        sources="PRIVATE_IMAGE_AND_VALUES_MUST_NOT_BE_READ")


@pytest.mark.parametrize("phase", ["before_capture", "before_receive", "before_apply"])
def testFirstResultEvidenceSeparatesPipelineBoundaries(pipeline, phase):
    backend, session = pipeline
    store = backend.presentation.store
    result = closed()
    if phase != "before_capture":
        backend.presentation.jobs["job"]["ordinals"][0] = 1
        store.cursor = 2
        store.high[("job", "root")] = store.closedHigh[("job", "root")] = 1
        store.latest[("job", "root")] = result
    if phase == "before_apply":
        session.stats["received"] = 1
        session.loading["root"] = result
        session.pending.queue.append((0, result, 0, 0))
    text = evidence.firstResultFailureDetails(backend, session)
    assert "PRIVATE_IMAGE" not in text
    value = json.loads(text)
    assert "non-atomic" in value["diagnostics"]
    assert value["identity"] == {"runtime": "runtime", "project": "project", "job": "job", "scope": "root"}
    assert value["producer"]["root_ordinal"] == (phase != "before_capture")
    assert bool(value["server"]["latest"]) == (phase != "before_capture")
    assert value["session"]["stats"]["received"] == (phase == "before_apply")
    assert value["session"]["queued"] == (phase == "before_apply")
    assert value["session"]["latest"] is None
    assert "does not establish delivery of the current root" in value["session"]["limitations"]


def testOpenCaptureSealAndBoundedErrorRecords(pipeline):
    backend, session = pipeline
    backend.presentation.store.open["result"] = {"identity": {
        "jobId": "job", "runtimeInstanceId": "runtime", "resultScopeId": "root", "resultOrdinal": 1}}
    backend.presentation.pending["result"] = {"job": "job", "seal": object(), "exports": {"image": object()}}
    session.errors.extend(("INVALID_RESULT", "message") for _ in range(64))
    session.records.extend({"key": str(index), "ordinal": index, "applied_to_live": False,
                            "failures": object(), "decoded": object()} for index in range(128))
    value = json.loads(evidence.firstResultFailureDetails(backend, session))
    assert value["server"]["open"] == [{"key": "result", "ordinal": 1, "has_seal": True, "export_count": 1}]
    history = value["session"]["history"]
    assert "unverified" in history["identity"]
    assert len(history["errors"]) == len(history["records"]) == 8
    assert history["records"][0] == {"key": "127", "ordinal": 127, "applied_to_live": False}


@pytest.mark.parametrize("change", ["session_job", "project", "result", "owner", "expected_runtime", "expected_project"])
def testIdentityMismatchDoesNotBorrowAnotherJob(pipeline, change):
    backend, session = pipeline
    if change == "session_job":
        session.jobId = "other"
    elif change == "project":
        backend.runtime.jobRepository._jobs["job"].projectId = "other"
    elif change == "owner":
        backend.runtime._presentationOwner = object()
    elif change == "expected_runtime":
        session.expectedRuntimeInstanceId = "other"
    elif change == "expected_project":
        session.projectId = "other"
    else:
        result = closed()
        result.identity.__dict__["jobId"] = "other"
        backend.presentation.store.latest[("job", "root")] = result
    value = json.loads(evidence.firstResultFailureDetails(backend, session))
    if change == "result":
        assert value["server"] == {"unknown": "unavailable_or_changed"}
    else:
        assert value["unknown"] == "identity_or_owner_unavailable"
        assert "server" not in value and "job_details" not in value


@pytest.mark.parametrize("change", ["session", "runtime", "project", "job"])
def testIdentityChangedDuringExistingDiagnosticIsUnknown(pipeline, monkeypatch, change):
    backend, session = pipeline
    def changed(*_):
        if change == "session":
            session.jobId = "replacement"
        else:
            setattr(backend, {"runtime": "runtime", "project": "project", "job": "jobId"}[change], object())
        return '{}'
    monkeypatch.setattr(evidence, "_jobFailureDetails", changed)
    assert json.loads(evidence.firstResultFailureDetails(backend, session))["unknown"] == "identity_or_owner_unavailable"


def testUnknownObjectsAndMappingsDoNotInvokePropertiesOrConversions(pipeline):
    backend, session = pipeline
    class Forbidden:
        def __getattribute__(self, name):
            raise AssertionError("must not read unknown object")
        def __eq__(self, other):
            raise AssertionError("must not compare unknown scalar")
        def __str__(self):
            raise AssertionError("must not stringify unknown object")
    session.instanceId = Forbidden()
    assert "identity_or_owner_unavailable" in evidence.firstResultFailureDetails(backend, session)
    assert "identity_or_owner_unavailable" in evidence.firstResultFailureDetails(backend, Forbidden())
    session.instanceId = "runtime"
    class ForbiddenDict(dict):
        def get(self, *args):
            raise AssertionError("must not query unknown mapping")
    session.high = ForbiddenDict()
    assert json.loads(evidence.firstResultFailureDetails(backend, session))["session"]["unknown"] == "unavailable_or_changed"


def testConcurrentDictionaryMutationMarksSectionUnknown(pipeline, monkeypatch):
    backend, session = pipeline
    opened = backend.presentation.store.open
    opened["result"] = {"identity": {"jobId": "job", "runtimeInstanceId": "runtime",
        "resultScopeId": "root", "resultOrdinal": 1}}
    backend.presentation.pending["result"] = {"job": "job", "exports": {}}
    scalar = evidence._scalar
    def mutate(value):
        if value == "result":
            opened["concurrent"] = {}
        return scalar(value)
    monkeypatch.setattr(evidence, "_scalar", mutate)
    value = json.loads(evidence.firstResultFailureDetails(backend, session))
    assert value["server"] == {"unknown": "unavailable_or_changed"}
    assert value["session"]["stats"]["received"] == 0


def testBusyOwnerLockIsUnknownWithoutWaiting(pipeline):
    backend, session = pipeline
    entered, release = threading.Event(), threading.Event()
    def hold():
        with backend.presentation.store.lock:
            entered.set()
            assert release.wait(2)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert entered.wait(2)
        value = json.loads(evidence.firstResultFailureDetails(backend, session))
        assert value["server"] == {"unknown": "lock_unavailable"}
        assert value["session"]["stats"]["received"] == 0
    finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()


def testFinalEncodedOutputIncludingPrefixHasHardByteBudget(pipeline, monkeypatch):
    backend, session = pipeline
    session.errors.extend(("\u0000" * 160, "\u0000" * 160) for _ in range(8))
    session.records.extend({"key": "\u0000" * 160, "ordinal": 1, "applied_to_live": False} for _ in range(8))
    monkeypatch.setattr(evidence, "_jobFailureDetails", lambda *_: json.dumps({"bounded": "x" * 16000}))
    text = evidence.firstResultFailureDetails(backend, session)
    assert len((evidence.PREFIX + text + "\n").encode("utf-8")) <= evidence.MAX_OUTPUT_BYTES
    assert json.loads(text) == {"unknown": "output_budget_exceeded"}


def testUnexpectedContainerBudgetDoesNotTraversePayload(pipeline):
    backend, session = pipeline
    backend.presentation.store.open = {str(index): object() for index in range(17)}
    session.records = deque(object() for _ in range(129))
    value = json.loads(evidence.firstResultFailureDetails(backend, session))
    assert value["server"]["unknown"] == value["session"]["unknown"] == "unavailable_or_changed"


def testExistingDiagnosticAliasCannotFollowSqlPluginReplacement(pipeline, monkeypatch):
    backend, session = pipeline
    monkeypatch.setattr(evidence, "_jobFailureDetails", runtime_test_utils.jobFailureDetails)
    def forbidden(*_):
        raise AssertionError("SQL plugin replacement must not be called")
    monkeypatch.setattr(runtime_test_utils, "jobFailureDetails", forbidden)
    value = json.loads(evidence.firstResultFailureDetails(backend, session))
    assert value["job_details"]["record"]["status"] == "STARTING"
    assert "diagnostic_error" not in value["job_details"]


def testSuccessDoesNotCollectOrReadOwners(monkeypatch):
    def forbidden(*_):
        raise AssertionError("successful wait must not collect")
    monkeypatch.setattr(evidence, "firstResultFailureDetails", forbidden)
    with evidence.firstResultFailureEvidence(object(), object()):
        pass


@pytest.mark.parametrize("failure", ["none", "collector", "output"])
def testOriginalAssertionObjectAndTracebackSurviveDiagnosticFailure(pipeline, monkeypatch, failure, capsys):
    backend, session = pipeline
    original = AssertionError("condition deadline")
    def fail():
        raise original
    def broken(*_):
        raise OSError("diagnostic failure")
    if failure == "collector":
        monkeypatch.setattr(evidence, "firstResultFailureDetails", broken)
    if failure == "output":
        monkeypatch.setattr("builtins.print", broken)
    with pytest.raises(AssertionError) as caught:
        with evidence.firstResultFailureEvidence(backend, session):
            fail()
    assert caught.value is original
    frames = []
    traceback = caught.value.__traceback__
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    assert "fail" in frames
    if failure == "none":
        output = capsys.readouterr().out
        assert output.startswith(evidence.PREFIX)
        assert len(output.encode("utf-8")) <= evidence.MAX_OUTPUT_BYTES


def testOtherExceptionsAreNotDiagnosed(monkeypatch):
    def forbidden(*_):
        raise AssertionError("only original wait assertions are diagnosed")
    monkeypatch.setattr(evidence, "firstResultFailureDetails", forbidden)
    with pytest.raises(ValueError, match="original"):
        with evidence.firstResultFailureEvidence(object(), object()):
            raise ValueError("original")
