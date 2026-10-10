"""PLC debug regressions: synthetic peers only, never PLC hardware or job execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import json
import socket
import struct
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import grpc
import pytest

from emo_master.apps.designer.operator_editors.controller_protocol import (
    EditorContext, EditorContextError, EditorKey,
)
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer, LIMITS, SyncContext
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.apps.runtime.preview import plc_debug
from emo_master.apps.runtime.preview.plc_debug import (
    LEASE_SECONDS, MAX_SESSIONS, PlcDebugManager, endpointFromParams, parseParams,
)
from emo_master.plugins.builtins import _slmp as slmp_transport
from emo_master.plugins.builtins._slmp import (
    Slmp3ESession, SlmpConnectionError, SlmpEndpoint, SlmpProtocolError,
    SlmpResponseError, SlmpTimeoutError,
)
from tests.runtime.plc_debug_server import BlockResponse, PlcDebugServer, response
from tests.runtime.test_builtin_communication_operator_workflows import _project as plcProject
from tests.runtime.presentation.test_normal_capture import normalProject


WAIT = 2.0
READ_OPERATOR = "communication.plc.slmp_read"
WRITE_OPERATOR = "communication.plc.slmp_write"
INVALID_WRITE_GENERATIONS = [
    pytest.param({}, "E_PARAM_INVALID", id="missing"),
    pytest.param({"lockGeneration": None}, "E_PARAM_INVALID", id="null"),
    pytest.param({"lockGeneration": True}, "E_PARAM_INVALID", id="boolean"),
    pytest.param({"lockGeneration": -1}, "E_PARAM_INVALID", id="negative"),
    pytest.param({"lockGeneration": "0"}, "E_PARAM_INVALID", id="string"),
    pytest.param({"lockGeneration": 0.0}, "E_PARAM_INVALID", id="float"),
    pytest.param({"lockGeneration": 1 << 63}, "E_PARAM_INVALID", id="overflow"),
    pytest.param({"lockGeneration": 1}, "E_PLC_WRITE_LOCK_STALE", id="stale"),
    pytest.param({"lockGeneration": (1 << 63) - 1}, "E_PLC_WRITE_LOCK_STALE", id="max-stale"),
]


def eventually(predicate, message: str, timeout: float = WAIT) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, message
        time.sleep(0.005)


@dataclass
class Clock:
    now: float = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePlcClient:
    def __init__(self, endpoint) -> None:
        self.endpoint = endpoint
        self.calls: list[tuple] = []
        self.words: dict[int, int] = {}
        self.bits: dict[int, bool] = {}
        self.closed = threading.Event()
        self.open_started = threading.Event()
        self.read_started = threading.Event()
        self.write_started = threading.Event()
        self.release_open = threading.Event()
        self.release_read = threading.Event()
        self.release_write = threading.Event()
        self.block_open = False
        self.block_read = False
        self.block_write = False
        self.abort_on_close = True
        self.late_read_success = False
        self.open_error = None
        self.read_error = None
        self.write_error = None

    def _wait(self, blocked, release) -> None:
        if blocked:
            assert release.wait(4.0), "test did not release fake PLC operation"

    def _check(self) -> None:
        if self.closed.is_set():
            raise SlmpConnectionError("fake PLC connection closed")

    def open(self) -> None:
        self.calls.append(("open",))
        self.open_started.set()
        self._wait(self.block_open, self.release_open)
        self._check()
        if self.open_error:
            raise self.open_error

    def close(self) -> None:
        self.calls.append(("close",))
        self.closed.set()
        if self.abort_on_close:
            self.unblock()

    def unblock(self) -> None:
        self.release_open.set()
        self.release_read.set()
        self.release_write.set()

    def _reading(self) -> None:
        self.read_started.set()
        self._wait(self.block_read, self.release_read)
        if not self.late_read_success:
            self._check()
        if self.read_error:
            raise self.read_error

    def readWords(self, device, start, count):
        self.calls.append(("read_words", device, start, count))
        self._reading()
        return [self.words.get(start + index, 0) for index in range(count)]

    def readBits(self, start, count):
        self.calls.append(("read_bits", start, count))
        self._reading()
        return [self.bits.get(start + index, False) for index in range(count)]

    def _writing(self) -> None:
        self.write_started.set()
        self._wait(self.block_write, self.release_write)
        self._check()
        if self.write_error:
            raise self.write_error

    def writeWords(self, device, start, words):
        self.calls.append(("write_words", device, start, tuple(words)))
        self._writing()
        self.words.update({start + index: value for index, value in enumerate(words)})

    def writeBits(self, start, values):
        self.calls.append(("write_bits", start, tuple(values)))
        self._writing()
        self.bits.update({start + index: value for index, value in enumerate(values)})


class ObservedLock:
    """Expose actual contention so queued-write races do not depend on sleeps."""

    def __init__(self, lock) -> None:
        self.lock = lock
        self.contended = threading.Event()

    def acquire(self, *args, **kwargs):
        if self.lock.locked():
            self.contended.set()
        return self.lock.acquire(*args, **kwargs)

    def release(self) -> None:
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.release()


@pytest.fixture
def manager_factory():
    managers = []
    clients = []

    def create(*, clock=None, client_factory=FakePlcClient, instance="runtime-test"):
        def tracked(endpoint):
            client = client_factory(endpoint)
            clients.append(client)
            return client
        manager = PlcDebugManager(instance, clock=clock or Clock(), clientFactory=tracked)
        managers.append(manager)
        return manager

    yield create
    for client in clients:
        if isinstance(client, FakePlcClient):
            client.unblock()
    for manager in managers:
        assert manager.closeAll() == []
        assert not manager._thread.is_alive()


def open_manager(manager, *, params=None, project="project-a", port=12345):
    session = manager.reserve(project, params or {"host": "127.0.0.1", "port": port})
    reply = manager.connect(session)
    assert reply.ok, reply.message
    assert reply.session_id == session.sessionId
    assert reply.runtime_instance_id == manager.runtimeInstanceId
    assert reply.state == "connected" and not reply.write_enabled
    assert reply.ttl_ms == int(LEASE_SECONDS * 1000)
    return session


def execute(manager, session, command="read", params=None, request_id=None, context=None):
    return manager.execute(session.sessionId, manager.runtimeInstanceId, command,
                           params or {}, request_id if request_id is not None else uuid4().hex, context)


def enable(manager, session) -> None:
    generation = session.writeGeneration
    reply = execute(manager, session, "set_write_enabled", {"enabled": True, "lockGeneration": generation})
    assert reply.ok and reply.write_enabled
    assert reply.write_lock_generation == generation


def write_params(owner, params: dict) -> dict:
    """Capture permission when constructing a request, never refresh it at dispatch."""
    assert "lockGeneration" not in params
    generation = owner.write_lock_generation if isinstance(owner, pb.PlcDebugReply) else owner.writeGeneration
    return {**params, "lockGeneration": generation}


def result(reply) -> dict:
    return json.loads(reply.result_json)


def receipt(reply, outcome: str) -> dict:
    payload = result(reply)
    assert payload["receipt"]["outcome"] == outcome
    assert "readback" in payload
    assert isinstance(payload["readbackError"], str)
    return payload


def data_calls(client):
    return [call for call in client.calls if call[0] not in {"open", "close"}]


@pytest.mark.parametrize("raw", ["[]", "null", "true", '"host"', "1", "{", "{\"x\":NaN}",
                                 "{\"x\":Infinity}", "{\"x\":-Infinity}",
                                 json.dumps({"x": "a" * 65536}),
                                 json.dumps({"x": "\u6d4b" * 21846}, ensure_ascii=False)],
                         ids=["array", "null", "bool", "string", "number", "malformed", "nan",
                              "infinity", "negative-infinity", "oversized-ascii", "oversized-utf8"])
def test_parse_rejects_nonobjects_malformed_nonfinite_and_oversized_json(raw):
    with pytest.raises((ValueError, TypeError)):
        parseParams(raw)


def test_parse_enforces_utf8_byte_limit_without_rejecting_exact_boundary():
    raw = '{"x":"' + "a" * (65536 - 8) + '"}'
    assert len(raw.encode("utf-8")) == 65536
    assert parseParams(raw)["x"] == "a" * (65536 - 8)
    assert parseParams("") == {}
    assert parseParams('{"nested":{"x":[true,2,"D"]}}') == {"nested": {"x": [True, 2, "D"]}}


@pytest.mark.parametrize("raw", ['{"values":[1e999]}', '{"nested":{"value":-1e999}}'],
                         ids=["positive-overflow", "nested-negative-overflow"])
def test_parse_rejects_numbers_that_overflow_to_nonfinite_values(raw):
    with pytest.raises(ValueError):
        parseParams(raw)


@pytest.mark.parametrize("name,value", [
    ("host", ""), ("host", "  "), ("host", []), ("host", "a" * 254),
    ("port", 0), ("port", 65536), ("port", True), ("port", "10001"), ("port", 1.5),
    ("connectTimeoutMs", 0), ("connectTimeoutMs", 60001), ("connectTimeoutMs", False),
    ("responseTimeoutMs", -1), ("responseTimeoutMs", 60001), ("responseTimeoutMs", 1.0),
    ("networkNo", -1), ("networkNo", 256), ("networkNo", True),
    ("pcNo", 256), ("pcNo", None), ("moduleIoNo", 65536), ("moduleIoNo", -1),
    ("moduleStationNo", 256), ("moduleStationNo", "0"),
    ("monitoringTimer", 65536), ("monitoringTimer", False),
])
def test_invalid_endpoint_rejected_before_client_creation(manager_factory, name, value):
    manager = manager_factory(client_factory=lambda _endpoint: pytest.fail("invalid endpoint created a client"))
    with pytest.raises((ValueError, TypeError)):
        manager.reserve("project", {name: value})
    assert not manager._sessions


def test_endpoint_defaults_and_boundary_fields():
    endpoint = endpointFromParams({})
    assert endpoint == SlmpEndpoint("127.0.0.1", 10001)
    endpoint = endpointFromParams({"host": " EXAMPLE.com ", "port": 65535,
        "connectTimeoutMs": 1, "responseTimeoutMs": 60000, "networkNo": 255,
        "pcNo": 0, "moduleIoNo": 65535, "moduleStationNo": 255, "monitoringTimer": 65535})
    assert endpoint.host == "EXAMPLE.com"
    assert endpoint.connectTimeoutSec == 0.001 and endpoint.responseTimeoutSec == 60.0
    assert (endpoint.networkNo, endpoint.pcNo, endpoint.moduleIoNo,
            endpoint.moduleStationNo, endpoint.monitoringTimer) == (255, 0, 65535, 255, 65535)


@pytest.mark.parametrize("params", [
    {"device": []}, {"device": "X"}, {"dataType": {}}, {"dataType": "double"},
    {"device": "D", "dataType": "bit"}, {"device": "M", "dataType": "uint32"},
    {"device": "M", "dataType": "int32"}, {"device": "M", "dataType": "float32"},
    {"startAddress": True}, {"startAddress": -1}, {"startAddress": 0x1000000},
    {"startAddress": "0"}, {"count": False}, {"count": 0}, {"count": 961}, {"count": 1.5},
    {"dataType": "uint32", "count": 481}, {"dataType": "int32", "count": 481},
    {"dataType": "float32", "count": 481}, {"startAddress": 0xFFFFFF, "count": 2},
    {"device": "M", "startAddress": 0xFFFFFF, "count": 1},
    {"device": "M", "startAddress": 0xFFFFF0, "count": 2},
    {"device": "M", "dataType": "bit", "startAddress": 0xFFFFFF, "count": 2},
])
def test_invalid_read_ranges_never_reach_transport(manager_factory, params):
    manager = manager_factory()
    session = open_manager(manager)
    reply = execute(manager, session, params=params)
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert data_calls(session.client) == []
    assert not session.closed.is_set()


@pytest.mark.parametrize("data_type,values", [
    ("uint16", [-1]), ("uint16", [65536]), ("uint16", [True]), ("uint16", [1.0]),
    ("int16", [-32769]), ("int16", [32768]), ("int16", ["1"]),
    ("uint32", [-1]), ("uint32", [4294967296]), ("uint32", [False]),
    ("int32", [-2147483649]), ("int32", [2147483648]), ("int32", [1.5]),
    ("float32", [True]), ("float32", ["1.0"]), ("float32", [1e39]),
    ("bit", [1]), ("bit", [None]), ("bit", ["true"]),
])
def test_typed_write_values_are_validated_before_io(manager_factory, data_type, values):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    reply = execute(manager, session, "write", write_params(session,
        {"device": "M" if data_type == "bit" else "D", "dataType": data_type, "values": values}))
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert result(reply)["receipt"]["outcome"] == "rejected"
    assert data_calls(session.client) == []
    assert not session.closed.is_set()


@pytest.mark.parametrize("params", [
    {}, {"values": []}, {"values": (1,)}, {"values": [1] * 961},
    {"dataType": "uint32", "values": [1] * 481},
    {"startAddress": 0xFFFFFF, "values": [1, 2]},
    {"device": "M", "startAddress": 0xFFFFFF, "values": [1]},
    {"device": "M", "dataType": "bit", "startAddress": 0xFFFFFF, "values": [True, False]},
])
def test_invalid_write_shape_and_span_cannot_write(manager_factory, params):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    reply = execute(manager, session, "write", write_params(session, params))
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert data_calls(session.client) == []


@pytest.mark.parametrize("params,expected_count", [
    ({"count": 960}, 960), ({"dataType": "uint32", "count": 480}, 960),
    ({"device": "D", "startAddress": 0xFFFFFF}, 1),
    ({"device": "M", "startAddress": 0xFFFFF0}, 1),
    ({"device": "M", "dataType": "bit", "startAddress": 0xFFFFFF}, 1),
])
def test_valid_read_range_boundaries(manager_factory, params, expected_count):
    manager = manager_factory()
    session = open_manager(manager)
    reply = execute(manager, session, params=params)
    assert reply.ok, reply.message
    assert data_calls(session.client)[0][-1] == expected_count
    assert result(reply)["count"] == params.get("count", 1)


@pytest.mark.parametrize("command,params,request_id", [
    ("read", {}, ""), ("read", {}, "r" * 129), ("retry_write", {}, "r"),
    ("READ", {}, "r"), ("set_write_enabled", {"enabled": 1}, "r"),
    ("set_write_enabled", {"enabled": "false"}, "r"), ("set_write_enabled", {}, "r"),
])
def test_invalid_command_request_id_and_write_enable(manager_factory, command, params, request_id):
    manager = manager_factory()
    session = open_manager(manager)
    reply = execute(manager, session, command, params, request_id)
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert not reply.write_enabled and data_calls(session.client) == []


def test_write_lock_default_and_dedup_cannot_turn_rejected_request_into_later_write(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    params = write_params(session, {"values": [9]})
    locked = execute(manager, session, "write", params, "locked-request")
    assert not locked.ok and locked.code == "E_PLC_WRITE_LOCKED"
    assert data_calls(session.client) == []
    enable(manager, session)
    replay = execute(manager, session, "write", params, "locked-request")
    assert not replay.ok and replay.code == locked.code
    assert data_calls(session.client) == []
    written = execute(manager, session, "write", params, "fresh-request")
    assert written.ok
    receipt(written, "confirmed")
    assert [call[0] for call in data_calls(session.client)] == ["write_words", "read_words"]


def test_locked_write_has_rejected_receipt_and_explicit_readback_fields(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    reply = execute(manager, session, "write", {"values": [9]})
    assert not reply.ok and reply.code == "E_PLC_WRITE_LOCKED"
    assert receipt(reply, "rejected")["readback"] is None


@pytest.mark.parametrize("generation_params,expected_code", INVALID_WRITE_GENERATIONS)
def test_enabled_write_requires_current_typed_generation_before_any_payload(
        manager_factory, generation_params, expected_code):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    # These requests intentionally bypass write_params to preserve invalid tokens.
    params = {"values": [3], **generation_params}
    reply = execute(manager, session, "write", params, "invalid-generation")
    assert not reply.ok and reply.code == expected_code
    payload = receipt(reply, "rejected")
    assert payload["readback"] is None and payload["readbackError"] == ""
    assert reply.write_enabled and reply.write_lock_generation == session.writeGeneration == 0
    assert data_calls(session.client) == [] and not session.closed.is_set()
    replay = execute(manager, session, "write", params, "invalid-generation")
    assert replay.code == reply.code and replay.result_json == reply.result_json
    assert data_calls(session.client) == []


def test_confirmed_write_dedup_uses_canonical_params_and_conflicts_do_not_repeat_io(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    params = write_params(session, {"device": "D", "startAddress": 7, "dataType": "uint16", "values": [1, 65535]})
    first = execute(manager, session, "write", params, "write-once")
    assert first.ok
    payload = receipt(first, "confirmed")
    assert payload["readback"]["values"] == [1, 65535]
    assert payload["readbackError"] == ""
    assert payload["receipt"] == {"outcome": "confirmed", "device": "D", "startAddress": 7,
                                  "dataType": "uint16", "valueCount": 2}
    calls = list(data_calls(session.client))
    locked = execute(manager, session, "set_write_enabled", {"enabled": False})
    assert locked.ok and locked.write_lock_generation > params["lockGeneration"]
    replay = execute(manager, session, "write", dict(reversed(list(params.items()))), "write-once")
    assert replay.ok and not replay.write_enabled
    assert replay.write_lock_generation == locked.write_lock_generation
    assert replay.result_json == first.result_json and replay.timestamp_ms == first.timestamp_ms
    enable(manager, session)
    replay_enabled = execute(manager, session, "write", params, "write-once")
    assert replay_enabled.ok and replay_enabled.write_enabled
    assert replay_enabled.write_lock_generation == locked.write_lock_generation
    assert replay_enabled.result_json == first.result_json and replay_enabled.timestamp_ms == first.timestamp_ms
    conflict = execute(manager, session, "write", {**params, "values": [2, 65535]}, "write-once")
    assert not conflict.ok and conflict.code == "E_PLC_REQUEST_CONFLICT"
    changed_token = execute(manager, session, "write",
                            {**params, "lockGeneration": session.writeGeneration}, "write-once")
    assert not changed_token.ok and changed_token.code == "E_PLC_REQUEST_CONFLICT"
    receipt(changed_token, "rejected")
    assert data_calls(session.client) == calls


def test_same_request_id_on_reads_is_not_a_cached_write(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    first = execute(manager, session, request_id="read-again")
    session.client.words[0] = 22
    second = execute(manager, session, request_id="read-again")
    assert result(first)["values"] == [0] and result(second)["values"] == [22]
    assert len(data_calls(session.client)) == 2


def test_write_request_budget_is_bounded_without_evicting_a_dedup_receipt(manager_factory, monkeypatch):
    monkeypatch.setattr(plc_debug, "MAX_WRITE_REQUESTS", 2)
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    params = write_params(session, {"values": [8]})
    for request_id in ("first", "second"):
        assert execute(manager, session, "write", params, request_id).ok
    calls = list(data_calls(session.client))
    rejected = execute(manager, session, "write", params, "third")
    assert not rejected.ok and rejected.code == "E_PLC_REQUEST_LIMIT"
    assert execute(manager, session, "write", params, "first").ok
    assert data_calls(session.client) == calls


@pytest.mark.parametrize("reason", ["request-conflict", "request-limit"])
def test_write_admission_rejections_have_uniform_rejected_receipts(manager_factory, monkeypatch, reason):
    monkeypatch.setattr(plc_debug, "MAX_WRITE_REQUESTS", 1)
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    assert execute(manager, session, "write", write_params(session, {"values": [3]}), "first-write").ok
    calls = list(data_calls(session.client))
    request_id = "first-write" if reason == "request-conflict" else "new-write"
    rejected = execute(manager, session, "write", write_params(session, {"values": [4]}), request_id)
    expected = "E_PLC_REQUEST_CONFLICT" if reason == "request-conflict" else "E_PLC_REQUEST_LIMIT"
    assert not rejected.ok and rejected.code == expected
    assert receipt(rejected, "rejected")["readback"] is None
    assert data_calls(session.client) == calls


@pytest.mark.parametrize("error,code", [
    (SlmpTimeoutError("connect timeout"), "E_TIMEOUT"),
    (SlmpConnectionError("connect failed"), "E_CONNECTION_FAILED"),
    (SlmpProtocolError("bad response"), "E_PROTOCOL_INVALID"),
])
def test_connect_failure_removes_reservation_and_allows_same_endpoint(manager_factory, error, code):
    manager = manager_factory()
    session = manager.reserve("project", {})
    session.client.open_error = error
    reply = manager.connect(session)
    assert not reply.ok and reply.code == code and not reply.write_enabled
    assert session.closed.is_set() and session.sessionId not in manager._sessions
    replacement = manager.reserve("project", {})
    assert replacement.sessionId != session.sessionId


@pytest.mark.parametrize("error,code", [
    (SlmpTimeoutError("read timeout"), "E_TIMEOUT"),
    (SlmpConnectionError("read closed"), "E_CONNECTION_FAILED"),
    (SlmpProtocolError("bad length"), "E_PROTOCOL_INVALID"),
    (SlmpResponseError(0xC051), "E_PLC_RESPONSE"),
])
def test_read_failure_maps_error_and_never_retries(manager_factory, error, code):
    manager = manager_factory()
    session = open_manager(manager)
    session.client.read_error = error
    reply = execute(manager, session, params={"retryCount": 5})
    assert not reply.ok and reply.code == code
    assert len(data_calls(session.client)) == 1
    if isinstance(error, SlmpResponseError):
        assert "C051" in reply.message
    else:
        assert session.closed.is_set() and not reply.write_enabled


@pytest.mark.parametrize("error", [SlmpTimeoutError("lost ack"), SlmpConnectionError("lost ack"),
                                   SlmpProtocolError("corrupt ack")])
def test_write_without_ack_is_unknown_never_retried_and_retires_connection(manager_factory, error):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.client.write_error = error
    params = write_params(session, {"values": [3], "retryCount": 5})
    reply = execute(manager, session, "write", params, "unknown-write")
    assert not reply.ok and reply.code == "E_PLC_WRITE_UNKNOWN"
    assert result(reply)["receipt"]["outcome"] == "unknown"
    assert session.closed.is_set() and not reply.write_enabled
    assert [call[0] for call in data_calls(session.client)] == ["write_words"]
    assert not execute(manager, session, "write", params, "unknown-write").ok
    assert len(data_calls(session.client)) == 1


@pytest.mark.parametrize("error,outcome,code", [
    (SlmpConnectionError("lost ack"), "unknown", "E_PLC_WRITE_UNKNOWN"),
    (SlmpResponseError(0xC051), "rejected", "E_PLC_RESPONSE"),
    (ValueError("bad values"), "rejected", "E_PARAM_INVALID"),
])
def test_write_error_receipts_include_readback_and_readback_error(manager_factory, error, outcome, code):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    if isinstance(error, ValueError):
        params = {"values": [-1]}
    else:
        session.client.write_error = error
        params = {"values": [3]}
    reply = execute(manager, session, "write", write_params(session, params))
    assert not reply.ok and reply.code == code
    assert receipt(reply, outcome)["readback"] is None


def test_plc_rejected_write_is_not_unknown_and_identical_retry_does_not_resend(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.client.write_error = SlmpResponseError(0xC051)
    params = write_params(session, {"values": [3]})
    first = execute(manager, session, "write", params, "rejected-write")
    second = execute(manager, session, "write", params, "rejected-write")
    assert not first.ok and first.code == "E_PLC_RESPONSE"
    assert result(first)["receipt"]["outcome"] == "rejected"
    assert not second.ok and second.code in {first.code, "E_PLC_SESSION_CLOSED"}
    if second.code == first.code:
        assert second.result_json == first.result_json
    assert [call[0] for call in data_calls(session.client)] == ["write_words"]


@pytest.mark.parametrize("error", [SlmpTimeoutError("readback timeout"), SlmpProtocolError("bad readback"),
                                   SlmpResponseError(0xC051)])
def test_acknowledged_write_remains_confirmed_when_readback_fails(manager_factory, error):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.client.read_error = error
    reply = execute(manager, session, "write", write_params(session, {"values": [9]}))
    assert reply.ok, reply.message
    payload = receipt(reply, "confirmed")
    assert payload["readback"] is None and str(error) in payload["readbackError"]
    assert not reply.write_enabled and session.closed.is_set()
    assert session.client.words[0] == 9
    assert [call[0] for call in data_calls(session.client)] == ["write_words", "read_words"]


def test_injected_clock_exact_expiry_renew_and_no_implicit_data_renewal(manager_factory):
    clock = Clock()
    manager = manager_factory(clock=clock)
    session = open_manager(manager)
    clock.advance(10)
    read = execute(manager, session)
    assert read.ok and read.ttl_ms == 20000
    renew = execute(manager, session, "renew")
    assert renew.ok and renew.ttl_ms == 30000
    clock.advance(29)
    manager.expire()
    assert session.sessionId in manager._sessions
    clock.advance(1)
    manager.expire()
    assert session.sessionId not in manager._sessions and session.closed.is_set()
    assert execute(manager, session).code == "E_PLC_SESSION_CLOSED"
    assert manager.close(session.sessionId) is None
    assert manager.reserve("project", {}).sessionId != session.sessionId


@pytest.mark.parametrize("command,params", [("read", {}), ("renew", {}),
                                            ("write", {"values": [1]})])
def test_expired_lease_cannot_be_revived_or_write_even_before_sweep(manager_factory, command, params):
    clock = Clock()
    manager = manager_factory(clock=clock)
    session = open_manager(manager)
    enable(manager, session)
    if command == "write":
        params = write_params(session, params)
    clock.advance(LEASE_SECONDS)
    reply = execute(manager, session, command, params)
    assert not reply.ok and reply.code == "E_PLC_SESSION_CLOSED"
    assert data_calls(session.client) == [] and not reply.write_enabled


def test_same_endpoint_admission_covers_connecting_cross_project_case_and_route_variants(manager_factory):
    manager = manager_factory()
    session = manager.reserve("project-a", {"host": "PLC.EXAMPLE", "port": 12345})
    with pytest.raises(RuntimeError, match="endpoint"):
        manager.reserve("project-b", {"host": " plc.example ", "port": 12345, "networkNo": 3})
    assert len(manager._sessions) == 1
    assert manager.reserve("project-b", {"host": "plc.example", "port": 12346})
    assert manager.close(session.sessionId) is None
    assert manager.reserve("project-b", {"host": "plc.example", "port": 12345})


def test_session_quota_close_project_close_sessions_and_terminal_close_all(manager_factory):
    manager = manager_factory()
    sessions = [open_manager(manager, project="a" if index % 2 else "b", port=10000 + index)
                for index in range(MAX_SESSIONS)]
    with pytest.raises(RuntimeError, match="limit"):
        manager.reserve("c", {"port": 12000})
    assert manager.closeProject("a") == []
    assert all(session.closed.is_set() == (session.projectId == "a") for session in sessions)
    replacement = open_manager(manager, project="c", port=12000)
    assert manager.closeSessions() == [] and not manager._sessions
    assert replacement.closed.is_set()
    assert open_manager(manager, port=12000)
    assert manager.closeAll() == []
    assert not manager._thread.is_alive()
    with pytest.raises(RuntimeError, match="closing"):
        manager.reserve("c", {"port": 12000})


def test_wrong_runtime_identity_does_not_mutate_owner(manager_factory):
    manager = manager_factory(instance="current-runtime")
    session = open_manager(manager)
    enable(manager, session)
    reply = manager.execute(session.sessionId, "previous-runtime", "write", write_params(session, {"values": [4]}), "r")
    assert not reply.ok and reply.code == "E_PLC_CONTEXT_INVALID"
    assert reply.runtime_instance_id == "current-runtime"
    assert not session.closed.is_set() and session.writeEnabled
    assert data_calls(session.client) == []


def test_close_interrupts_blocked_connect_and_fences_late_open(manager_factory):
    manager = manager_factory()
    session = manager.reserve("project", {})
    session.client.block_open = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        connecting = pool.submit(manager.connect, session)
        try:
            assert session.client.open_started.wait(WAIT)
            assert pool.submit(manager.close, session.sessionId).result(WAIT) is None
            reply = connecting.result(WAIT)
            assert not reply.ok and not reply.write_enabled
            assert session.closed.is_set() and session.sessionId not in manager._sessions
        finally:
            session.client.unblock()
    assert open_manager(manager).sessionId != session.sessionId


def test_control_renew_and_page_lock_complete_while_read_is_blocked(manager_factory):
    clock = Clock()
    manager = manager_factory(clock=clock)
    session = open_manager(manager)
    enable(manager, session)
    session.client.block_read = True
    with ThreadPoolExecutor(max_workers=3) as pool:
        reading = pool.submit(execute, manager, session)
        try:
            assert session.client.read_started.wait(WAIT)
            clock.advance(15)
            renew = pool.submit(execute, manager, session, "renew").result(WAIT)
            assert renew.ok and renew.ttl_ms == 30000
            locked = pool.submit(execute, manager, session, "set_write_enabled", {"enabled": False}).result(WAIT)
            assert locked.ok and not locked.write_enabled
            assert not reading.done()
        finally:
            session.client.unblock()
        assert reading.result(WAIT).ok


def test_page_lock_fences_already_queued_write_without_cancelling_read(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.operation = ObservedLock(session.operation)
    session.client.block_read = True
    with ThreadPoolExecutor(max_workers=3) as pool:
        reading = pool.submit(execute, manager, session)
        try:
            assert session.client.read_started.wait(WAIT)
            writing = pool.submit(execute, manager, session, "write",
                                  write_params(session, {"values": [7]}), "queued-write")
            assert session.operation.contended.wait(WAIT)
            locked = pool.submit(execute, manager, session, "set_write_enabled", {"enabled": False}).result(WAIT)
            assert locked.ok and not locked.write_enabled
        finally:
            session.client.unblock()
        assert reading.result(WAIT).ok
        refused = writing.result(WAIT)
    assert not refused.ok and refused.code == "E_PLC_WRITE_LOCKED"
    assert [call[0] for call in data_calls(session.client)] == ["read_words"]


def test_queued_old_generation_write_stays_rejected_after_disable_and_reenable(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    params = write_params(session, {"values": [7]})
    assert params["lockGeneration"] == 0
    session.operation = ObservedLock(session.operation)
    session.client.block_read = True
    with ThreadPoolExecutor(max_workers=3) as pool:
        reading = pool.submit(execute, manager, session)
        try:
            assert session.client.read_started.wait(WAIT)
            writing = pool.submit(execute, manager, session, "write", params, "old-queued-write")
            assert session.operation.contended.wait(WAIT)
            locked = pool.submit(execute, manager, session, "set_write_enabled", {"enabled": False}).result(WAIT)
            assert locked.ok and not locked.write_enabled and locked.write_lock_generation == 1
            enabled = pool.submit(execute, manager, session, "set_write_enabled",
                {"enabled": True, "lockGeneration": locked.write_lock_generation}).result(WAIT)
            assert enabled.ok and enabled.write_enabled and enabled.write_lock_generation == 1
            assert not reading.done() and not writing.done()
        finally:
            session.client.unblock()
        assert reading.result(WAIT).ok
        refused = writing.result(WAIT)
    assert not refused.ok and refused.code == "E_PLC_WRITE_LOCK_STALE"
    assert refused.write_enabled and refused.write_lock_generation == 1
    assert receipt(refused, "rejected")["readback"] is None
    assert [call[0] for call in data_calls(session.client)] == ["read_words"]
    assert session.client.words == {} and not session.client.write_started.is_set()
    assert not session.closed.is_set()


def test_delayed_old_generation_write_cannot_use_reenabled_permission(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    captured = write_params(session, {"values": [29]})
    locked = execute(manager, session, "set_write_enabled", {"enabled": False})
    assert locked.ok and locked.write_lock_generation == captured["lockGeneration"] + 1
    enable(manager, session)
    refused = execute(manager, session, "write", captured, "delayed-write")
    assert not refused.ok and refused.code == "E_PLC_WRITE_LOCK_STALE"
    assert refused.write_enabled and refused.write_lock_generation == locked.write_lock_generation
    payload = receipt(refused, "rejected")
    assert payload["readback"] is None and payload["readbackError"] == ""
    assert data_calls(session.client) == [] and not session.closed.is_set()
    current = write_params(session, {"values": [29]})
    written = execute(manager, session, "write", current, "fresh-write")
    assert written.ok and receipt(written, "confirmed")["readback"]["values"] == [29]
    replay = execute(manager, session, "write", captured, "delayed-write")
    assert not replay.ok and replay.code == refused.code and replay.result_json == refused.result_json
    assert [call[0] for call in data_calls(session.client)] == ["write_words", "read_words"]


def test_close_read_and_queued_write_race_cannot_publish_late_success_or_write(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.operation = ObservedLock(session.operation)
    session.client.block_read = True
    session.client.abort_on_close = False
    session.client.late_read_success = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(execute, manager, session)
        try:
            assert session.client.read_started.wait(WAIT)
            writing = pool.submit(execute, manager, session, "write",
                                  write_params(session, {"values": [7]}), "queued-write")
            assert session.operation.contended.wait(WAIT)
            assert manager.close(session.sessionId, timeoutSeconds=0.0) is not None
            assert session.closed.is_set() and not session.writeEnabled
            with pytest.raises(RuntimeError, match="endpoint"):
                manager.reserve("other-project", {"host": session.endpoint.host, "port": session.endpoint.port})
        finally:
            session.client.unblock()
        assert not reading.result(WAIT).ok
        assert not writing.result(WAIT).ok
    assert [call[0] for call in data_calls(session.client)] == ["read_words"]
    eventually(lambda: session.sessionId not in manager._sessions, "closed owner was not retired")
    assert open_manager(manager).sessionId != session.sessionId


def test_concurrent_identical_write_requests_perform_exactly_one_write_and_readback(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.operation = ObservedLock(session.operation)
    session.client.block_write = True
    params = write_params(session, {"values": [11]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, manager, session, "write", params, "same-write")
        try:
            assert session.client.write_started.wait(WAIT)
            second = pool.submit(execute, manager, session, "write", params, "same-write")
            assert session.operation.contended.wait(WAIT)
        finally:
            session.client.unblock()
        replies = first.result(WAIT), second.result(WAIT)
    assert all(reply.ok for reply in replies)
    assert replies[0].result_json == replies[1].result_json
    assert [call[0] for call in data_calls(session.client)] == ["write_words", "read_words"]


def test_concurrent_duplicate_of_lost_ack_write_keeps_unknown_outcome_with_one_wire_write(manager_factory):
    committed = threading.Event()
    lose_ack = threading.Event()

    def commit_without_ack(peer, request, _sock):
        peer.memory_response(request)
        committed.set()
        assert lose_ack.wait(4.0), "test did not release lost-ACK peer"
        return None

    with PlcDebugServer([commit_without_ack]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params(responseTimeoutMs=2000))
        enable(manager, session)
        session.operation = ObservedLock(session.operation)
        params = write_params(session, {"values": [51]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(execute, manager, session, "write", params, "same-lost-ack")
            try:
                assert committed.wait(WAIT)
                locked = execute(manager, session, "set_write_enabled", {"enabled": False})
                assert locked.ok and locked.write_lock_generation == params["lockGeneration"] + 1
                enable(manager, session)
                duplicate = pool.submit(execute, manager, session, "write", params, "same-lost-ack")
                assert session.operation.contended.wait(WAIT)
                assert session.writeEnabled and not first.done() and not duplicate.done()
                lose_ack.set()
                replies = first.result(WAIT), duplicate.result(WAIT)
            finally:
                lose_ack.set()
        assert all(not reply.ok and reply.code == "E_PLC_WRITE_UNKNOWN" for reply in replies)
        assert all(receipt(reply, "unknown")["readback"] is None for reply in replies)
        assert replies[0].result_json == replies[1].result_json
        assert all(reply.write_lock_generation > params["lockGeneration"] for reply in replies)
        assert peer.words[0] == 51 and len(peer.requests) == 1 and peer.connections == 1
        assert session.closed.is_set() and not any(reply.write_enabled for reply in replies)


@pytest.mark.parametrize("generation", [None, True, -1, "0", 1])
def test_enable_requires_current_typed_lock_generation(manager_factory, generation):
    manager = manager_factory()
    session = open_manager(manager)
    params = {"enabled": True}
    if generation is not None:
        params["lockGeneration"] = generation
    reply = execute(manager, session, "set_write_enabled", params)
    expected = "E_PLC_WRITE_LOCK_STALE" if generation == 1 and generation is not True else "E_PARAM_INVALID"
    assert not reply.ok and reply.code == expected
    assert not reply.write_enabled and reply.write_lock_generation == session.writeGeneration == 0
    assert data_calls(session.client) == []


def test_old_enable_generation_is_rejected_after_page_lock_and_fresh_enable_never_queues(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    old_generation = session.writeGeneration
    session.client.block_read = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(execute, manager, session)
        try:
            assert session.client.read_started.wait(WAIT)
            locked = execute(manager, session, "set_write_enabled", {"enabled": False})
            assert locked.ok and locked.write_lock_generation > old_generation and not locked.write_enabled
            stale = pool.submit(execute, manager, session, "set_write_enabled",
                                {"enabled": True, "lockGeneration": old_generation}).result(WAIT)
            assert not stale.ok and stale.code == "E_PLC_WRITE_LOCK_STALE" and not stale.write_enabled
            assert stale.write_lock_generation == locked.write_lock_generation
            fresh = pool.submit(execute, manager, session, "set_write_enabled",
                {"enabled": True, "lockGeneration": locked.write_lock_generation}).result(WAIT)
            assert fresh.ok and fresh.write_enabled and not pending.done()
            relocked = execute(manager, session, "set_write_enabled", {"enabled": False})
            assert relocked.write_lock_generation > fresh.write_lock_generation
        finally:
            session.client.unblock()
        assert pending.result(WAIT).ok
    assert [call[0] for call in data_calls(session.client)] == ["read_words"]
    generation = session.writeGeneration
    assert manager.close(session.sessionId) is None
    assert session.writeGeneration > generation and not session.writeEnabled


def cancel_context(context):
    context.cancelled.set()
    context.finishCallbacks()


@pytest.mark.parametrize("operation", ["connect", "read", "write"])
def test_manager_context_cancellation_interrupts_socket_owner(manager_factory, operation):
    manager = manager_factory()
    session = manager.reserve("project", {}) if operation == "connect" else open_manager(manager)
    context = SyncContext()
    if operation == "write":
        enable(manager, session)
    setattr(session.client, f"block_{'open' if operation == 'connect' else operation}", True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = (pool.submit(manager.connect, session, context) if operation == "connect" else
                   pool.submit(execute, manager, session, operation,
                               write_params(session, {"values": [3]}) if operation == "write" else {},
                               "cancel-me", context))
        try:
            started = getattr(session.client, f"{'open' if operation == 'connect' else operation}_started")
            assert started.wait(WAIT)
            cancel_context(context)
            reply = pending.result(WAIT)
        finally:
            session.client.unblock()
    assert not reply.ok and not reply.write_enabled
    assert session.closed.is_set() and session.sessionId not in manager._sessions
    if operation == "write":
        assert reply.code == "E_PLC_WRITE_UNKNOWN"
        assert result(reply)["receipt"]["outcome"] == "unknown"
    assert len(data_calls(session.client)) == (0 if operation == "connect" else 1)


def test_precancelled_connect_never_opens_and_completed_callbacks_do_not_close_live_session(manager_factory):
    manager = manager_factory()
    context = SyncContext()
    context.cancelled.set()
    abandoned = manager.reserve("project", {})
    reply = manager.connect(abandoned, context)
    assert not reply.ok and not any(call[0] == "open" for call in abandoned.client.calls)
    session = manager.reserve("project", {})
    context = SyncContext()
    assert manager.connect(session, context).ok
    cancel_context(context)
    assert not session.closed.is_set()
    context = SyncContext()
    assert execute(manager, session, context=context).ok
    cancel_context(context)
    assert not session.closed.is_set()
    assert execute(manager, session).ok


def test_close_project_invalidates_all_owners_before_waiting_and_keeps_other_project(manager_factory):
    manager = manager_factory()
    owners = [open_manager(manager, project="a", port=12000 + index) for index in range(2)]
    other = open_manager(manager, project="b", port=12002)
    for session in owners:
        session.client.block_read = True
        session.client.abort_on_close = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(execute, manager, session) for session in owners]
        try:
            assert all(session.client.read_started.wait(WAIT) for session in owners)
            errors = manager.closeProject("a", timeoutSeconds=0.0)
            assert len(errors) == 2
            assert all(session.closed.is_set() and not session.writeEnabled for session in owners)
            assert not other.closed.is_set() and execute(manager, other).ok
        finally:
            for session in owners:
                session.client.unblock()
        assert all(not future.result(WAIT).ok for future in pending)
    assert manager.closeProject("a") == []
    assert manager.closeProject("missing") == []


def test_cancellation_after_write_ack_preserves_confirmed_receipt_and_readback_error(manager_factory):
    manager = manager_factory()
    session = open_manager(manager)
    enable(manager, session)
    session.client.block_read = True
    context = SyncContext()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute, manager, session, "write",
                              write_params(session, {"values": [17]}), "ack-before-cancel", context)
        try:
            assert session.client.read_started.wait(WAIT)
            assert session.client.words[0] == 17
            cancel_context(context)
            reply = pending.result(WAIT)
        finally:
            session.client.unblock()
    assert reply.ok and not reply.write_enabled
    payload = receipt(reply, "confirmed")
    assert payload["readback"] is None and payload["readbackError"]
    assert [call[0] for call in data_calls(session.client)] == ["write_words", "read_words"]
    assert session.sessionId not in manager._sessions


def test_injected_lease_expiry_interrupts_inflight_data_and_releases_endpoint(manager_factory):
    clock = Clock()
    manager = manager_factory(clock=clock)
    session = open_manager(manager)
    session.client.block_read = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute, manager, session)
        try:
            assert session.client.read_started.wait(WAIT)
            clock.advance(LEASE_SECONDS)
            manager.expire()
            assert not pending.result(WAIT).ok and session.closed.is_set()
        finally:
            session.client.unblock()
    assert session.sessionId not in manager._sessions
    assert open_manager(manager).sessionId != session.sessionId


def test_expiry_snapshot_cannot_close_a_lease_renewed_after_its_active_check(manager_factory):
    class RenewalClock(Clock):
        entered = threading.Event()
        release = threading.Event()
        held = False

        def __call__(self):
            captured = self.now
            if threading.current_thread().name.startswith("test-renew") and not self.held:
                self.held = True
                self.entered.set()
                assert self.release.wait(4.0), "test did not release renewal clock"
            return captured

    snapshot = threading.Event()
    finish_snapshot = threading.Event()

    class SnapshotDict(dict):
        def values(self):
            original = super().values()

            def items():
                yield from original
                if threading.current_thread().name.startswith("test-expire"):
                    snapshot.set()
                    assert finish_snapshot.wait(4.0), "test did not release expiry snapshot"
            return items()

    clock = RenewalClock()
    manager = manager_factory(clock=clock)
    session = open_manager(manager)
    manager._sessions = SnapshotDict(manager._sessions)
    clock.advance(29)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-renew") as renew_pool, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-expire") as expire_pool:
        renewing = renew_pool.submit(execute, manager, session, "renew")
        try:
            assert clock.entered.wait(WAIT)
            clock.advance(1)
            expiring = expire_pool.submit(manager.expire)
            assert snapshot.wait(WAIT)
            clock.release.set()
            renewed = renewing.result(WAIT)
            assert renewed.ok and renewed.ttl_ms == 30000
            finish_snapshot.set()
            expiring.result(WAIT)
        finally:
            clock.release.set()
            finish_snapshot.set()
    assert session.sessionId in manager._sessions and not session.closed.is_set()
    assert not session.client.closed.is_set() and execute(manager, session).ok


def test_inline_active_check_cannot_invalidate_lease_renewed_while_waiting_for_state_lock(
        manager_factory, monkeypatch):
    class RenewalClock(Clock):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
            self.held = False

        def __call__(self):
            captured = self.now
            if threading.current_thread().name.startswith("test-inline-renew") and not self.held:
                self.held = True
                self.entered.set()
                assert self.release.wait(4.0), "test did not release inline renewal clock"
            return captured

    class ObservedStateLock:
        def __init__(self, lock):
            self.lock = lock
            self.data_waiting = threading.Event()

        def __enter__(self):
            if threading.current_thread().name.startswith("test-inline-data"):
                self.data_waiting.set()
            self.lock.acquire()
            return self

        def __exit__(self, *_args):
            self.lock.release()

    clock = RenewalClock()
    manager = manager_factory(clock=clock)
    session = open_manager(manager)
    enable(manager, session)
    # Isolate the inline operation-wait check from the independent lease sweeper.
    monkeypatch.setattr(manager, "expire", lambda: None)
    session.stateLock = ObservedStateLock(session.stateLock)
    session.operation.acquire()
    clock.advance(LEASE_SECONDS - 1)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-inline-renew") as renew_pool, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-inline-data") as data_pool:
        renewing = renew_pool.submit(execute, manager, session, "renew")
        try:
            assert clock.entered.wait(WAIT)
            clock.advance(1)
            assert clock.now == session.expiresAt
            reading = data_pool.submit(execute, manager, session)
            # The old check observes expiry before blocking inside _invalidate;
            # the atomic check blocks here before inspecting the renewed lease.
            assert session.stateLock.data_waiting.wait(WAIT)
            assert not renewing.done() and not reading.done()
            clock.release.set()
            renewed = renewing.result(WAIT)
            assert renewed.ok and session.expiresAt == clock.now + LEASE_SECONDS
        finally:
            clock.release.set()
            session.operation.release()
        reply = reading.result(WAIT)
    assert reply.ok, reply.message
    assert reply.write_enabled and reply.write_lock_generation == 0
    assert reply.ttl_ms == 30000 and renewed.ttl_ms == 30000
    assert session.sessionId in manager._sessions and not session.closed.is_set()
    assert not session.client.closed.is_set() and ("close",) not in session.client.calls
    assert [call[0] for call in data_calls(session.client)] == ["read_words"]


@pytest.mark.parametrize("connect_elapsed", [31, 59], ids=["past-lease", "near-connect-budget"])
def test_sixty_second_connect_budget_outlives_initial_lease_and_connected_lease_starts_after_open(
        manager_factory, connect_elapsed):
    clock = Clock()
    manager = manager_factory(clock=clock)
    session = manager.reserve("project", {"connectTimeoutMs": 60000})
    session.client.block_open = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager.connect, session)
        try:
            assert session.client.open_started.wait(WAIT)
            clock.advance(connect_elapsed)
            manager.expire()
            assert not session.closed.is_set() and not pending.done()
            session.client.unblock()
            reply = pending.result(WAIT)
        finally:
            session.client.unblock()
    assert reply.ok and reply.state == "connected" and reply.ttl_ms == 30000
    assert session.expiresAt == clock.now + LEASE_SECONDS
    clock.advance(29)
    manager.expire()
    assert not session.closed.is_set()
    clock.advance(1)
    manager.expire()
    assert session.closed.is_set()


NUMERIC_CASES = [
    ("uint16", [0, 65535], [0, 65535]),
    ("int16", [-32768, -1, 0, 32767], [0x8000, 0xFFFF, 0, 0x7FFF]),
    ("uint32", [0, 0x12345678, 0xFFFFFFFF], [0, 0, 0x5678, 0x1234, 0xFFFF, 0xFFFF]),
    ("int32", [-2147483648, -1, 2147483647], [0, 0x8000, 0xFFFF, 0xFFFF, 0xFFFF, 0x7FFF]),
    ("float32", [-1.5, 0.0, 3.25], [0, 0xBFC0, 0, 0, 0, 0x4050]),
]


@pytest.mark.parametrize("data_type,values,words", NUMERIC_CASES)
def test_loopback_numeric_d_multiple_exchanges_one_socket_and_exact_little_endian_words(
        manager_factory, data_type, values, words):
    with PlcDebugServer(fragment=True) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        route = {"networkNo": 2, "pcNo": 3, "moduleIoNo": 0x1234, "moduleStationNo": 5,
                 "monitoringTimer": 0x4567}
        session = open_manager(manager, params=peer.params(**route))
        per_value = 1 if data_type.endswith("16") else 2
        params = {"device": "D", "startAddress": 798, "dataType": data_type, "count": len(values)}
        assert execute(manager, session, params=params).ok
        enable(manager, session)
        written = execute(manager, session, "write", write_params(session, {**params, "values": values}), "wire-write")
        assert written.ok, written.message
        payload = receipt(written, "confirmed")
        assert payload["readback"]["values"] == pytest.approx(values)
        read = execute(manager, session, params=params)
        assert read.ok and read.state == "verified"
        decoded = result(read)
        assert decoded["values"] == pytest.approx(values) and decoded["rawWords"] == words
        assert decoded["addresses"] == [f"D{798 + i * per_value}" +
            (f"-D{799 + i * per_value}" if per_value == 2 else "") for i in range(len(values))]
        peer.wait_requests(4)
        assert peer.connections == 1 and {r.connection for r in peer.requests} == {1}
        assert [r.command for r in peer.requests] == [0x0401, 0x1401, 0x0401, 0x0401]
        request = peer.requests[1]
        assert request.device == "D" and request.subcommand == 0 and request.start == 798
        assert request.count == len(words)
        assert request.payload == struct.pack("<" + "H" * len(words), *words)
        assert request.raw[2:7] == b"\x02\x03\x34\x12\x05"
        assert request.raw[9:11] == b"\x67\x45"
        assert manager.close(session.sessionId) is None


@pytest.mark.parametrize("start,count", [(0, 1), (17, 2), (18, 3), (17, 4), (18, 5)])
def test_loopback_m_bit_odd_even_counts_pack_nibbles_and_preserve_neighbors(manager_factory, start, count):
    values = [index % 3 != 1 for index in range(count)]
    with PlcDebugServer() as peer:
        peer.bits.update({start - 1: True, start + count: True})
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params())
        params = {"device": "M", "startAddress": start, "dataType": "bit", "count": count}
        assert result(execute(manager, session, params=params))["values"] == [False] * count
        enable(manager, session)
        written = execute(manager, session, "write", write_params(session, {**params, "values": values}))
        assert written.ok
        assert receipt(written, "confirmed")["readback"]["values"] == values
        again = execute(manager, session, params=params)
        assert result(again)["values"] == values and result(again)["rawWords"] == []
        assert result(again)["addresses"] == [f"M{start + index}" for index in range(count)]
        assert peer.bits[start - 1] is True and peer.bits[start + count] is True
        assert peer.connections == 1 and len(peer.requests) == 4
        request = peer.requests[1]
        assert request.subcommand == 1 and request.device == "M" and request.count == count
        assert request.payload == bytes((int(values[i]) << 4) | (int(values[i + 1]) if i + 1 < count else 0)
                                        for i in range(0, count, 2))
        assert manager.close(session.sessionId) is None


def test_loopback_m_word_access_uses_sixteen_bit_span_without_touching_neighbors(manager_factory):
    with PlcDebugServer() as peer:
        peer.bits.update({2: True, 35: True})
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params())
        enable(manager, session)
        written = execute(manager, session, "write", write_params(session, {"device": "M", "startAddress": 3,
            "dataType": "uint16", "values": [0x8001, 0x0002]}))
        assert written.ok
        readback = receipt(written, "confirmed")["readback"]
        assert readback["values"] == [0x8001, 0x0002]
        assert readback["addresses"] == ["M3-M18", "M19-M34"]
        assert peer.bits[2] is True and peer.bits[35] is True
        assert peer.bits[3] and peer.bits[18] and peer.bits[20]
        assert peer.requests[0].subcommand == 0 and peer.requests[0].count == 2
        assert manager.close(session.sessionId) is None


def test_persistent_transport_requires_explicit_open_and_does_not_reconnect_after_close():
    with PlcDebugServer() as peer:
        client = Slmp3ESession(SlmpEndpoint("127.0.0.1", peer.port, responseTimeoutSec=0.5))
        try:
            with pytest.raises(SlmpConnectionError):
                client.readWords("D", 0, 1)
            assert peer.connections == 0
            client.open()
            client.open()
            assert client.readWords("D", 0, 1) == [0]
            client.writeWords("D", 0, [23])
            assert client.readWords("D", 0, 1) == [23]
            assert peer.connections == 1 and len(peer.requests) == 3
            client.close()
            client.close()
            with pytest.raises(SlmpConnectionError):
                client.readWords("D", 0, 1)
            assert peer.connections == 1 and len(peer.requests) == 3
        finally:
            client.close()


def faulty_response(kind):
    def action(_peer, request, _sock):
        if kind == "closed":
            return None
        if kind == "subheader":
            return response(request, b"\x00\x00", subheader=b"\x50\x00")
        if kind.startswith("route-"):
            route = bytearray(request.raw[2:7])
            route[int(kind[-1])] ^= 1
            return response(request, b"\x00\x00", route=bytes(route))
        if kind.startswith("length-"):
            return response(request, length=int(kind.split("-")[1]))
        if kind == "truncated":
            return response(request, b"\x01\x00")[:10]
        if kind == "short-words":
            return response(request, b"\x01")
        if kind == "extra-words":
            return response(request, b"\x01\x00\x02\x00")
        if kind == "nonfinite-float":
            return response(request, b"\x00\x00\x80\x7f")
        if kind == "bit-nibble":
            return response(request, b"\x20")
        if kind == "bit-padding":
            return response(request, b"\x11")
        if kind == "bit-length":
            return response(request, b"\x10\x00")
        if kind == "write-data":
            return response(request, b"\x00\x00")
        if kind == "end-code":
            return response(request, end_code=0xC051)
        raise AssertionError(kind)
    return action


@pytest.mark.parametrize("kind,code", [
    ("closed", "E_CONNECTION_FAILED"), ("subheader", "E_PROTOCOL_INVALID"),
    *[(f"route-{index}", "E_PROTOCOL_INVALID") for index in range(5)],
    *[(f"length-{size}", "E_PROTOCOL_INVALID") for size in (0, 1, 1923, 65535)],
    ("short-words", "E_PROTOCOL_INVALID"), ("extra-words", "E_PROTOCOL_INVALID"),
    ("nonfinite-float", "E_PROTOCOL_INVALID"), ("bit-nibble", "E_PROTOCOL_INVALID"),
    ("bit-padding", "E_PROTOCOL_INVALID"), ("bit-length", "E_PROTOCOL_INVALID"),
    ("end-code", "E_PLC_RESPONSE"),
])
def test_loopback_read_faults_are_bounded_classified_and_never_retry(manager_factory, kind, code):
    with PlcDebugServer([faulty_response(kind)]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params(responseTimeoutMs=100))
        params = {"dataType": "float32"} if kind == "nonfinite-float" else (
            {"device": "M", "dataType": "bit"} if kind.startswith("bit-") else {})
        reply = execute(manager, session, params={**params, "retryCount": 9})
        assert not reply.ok and reply.code == code, reply
        assert peer.connections == 1 and len(peer.requests) == 1
        assert manager.close(session.sessionId) is None


def test_loopback_timeout_closes_socket_and_returns_bounded_timeout(manager_factory):
    blocked = BlockResponse()
    with PlcDebugServer([blocked]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params(responseTimeoutMs=80))
        started = time.monotonic()
        reply = execute(manager, session)
        assert not reply.ok and reply.code == "E_TIMEOUT"
        assert time.monotonic() - started < WAIT
        assert blocked.peer_closed.wait(WAIT)
        assert len(peer.requests) == 1 and peer.connections == 1
        assert session.closed.is_set()


def test_loopback_non_listening_endpoint_fails_with_bounded_connect_error(manager_factory, monkeypatch):
    attempts = []
    original_connect = slmp_transport._connectSessionSocket

    def observed_connect(*args, **kwargs):
        attempts.append(args[1])
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(slmp_transport, "_connectSessionSocket", observed_connect)
    # A bound, non-listening socket keeps this ephemeral port unavailable throughout the test.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        manager = manager_factory(client_factory=Slmp3ESession)
        session = manager.reserve("project", {"host": "127.0.0.1", "port": reserved.getsockname()[1],
            "connectTimeoutMs": 500, "responseTimeoutMs": 500})
        started = time.monotonic()
        reply = manager.connect(session)
        # Windows may deliver refusal after the configured connect deadline.
        assert not reply.ok and reply.code in {"E_CONNECTION_FAILED", "E_TIMEOUT"}
        assert time.monotonic() - started < WAIT
        assert not reply.write_enabled and session.sessionId not in manager._sessions
        assert len(attempts) == 1
        assert execute(manager, session).code == "E_PLC_SESSION_CLOSED"
        with pytest.raises(SlmpConnectionError):
            session.client.readWords("D", 0, 1)
        assert len(attempts) == 1


def test_loopback_truncated_body_then_peer_close_is_connection_error(manager_factory):
    def truncated(_peer, request, sock):
        sock.sendall(response(request, b"\x01\x00")[:10])
        return None
    with PlcDebugServer([truncated]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params())
        reply = execute(manager, session)
        assert not reply.ok and reply.code == "E_CONNECTION_FAILED"
        assert session.closed.is_set() and len(peer.requests) == 1


@pytest.mark.parametrize("fault", ["closed", "write-data", "route-0"])
def test_loopback_committed_write_with_lost_or_invalid_ack_is_unknown_and_not_retried(manager_factory, fault):
    def commit_then_fail(peer, request, sock):
        peer.memory_response(request)
        return faulty_response(fault)(peer, request, sock)
    with PlcDebugServer([commit_then_fail]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params())
        enable(manager, session)
        params = write_params(session, {"values": [41], "retryCount": 9})
        reply = execute(manager, session, "write", params, "lost-ack")
        assert not reply.ok and reply.code == "E_PLC_WRITE_UNKNOWN"
        assert result(reply)["receipt"]["outcome"] == "unknown"
        assert peer.words[0] == 41 and len(peer.requests) == 1 and peer.connections == 1
        assert not execute(manager, session, "write", params, "lost-ack").ok
        assert len(peer.requests) == 1


def test_loopback_plc_end_code_rejects_write_without_readback_or_retry(manager_factory):
    with PlcDebugServer([faulty_response("end-code")]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params())
        enable(manager, session)
        reply = execute(manager, session, "write", write_params(session, {"values": [41], "retryCount": 9}))
        assert not reply.ok and reply.code == "E_PLC_RESPONSE"
        assert result(reply)["receipt"]["outcome"] == "rejected"
        assert peer.words == {} and len(peer.requests) == 1 and peer.connections == 1
        assert manager.close(session.sessionId) is None


def test_loopback_ack_then_failed_readback_does_not_change_confirmed_receipt(manager_factory):
    with PlcDebugServer([PlcDebugServer._memory_action, faulty_response("short-words")]) as peer:
        manager = manager_factory(client_factory=Slmp3ESession)
        session = open_manager(manager, params=peer.params())
        enable(manager, session)
        reply = execute(manager, session, "write", write_params(session, {"values": [41]}))
        assert reply.ok
        payload = receipt(reply, "confirmed")
        assert payload["readback"] is None and payload["readbackError"]
        assert not reply.write_enabled and session.closed.is_set()
        assert peer.words[0] == 41 and len(peer.requests) == 2 and peer.connections == 1


@pytest.fixture
def runtime(tmp_path):
    service = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "jobs")
    try:
        directory = tmp_path / "project"
        directory.mkdir()
        document = plcProject(10001)
        (directory / "project.json").write_text(document.model_dump_json(), encoding="utf-8")
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(directory)), None)
        scan = service.pluginScanResult
        required = {READ_OPERATOR, WRITE_OPERATOR, "vision.compare.number"}
        issues = [
            f"{operator}: {issue.ruleId}/{issue.code}: {issue.message}"
            for operator, rejected in scan.rejectedOperators.items()
            for issue in rejected
            if operator in required or issue.ruleId in {"R00", "R01"} or not scan.activeOperators
        ]
        assert loaded.ok, (
            f"{loaded.message}\nPluginScan active={len(scan.activeOperators)}; "
            "required-operator/manifest/root issues:\n" + "\n".join(issues or ["<none reported>"])
        )
        yield service
    finally:
        for record in service.jobRepository.all():
            if not record.isTerminal and record.pid is None:
                service.jobRepository.update(record.jobId, status="FAILED")
        service.close()


@contextmanager
def connected_client(runtime, mode):
    server = channel = client = None
    try:
        if mode == "INPROCESS":
            implementation = runtime
            target = ""
        else:
            server = AioRuntimeServer(runtime, None)
            target = f"127.0.0.1:{server.port}"
            channel = grpc.insecure_channel(target)
            grpc.channel_ready_future(channel).result(timeout=WAIT)
            implementation = rpc.RuntimeServiceStub(channel)
        client = RuntimeClient(implementation, deadlineMs=1000, runtimeTarget=target)
        yield SimpleNamespace(client=client, server=server, channel=channel, target=target, rpc=implementation)
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            try:
                assert runtime.plcDebugManager.closeSessions() == []
            finally:
                if channel is not None:
                    channel.close()
                if server is not None:
                    server.close()
                    assert not server.thread.is_alive() and not any(server.active.values())


@pytest.fixture(params=["INPROCESS", "GRPC_AIO"])
def client_path(request, runtime):
    with connected_client(runtime, request.param) as path:
        yield path


def editor_context(runtime, client, *, operator=READ_OPERATOR, node="read"):
    return EditorContext(key=EditorKey(runtime.loadedProjectId, "main", node), operatorId=operator,
        version="1.0.0", previewMode="none", paramSchema={}, runtimeClient=client,
        applyParams=lambda *_args: pytest.fail("PLC debug changed saved node parameters"),
        appendLog=lambda *_args: None)


def open_client(runtime, client, params, *, node="read", operator=READ_OPERATOR, cancellation=None):
    reply = client.openPlcDebugSession(runtime.loadedProjectId, "main", node, operator, params,
                                      uuid4().hex, cancellation=cancellation)
    assert isinstance(reply, pb.PlcDebugReply)
    assert reply.ok, reply.message
    assert reply.session_id and reply.runtime_instance_id == runtime.runtimeInstanceId
    assert reply.state == "connected" and not reply.write_enabled and 0 < reply.ttl_ms <= 30000
    return reply


def client_execute(client, opened, command="read", params=None, *, request_id=None, cancellation=None):
    return client.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id, command,
        params or {}, request_id or uuid4().hex, cancellation=cancellation)


def client_enable(client, opened):
    current = client_execute(client, opened, "renew")
    assert current.ok, current.message
    reply = client_execute(client, opened, "set_write_enabled",
                           {"enabled": True, "lockGeneration": current.write_lock_generation})
    assert reply.ok and reply.write_enabled, reply.message
    assert reply.write_lock_generation == current.write_lock_generation
    return reply


def test_editor_context_runtime_client_roundtrip_preserves_receipt_and_runtime_identity(runtime, client_path):
    with PlcDebugServer() as peer:
        peer.words[798] = 6
        context = editor_context(runtime, client_path.client)
        saved = runtime.loadedDocument.model_dump_json()
        params = peer.params()
        opened = context.openPlcDebugSession(params, "editor-open")
        assert isinstance(opened, pb.PlcDebugReply) and opened.ok, opened.message
        assert opened.runtime_instance_id == runtime.runtimeInstanceId
        assert context.plcDebugLocation() == (client_path.target or "\u672c\u673a\u5185\u5d4c Runtime")
        data = {"device": "D", "startAddress": 798, "dataType": "uint16", "count": 1}
        read = context.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id, "read", data, "editor-read")
        assert read.ok and result(read)["values"] == [6]
        unlocked = context.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id,
            "set_write_enabled", {"enabled": True, "lockGeneration": opened.write_lock_generation}, "editor-unlock")
        assert unlocked.ok and unlocked.write_enabled
        captured_write = write_params(unlocked, {**data, "values": [321]})
        written = context.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id,
            "write", captured_write, "editor-write")
        assert written.ok and receipt(written, "confirmed")["readback"]["values"] == [321]
        replay = context.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id,
            "write", captured_write, "editor-write")
        assert replay.result_json == written.result_json
        renewed = context.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id, "renew", {}, "editor-renew")
        assert renewed.ok and 0 < renewed.ttl_ms <= 30000
        assert runtime.loadedDocument.model_dump_json() == saved
        assert params == peer.params()
        assert peer.connections == 1 and len(peer.requests) == 3
        closed = context.closePlcDebugSession(opened.session_id, opened.runtime_instance_id)
        assert isinstance(closed, pb.PlcDebugReply) and closed.ok and closed.state == "closed"
        assert closed.runtime_instance_id == opened.runtime_instance_id
        assert not context.executePlcDebugCommand(opened.session_id, opened.runtime_instance_id,
            "read", data, "after-close").ok
        assert context.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok


@pytest.mark.parametrize("node,operator", [("read", READ_OPERATOR), ("write", WRITE_OPERATOR)])
def test_service_admits_both_plc_operator_nodes(runtime, client_path, node, operator):
    with PlcDebugServer() as peer:
        opened = open_client(runtime, client_path.client, peer.params(), node=node, operator=operator)
        assert client_execute(client_path.client, opened).ok
        assert client_path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok


def test_runtime_identity_mismatch_rejects_remote_execute_and_close_without_touching_owner(runtime, client_path):
    with PlcDebugServer() as peer:
        opened = open_client(runtime, client_path.client, peer.params())
        rejected = client_path.client.executePlcDebugCommand(opened.session_id, "old-runtime", "read", {}, "wrong-runtime")
        assert not rejected.ok and rejected.code == "E_PLC_CONTEXT_INVALID"
        assert rejected.runtime_instance_id == runtime.runtimeInstanceId
        refused_close = client_path.client.closePlcDebugSession(opened.session_id, "old-runtime")
        assert not refused_close.ok and refused_close.code == "E_PLC_CONTEXT_INVALID"
        assert opened.session_id in runtime.plcDebugManager._sessions
        assert peer.requests == []
        assert client_execute(client_path.client, opened).ok
        assert client_path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok


@pytest.mark.parametrize("fields", [
    {"project_id": "other-project"}, {"project_id": ""}, {"workflow_id": ""}, {"node_id": ""},
    {"workflow_id": "w" * 257}, {"node_id": "n" * 257},
    {"operator_id": "communication.plc.unregistered"}, {"operator_id": "vision.compare.number"},
])
def test_service_rejects_foreign_project_unbounded_draft_identity_and_non_plc_operator(runtime, client_path, fields):
    request = pb.OpenPlcDebugSessionRequest(project_id=runtime.loadedProjectId, workflow_id="main",
        node_id="read", operator_id=READ_OPERATOR, params_json="{}", request_id="bad-context")
    for name, value in fields.items():
        setattr(request, name, value)
    reply = (client_path.rpc.OpenPlcDebugSession(request, None) if client_path.server is None else
             client_path.rpc.OpenPlcDebugSession(request, timeout=WAIT))
    assert not reply.ok and reply.code == "E_PLC_CONTEXT_INVALID"
    assert not runtime.plcDebugManager._sessions


@pytest.mark.parametrize("workflow,node,operator", [
    ("main", "unsaved-node", READ_OPERATOR),
    ("unsaved-workflow", "unsaved-node", WRITE_OPERATOR),
    ("main", "read", WRITE_OPERATOR),
])
def test_unsaved_draft_plc_context_can_debug_without_saving_or_reloading_project(
        runtime, client_path, workflow, node, operator):
    project_file = runtime.workspaceRoot.parent / "project" / "project.json"
    saved = project_file.read_bytes()
    document = runtime.loadedDocument.model_dump_json()
    with PlcDebugServer() as peer:
        opened = client_path.client.openPlcDebugSession(runtime.loadedProjectId, workflow, node, operator,
                                                       peer.params(), "unsaved-draft")
        assert opened.ok, opened.message
        session = runtime.plcDebugManager._sessions[opened.session_id]
        assert session.projectId == runtime.loadedProjectId and session.workflowId == workflow and session.nodeId == node
        assert client_execute(client_path.client, opened).ok
        assert runtime.loadedDocument.model_dump_json() == document
        assert project_file.read_bytes() == saved
        assert len(peer.requests) == 1 and peer.connections == 1
        assert client_path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok


def test_allowlisted_but_unregistered_plc_operator_cannot_debug(runtime, monkeypatch):
    monkeypatch.delitem(runtime.pluginScanResult.activeOperators, READ_OPERATOR)
    with connected_client(runtime, "INPROCESS") as path, PlcDebugServer() as peer:
        reply = path.client.openPlcDebugSession(runtime.loadedProjectId, "draft-workflow", "draft-node", READ_OPERATOR,
                                               peer.params(), "unregistered-plc")
        assert not reply.ok and reply.code == "E_PLC_CONTEXT_INVALID"
        assert peer.connections == 0 and not runtime.plcDebugManager._sessions


def test_late_wire_enable_uses_current_generation_and_cannot_undo_page_lock(runtime, client_path):
    with PlcDebugServer() as peer:
        opened = open_client(runtime, client_path.client, peer.params())
        enabled = client_enable(client_path.client, opened)
        locked = client_execute(client_path.client, opened, "set_write_enabled", {"enabled": False})
        assert locked.ok and locked.write_lock_generation > enabled.write_lock_generation
        stale = client_execute(client_path.client, opened, "set_write_enabled",
            {"enabled": True, "lockGeneration": enabled.write_lock_generation})
        assert not stale.ok and stale.code == "E_PLC_WRITE_LOCK_STALE" and not stale.write_enabled
        assert stale.write_lock_generation == locked.write_lock_generation
        rejected = client_execute(client_path.client, opened, "write", {"values": [27]})
        assert not rejected.ok and rejected.code == "E_PLC_WRITE_LOCKED"
        receipt(rejected, "rejected")
        assert peer.requests == []
        fresh = client_enable(client_path.client, opened)
        assert fresh.ok and fresh.write_lock_generation == locked.write_lock_generation


@pytest.mark.parametrize("generation_params,expected_code", INVALID_WRITE_GENERATIONS)
def test_client_paths_reject_missing_invalid_and_stale_write_tokens_without_wire_payload(
        runtime, client_path, generation_params, expected_code):
    with PlcDebugServer() as peer:
        opened = open_client(runtime, client_path.client, peer.params())
        enabled = client_enable(client_path.client, opened)
        reply = client_execute(client_path.client, opened, "write", {"values": [37], **generation_params})
        assert not reply.ok and reply.code == expected_code
        payload = receipt(reply, "rejected")
        assert payload["readback"] is None and payload["readbackError"] == ""
        assert reply.write_enabled and reply.write_lock_generation == enabled.write_lock_generation == 0
        peer.wait_connections(1)
        assert peer.connections == 1 and peer.requests == [] and peer.words == {}
        assert client_path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok


def test_client_paths_reenabled_permission_rejects_delayed_write_but_preserves_original_receipt(
        runtime, client_path):
    with PlcDebugServer() as peer:
        opened = open_client(runtime, client_path.client, peer.params())
        enabled = client_enable(client_path.client, opened)
        captured = write_params(enabled, {"values": [43]})
        first = client_execute(client_path.client, opened, "write", captured, request_id="original-write")
        assert first.ok and receipt(first, "confirmed")["readback"]["values"] == [43]
        locked = client_execute(client_path.client, opened, "set_write_enabled", {"enabled": False})
        assert locked.ok and locked.write_lock_generation == captured["lockGeneration"] + 1
        enabled = client_enable(client_path.client, opened)
        assert enabled.write_lock_generation == locked.write_lock_generation
        replay = client_execute(client_path.client, opened, "write", captured, request_id="original-write")
        assert replay.ok and replay.result_json == first.result_json and replay.timestamp_ms == first.timestamp_ms
        assert replay.write_enabled and replay.write_lock_generation == enabled.write_lock_generation
        delayed = client_execute(client_path.client, opened, "write", captured, request_id="delayed-write")
        assert not delayed.ok and delayed.code == "E_PLC_WRITE_LOCK_STALE"
        assert delayed.write_enabled and delayed.write_lock_generation == enabled.write_lock_generation
        assert receipt(delayed, "rejected")["readback"] is None
        assert peer.connections == 1 and len(peer.requests) == 2 and peer.words == {0: 43}
        current = write_params(enabled, {"values": [47]})
        fresh = client_execute(client_path.client, opened, "write", current, request_id="fresh-write")
        assert fresh.ok and receipt(fresh, "confirmed")["readback"]["values"] == [47]
        assert peer.connections == 1 and len(peer.requests) == 4 and peer.words == {0: 47}
        assert client_path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok


@pytest.mark.parametrize("raw", ["[]", "null", "{", '{"x":NaN}', '{"x":Infinity}',
    json.dumps({"x": "a" * 65536})], ids=["array", "null", "malformed", "nan", "infinity", "oversized"])
def test_service_open_and_execute_reject_invalid_json_without_io(runtime, client_path, raw):
    request = pb.OpenPlcDebugSessionRequest(project_id=runtime.loadedProjectId, workflow_id="main",
        node_id="read", operator_id=READ_OPERATOR, params_json=raw, request_id="bad-json")
    reply = (client_path.rpc.OpenPlcDebugSession(request, None) if client_path.server is None else
             client_path.rpc.OpenPlcDebugSession(request, timeout=WAIT))
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    command = pb.ExecutePlcDebugCommandRequest(session_id="missing", runtime_instance_id=runtime.runtimeInstanceId,
        command="read", params_json=raw, request_id="bad-json")
    reply = (client_path.rpc.ExecutePlcDebugCommand(command, None) if client_path.server is None else
             client_path.rpc.ExecutePlcDebugCommand(command, timeout=WAIT))
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert not runtime.plcDebugManager._sessions


@pytest.mark.parametrize("request_id", ["", "a" * 129], ids=["empty", "oversized"])
def test_service_open_requires_bounded_request_id(runtime, client_path, request_id):
    reply = client_path.client.openPlcDebugSession(runtime.loadedProjectId, "main", "read", READ_OPERATOR,
                                                   {}, request_id)
    assert not reply.ok and reply.code == "E_PARAM_INVALID"
    assert not runtime.plcDebugManager._sessions


@pytest.mark.parametrize("status", ["ACCEPTED", "STARTING", "RUNNING", "STOPPING"])
@pytest.mark.parametrize("foreign_project", [False, True], ids=["loaded-project", "foreign-project"])
def test_public_job_record_admission_blocks_debug_for_any_active_job(runtime, status, foreign_project):
    project = "other-project" if foreign_project else runtime.loadedProjectId
    record = runtime.jobManager.createJob(project, 1, "main")
    runtime.jobRepository.update(record.jobId, status=status)
    with PlcDebugServer() as peer:
        client = RuntimeClient(runtime)
        try:
            reply = client.openPlcDebugSession(runtime.loadedProjectId, "main", "read", READ_OPERATOR,
                                              peer.params(), "job-active")
            assert not reply.ok and reply.code == "E_RESOURCE_BUSY"
            assert peer.connections == 0 and not runtime.plcDebugManager._sessions
        finally:
            client.close()
            runtime.jobRepository.update(record.jobId, status="FAILED")


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "ABORTED"])
def test_terminal_public_job_records_do_not_block_debug(runtime, status):
    record = runtime.jobManager.createJob(runtime.loadedProjectId, 1, "main")
    runtime.jobRepository.update(record.jobId, status=status)
    with PlcDebugServer() as peer, connected_client(runtime, "INPROCESS") as path:
        assert open_client(runtime, path.client, peer.params()).ok


@pytest.mark.parametrize("owner", ["_handles", "_bridges", "_finalizations", "_recoveries", "_retirements"])
def test_terminal_job_with_unretired_worker_resources_refuses_debug_open(runtime, client_path, owner):
    record = runtime.jobManager.createJob("other-project", 1, "main")
    runtime.jobRepository.update(record.jobId, status="COMPLETED")
    supervisor = runtime.jobSupervisor
    # Model ownership, without creating a process, event bridge or native queue.
    with supervisor._lock:
        getattr(supervisor, owner)[record.jobId] = object()
    try:
        assert runtime.jobRepository.get(record.jobId).isTerminal
        assert supervisor.ownsJobResources(record.jobId)
        with PlcDebugServer() as peer:
            reply = client_path.client.openPlcDebugSession(runtime.loadedProjectId, "draft-workflow", "draft-node",
                READ_OPERATOR, peer.params(), "unretired-terminal-job")
            assert not reply.ok and reply.code == "E_RESOURCE_BUSY"
            assert peer.connections == 0 and not runtime.plcDebugManager._sessions
    finally:
        with supervisor._lock:
            getattr(supervisor, owner).pop(record.jobId, None)
    assert not supervisor.ownsJobResources(record.jobId)


def test_service_not_loaded_and_closing_do_not_reserve_a_socket(runtime, monkeypatch):
    with PlcDebugServer() as peer, connected_client(runtime, "INPROCESS") as path:
        project_id, directory = runtime.loadedProjectId, runtime.loadedProjectPath
        unloaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=directory + "-missing"), None)
        assert not unloaded.ok and runtime.loadedDocument is None and not runtime.loadedProjectId
        reply = path.client.openPlcDebugSession(project_id, "main", "read", READ_OPERATOR,
                                               peer.params(), "no-project")
        assert not reply.ok and reply.code == "E_PLC_CONTEXT_INVALID"
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=directory), None).ok
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "_closing", True)
            reply = path.client.openPlcDebugSession(runtime.loadedProjectId, "main", "read", READ_OPERATOR,
                                                   peer.params(), "runtime-closing")
            assert not reply.ok and reply.code == "E_RUNTIME_CLOSING"
        assert peer.connections == 0 and not runtime.plcDebugManager._sessions


def test_service_same_endpoint_rejected_and_released_by_project_reload(runtime, client_path):
    with PlcDebugServer() as peer:
        opened = open_client(runtime, client_path.client, peer.params())
        duplicate = client_path.client.openPlcDebugSession(runtime.loadedProjectId, "main", "write", WRITE_OPERATOR,
                                                          peer.params(), "duplicate-endpoint")
        assert not duplicate.ok and duplicate.code == "E_RESOURCE_BUSY"
        assert peer.connections == 1
        loaded = client_path.client.loadProject(runtime.loadedProjectPath)
        assert loaded.ok, loaded.message
        assert opened.session_id not in runtime.plcDebugManager._sessions
        assert client_execute(client_path.client, opened).code == "E_PLC_SESSION_CLOSED"
        replacement = open_client(runtime, client_path.client, peer.params())
        assert replacement.session_id != opened.session_id
        assert client_execute(client_path.client, replacement).ok
        assert peer.connections == 2


def test_start_job_closes_all_debug_sessions_before_job_creation_and_process_admission(runtime, monkeypatch):
    sessions = [runtime.plcDebugManager.reserve(project, {"port": port})
                for project, port in ((runtime.loadedProjectId, 12345), ("foreign-project", 12346))]
    clients = []
    for session in sessions:
        session.client = FakePlcClient(session.endpoint)
        clients.append(session.client)
        assert runtime.plcDebugManager.connect(session).ok
    created = runtime.jobManager.createJob
    observations = []

    def create_job(*args, **kwargs):
        assert all(client.closed.is_set() for client in clients)
        assert not runtime.plcDebugManager._sessions
        observations.append("create")
        return created(*args, **kwargs)

    def start_job(record, _spec):
        assert all(client.closed.is_set() for client in clients)
        observations.append("start")
        runtime.jobRepository.update(record.jobId, status="COMPLETED")

    monkeypatch.setattr(runtime.jobManager, "createJob", create_job)
    monkeypatch.setattr(runtime.jobManager, "start", start_job)
    reply = runtime.StartJob(pb.StartJobRequest(project_id=runtime.loadedProjectId), None)
    assert reply.ok, reply.message
    assert observations == ["create", "start"]
    assert all(runtime.plcDebugManager.execute(session.sessionId, runtime.runtimeInstanceId,
        "read", {}, "retired-by-start").code == "E_PLC_SESSION_CLOSED" for session in sessions)


def test_start_job_release_failure_refuses_job_creation(runtime, monkeypatch):
    original_close = runtime.plcDebugManager.closeSessions
    monkeypatch.setattr(runtime.plcDebugManager, "closeSessions", lambda: ["test connection still owned"])
    monkeypatch.setattr(runtime.jobManager, "createJob", lambda *_args, **_kwargs: pytest.fail("job admitted before release"))
    try:
        reply = runtime.StartJob(pb.StartJobRequest(project_id=runtime.loadedProjectId), None)
        assert not reply.ok and "E_PREVIEW_RELEASE_FAILED" in reply.message
        assert "test connection still owned" in reply.message and runtime.jobRepository.all() == []
    finally:
        monkeypatch.setattr(runtime.plcDebugManager, "closeSessions", original_close)


def test_presentation_start_closes_debug_before_public_job_admission(runtime, tmp_path, monkeypatch):
    presentation = PresentationService(runtime, tmp_path / "display")
    directory, document = normalProject(tmp_path / "presentation-project")
    prepared = presentation.prepare(document, directory)
    session = runtime.plcDebugManager.reserve(runtime.loadedProjectId, {"port": 12345})
    session.client = FakePlcClient(session.endpoint)
    assert runtime.plcDebugManager.connect(session).ok
    observations = []
    original_create = runtime.jobManager.createJob

    def create_job(*args, **kwargs):
        assert session.client.closed.is_set() and not runtime.plcDebugManager._sessions
        observations.append("create")
        return original_create(*args, **kwargs)

    def start_job(record, _spec):
        assert session.client.closed.is_set()
        observations.append("start")
        runtime.jobRepository.update(record.jobId, status="COMPLETED")

    # Keep the real preparation and start/admission path; skip only IPC/process work.
    monkeypatch.setattr(runtime.jobManager, "createJob", create_job)
    monkeypatch.setattr(runtime.jobManager, "start", start_job)
    monkeypatch.setattr(presentation, "_attach", lambda *_args, **_kwargs: {})
    try:
        job_id = presentation.start(prepared.snapshot.snapshotId, capture=False)
        assert runtime.jobRepository.get(job_id).status == "COMPLETED"
        assert observations == ["create", "start"]
    finally:
        presentation.close()


def test_presentation_start_release_failure_refuses_any_job_admission(runtime, tmp_path, monkeypatch):
    presentation = PresentationService(runtime, tmp_path / "display")
    directory, document = normalProject(tmp_path / "presentation-project")
    prepared = presentation.prepare(document, directory)
    original_close = runtime.plcDebugManager.closeSessions
    monkeypatch.setattr(runtime.plcDebugManager, "closeSessions", lambda: ["test release pending"])
    monkeypatch.setattr(runtime.jobManager, "createJob", lambda *_args, **_kwargs: pytest.fail("unsafe presentation start"))
    try:
        with pytest.raises(RuntimeError, match="E_PLC_RELEASE_FAILED.*test release pending"):
            presentation.start(prepared.snapshot.snapshotId, capture=False)
        assert runtime.jobRepository.all() == []
    finally:
        monkeypatch.setattr(runtime.plcDebugManager, "closeSessions", original_close)
        presentation.close()


def test_invalid_presentation_prepared_id_preserves_healthy_debug_session(runtime, client_path, tmp_path):
    presentation = PresentationService(runtime, tmp_path / "display")
    try:
        with PlcDebugServer() as peer:
            opened = open_client(runtime, client_path.client, peer.params())
            enabled = client_enable(client_path.client, opened)
            with pytest.raises((KeyError, ValueError)):
                presentation.start("not-a-prepared-id", capture=False)
            assert runtime.jobRepository.all() == []
            assert opened.session_id in runtime.plcDebugManager._sessions
            renewed = client_execute(client_path.client, opened, "renew")
            assert renewed.ok and renewed.write_enabled
            assert renewed.write_lock_generation == enabled.write_lock_generation
            assert client_execute(client_path.client, opened).ok
            assert peer.connections == 1 and len(peer.requests) == 1
            assert client_path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok
    finally:
        presentation.close()


@pytest.mark.parametrize("operation", ["open", "read", "write"])
def test_client_event_cancellation_interrupts_fake_connect_and_data_owners(
        runtime, client_path, manager_factory, operation):
    clients = []

    def create(endpoint):
        client = FakePlcClient(endpoint)
        setattr(client, f"block_{operation}", True)
        clients.append(client)
        return client

    runtime.plcDebugManager.closeAll()
    runtime.plcDebugManager = manager_factory(instance=runtime.runtimeInstanceId, client_factory=create)
    cancelled = threading.Event()
    client = client_path.client
    opened = None
    if operation != "open":
        opened = open_client(runtime, client, {})
        if operation == "write":
            opened = client_enable(client, opened)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = (pool.submit(open_client, runtime, client, {}, cancellation=cancelled) if operation == "open" else
                   pool.submit(client_execute, client, opened, operation,
                               write_params(opened, {"values": [31]}) if operation == "write" else {},
                               cancellation=cancelled))
        try:
            eventually(lambda: bool(clients), "client did not reserve fake connection")
            started = getattr(clients[0], f"{operation}_started")
            assert started.wait(WAIT)
            cancelled.set()
            if operation == "write" and client_path.server is None:
                reply = pending.result(WAIT)
                assert not reply.ok and reply.code == "E_PLC_WRITE_UNKNOWN"
                receipt(reply, "unknown")
            else:
                with pytest.raises(RuntimeClientError) as failure:
                    pending.result(WAIT)
                expected = "E_PLC_WRITE_UNKNOWN" if operation == "write" else "E_DISPLAY_CANCELLED"
                assert failure.value.code == expected
            eventually(lambda: not runtime.plcDebugManager._sessions, "cancelled fake owner remained admitted")
            assert clients[0].closed.is_set()
            assert len(data_calls(clients[0])) == (0 if operation == "open" else 1)
        finally:
            cancelled.set()
            for plc in clients:
                plc.unblock()


@pytest.mark.parametrize("operation", ["read", "write"])
def test_client_event_cancellation_closes_real_loopback_socket_without_retry(runtime, client_path, operation):
    blocked = BlockResponse()

    def hold(peer, request, sock):
        if operation == "write":
            peer.memory_response(request)
        return blocked(peer, request, sock)

    with PlcDebugServer([hold]) as peer:
        opened = open_client(runtime, client_path.client, peer.params(responseTimeoutMs=3000))
        if operation == "write":
            opened = client_enable(client_path.client, opened)
        cancellation = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client_execute, client_path.client, opened, operation,
                                  write_params(opened, {"values": [97]}) if operation == "write" else {},
                                  cancellation=cancellation)
            try:
                assert blocked.entered.wait(WAIT)
                cancellation.set()
                if operation == "write" and client_path.server is None:
                    reply = pending.result(WAIT)
                    assert not reply.ok and reply.code == "E_PLC_WRITE_UNKNOWN"
                    receipt(reply, "unknown")
                else:
                    with pytest.raises(RuntimeClientError) as failure:
                        pending.result(WAIT)
                    assert failure.value.code == ("E_PLC_WRITE_UNKNOWN" if operation == "write" else "E_DISPLAY_CANCELLED")
                assert blocked.peer_closed.wait(WAIT)
                eventually(lambda: not runtime.plcDebugManager._sessions, "cancelled socket stayed admitted")
                assert len(peer.requests) == 1 and peer.connections == 1
                if operation == "write":
                    assert peer.words[0] == 97
            finally:
                cancellation.set()
                blocked.release.set()


def test_cancelled_embedded_write_during_readback_preserves_confirmed_reply(runtime, manager_factory):
    clients = []

    def create(endpoint):
        client = FakePlcClient(endpoint)
        client.block_read = True
        clients.append(client)
        return client

    runtime.plcDebugManager.closeAll()
    runtime.plcDebugManager = manager_factory(instance=runtime.runtimeInstanceId, client_factory=create)
    cancellation = threading.Event()
    with connected_client(runtime, "INPROCESS") as path:
        opened = open_client(runtime, path.client, {})
        enabled = client_enable(path.client, opened)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client_execute, path.client, opened, "write", write_params(enabled, {"values": [73]}),
                                  cancellation=cancellation)
            try:
                assert clients[0].read_started.wait(WAIT)
                assert clients[0].words[0] == 73
                cancellation.set()
                reply = pending.result(WAIT)
            finally:
                cancellation.set()
                clients[0].unblock()
        assert isinstance(reply, pb.PlcDebugReply) and reply.ok and not reply.write_enabled
        payload = receipt(reply, "confirmed")
        assert payload["readback"] is None and payload["readbackError"]
        assert [call[0] for call in data_calls(clients[0])] == ["write_words", "read_words"]
        assert opened.session_id not in runtime.plcDebugManager._sessions


@pytest.mark.parametrize("mode", ["INPROCESS", "GRPC_AIO"])
def test_precancelled_client_write_never_dispatches_and_reports_plc_cancelled(
        runtime, manager_factory, monkeypatch, mode):
    runtime.plcDebugManager.closeAll()
    runtime.plcDebugManager = manager_factory(instance=runtime.runtimeInstanceId)
    calls = []
    original = runtime.ExecutePlcDebugCommand

    def observe(request, context):
        calls.append(request)
        return original(request, context)

    monkeypatch.setattr(runtime, "ExecutePlcDebugCommand", observe)
    with connected_client(runtime, mode) as path:
        opened = open_client(runtime, path.client, {})
        enabled = client_enable(path.client, opened)
        session = runtime.plcDebugManager._sessions[opened.session_id]
        calls.clear()
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(RuntimeClientError) as failure:
            client_execute(path.client, opened, "write", write_params(enabled, {"values": [19]}), cancellation=cancelled)
        assert failure.value.code == "E_PLC_CANCELLED"
        assert calls == [] and data_calls(session.client) == []
        assert not session.closed.is_set() and session.writeEnabled
        renewed = client_execute(path.client, opened, "renew")
        assert renewed.ok and renewed.write_lock_generation == enabled.write_lock_generation


@pytest.mark.parametrize("cancellation_kind", ["cancel", "deadline"])
def test_real_aio_rpc_cancellation_and_deadline_retire_data_quota_and_socket(runtime, cancellation_kind):
    blocked = BlockResponse()
    with PlcDebugServer([blocked]) as peer, connected_client(runtime, "GRPC_AIO") as path:
        opened = open_client(runtime, path.client, peer.params(responseTimeoutMs=3000))
        request = pb.ExecutePlcDebugCommandRequest(session_id=opened.session_id,
            runtime_instance_id=opened.runtime_instance_id, command="read", params_json="{}", request_id="cancel-rpc")
        pending = path.rpc.ExecutePlcDebugCommand.future(request, timeout=0.2 if cancellation_kind == "deadline" else 3)
        try:
            assert blocked.entered.wait(WAIT)
            if cancellation_kind == "cancel":
                assert pending.cancel()
                with pytest.raises(grpc.FutureCancelledError):
                    pending.result(timeout=WAIT)
            else:
                with pytest.raises(grpc.RpcError) as failure:
                    pending.result(timeout=WAIT)
                assert failure.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
            assert blocked.peer_closed.wait(WAIT)
            eventually(lambda: path.server.active["plc"] == 0, "cancelled RPC still owns PLC quota")
            eventually(lambda: not runtime.plcDebugManager._sessions, "cancelled RPC still owns session")
            assert len(peer.requests) == 1 and peer.connections == 1
        finally:
            pending.cancel()
            blocked.release.set()


@pytest.mark.parametrize("finish", ["page-lock", "close"])
def test_real_aio_control_remains_responsive_when_all_plc_data_slots_are_blocked(runtime, finish):
    blocked = BlockResponse()
    with PlcDebugServer([blocked]) as peer, connected_client(runtime, "GRPC_AIO") as path:
        opened = open_client(runtime, path.client, peer.params(responseTimeoutMs=3000))
        enabled = client_enable(path.client, opened)
        session = runtime.plcDebugManager._sessions[opened.session_id]
        session.operation = ObservedLock(session.operation)
        base = {"session_id": opened.session_id, "runtime_instance_id": opened.runtime_instance_id}
        read = path.rpc.ExecutePlcDebugCommand.future(pb.ExecutePlcDebugCommandRequest(
            **base, command="read", params_json="{}", request_id="held-read"), timeout=4)
        write = None
        try:
            assert blocked.entered.wait(WAIT)
            write = path.rpc.ExecutePlcDebugCommand.future(pb.ExecutePlcDebugCommandRequest(
                **base, command="write", params_json=json.dumps(write_params(enabled, {"values": [17]})),
                request_id="queued-write"), timeout=4)
            assert session.operation.contended.wait(WAIT)
            eventually(lambda: path.server.active["plc"] == LIMITS["plc"], "data admission was not saturated")
            with pytest.raises(grpc.RpcError) as saturated:
                path.rpc.ExecutePlcDebugCommand(pb.ExecutePlcDebugCommandRequest(
                    **base, command="read", params_json="{}", request_id="overflow"), timeout=WAIT)
            assert saturated.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
            assert client_execute(path.client, opened, "renew").ok
            assert not read.done() and not write.done()
            if finish == "page-lock":
                locked = client_execute(path.client, opened, "set_write_enabled", {"enabled": False})
                assert locked.ok and not locked.write_enabled and not read.done()
                blocked.release.set()
                assert read.result(timeout=WAIT).ok
                refused = write.result(timeout=WAIT)
                assert not refused.ok and refused.code == "E_PLC_WRITE_LOCKED"
                assert path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id).ok
            else:
                closed = path.client.closePlcDebugSession(opened.session_id, opened.runtime_instance_id)
                assert closed.ok and closed.state == "closed"
                assert not read.result(timeout=WAIT).ok and not write.result(timeout=WAIT).ok
                assert blocked.peer_closed.wait(WAIT)
            assert peer.words == {} and len(peer.requests) == 1
            assert path.server.peak["plc"] == LIMITS["plc"]
            eventually(lambda: path.server.active["plc"] == 0, "data owners failed to retire after control command")
        finally:
            blocked.release.set()
            read.cancel()
            if write is not None:
                write.cancel()


def test_runtime_client_close_cancels_blocked_data_owner(runtime, client_path):
    blocked = BlockResponse()
    with PlcDebugServer([blocked]) as peer:
        opened = open_client(runtime, client_path.client, peer.params(responseTimeoutMs=3000))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client_execute, client_path.client, opened)
            try:
                assert blocked.entered.wait(WAIT)
                client_path.client.close()
                with pytest.raises(RuntimeClientError) as failure:
                    pending.result(WAIT)
                assert failure.value.code == "E_DISPLAY_CANCELLED"
                assert blocked.peer_closed.wait(WAIT)
                eventually(lambda: not runtime.plcDebugManager._sessions, "client close left a debug owner")
            finally:
                blocked.release.set()


def test_service_open_connect_does_not_hold_project_or_job_admission_locks(runtime, manager_factory):
    client = FakePlcClient(endpointFromParams({}))
    client.block_open = True
    runtime.plcDebugManager.closeAll()
    runtime.plcDebugManager = manager_factory(instance=runtime.runtimeInstanceId, client_factory=lambda _endpoint: client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(runtime.OpenPlcDebugSession, pb.OpenPlcDebugSessionRequest(
            project_id=runtime.loadedProjectId, workflow_id="main", node_id="read", operator_id=READ_OPERATOR,
            params_json="{}", request_id="blocked-connect"), None)
        try:
            assert client.open_started.wait(WAIT)

            def acquire_admission():
                with runtime._projectStateLock, runtime._previewJobLock:
                    return True

            assert pool.submit(acquire_admission).result(WAIT)
            assert runtime.plcDebugManager.closeSessions() == []
            assert not pending.result(WAIT).ok
        finally:
            client.unblock()


@pytest.mark.parametrize("method_name,args", [
    ("openPlcDebugSession", ("project", "main", "read", READ_OPERATOR, {}, "request")),
    ("executePlcDebugCommand", ("session", "instance", "write", {"values": [3], "lockGeneration": 0}, "request")),
    ("closePlcDebugSession", ("session", "instance")),
])
@pytest.mark.parametrize("old_style", ["missing", "not-implemented"])
def test_old_embedded_runtime_reports_plc_unsupported_without_retry(method_name, args, old_style):
    calls = []

    def unsupported(_request, _context):
        calls.append("call")
        raise NotImplementedError("old Runtime")

    runtime = (SimpleNamespace() if old_style == "missing" else SimpleNamespace(
        OpenPlcDebugSession=unsupported, ExecutePlcDebugCommand=unsupported, ClosePlcDebugSession=unsupported))
    client = RuntimeClient(runtime)
    try:
        with pytest.raises(RuntimeClientError) as failure:
            getattr(client, method_name)(*args)
        assert failure.value.code == "E_PLC_UNSUPPORTED"
        assert len(calls) == (0 if old_style == "missing" else 1)
    finally:
        client.close()


def test_real_aio_old_runtime_unimplemented_maps_to_unsupported_even_for_write(runtime, monkeypatch):
    calls = []

    def unsupported(request, context):
        calls.append(request)
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "old Runtime does not expose PLC debug")

    for name in ("OpenPlcDebugSession", "ExecutePlcDebugCommand", "ClosePlcDebugSession"):
        monkeypatch.setattr(runtime, name, unsupported)
    with connected_client(runtime, "GRPC_AIO") as path:
        operations = [
            lambda: path.client.openPlcDebugSession(runtime.loadedProjectId, "main", "read", READ_OPERATOR, {}, "r"),
            lambda: path.client.executePlcDebugCommand("s", "r", "write", {"values": [1], "lockGeneration": 0}, "r"),
            lambda: path.client.closePlcDebugSession("s", "r"),
        ]
        for index, call in enumerate(operations, start=1):
            with pytest.raises(RuntimeClientError) as failure:
                call()
            assert failure.value.code == "E_PLC_UNSUPPORTED"
            assert len(calls) == index


def test_client_lost_write_rpc_reply_is_unknown_without_resubmission(monkeypatch):
    client = RuntimeClient(SimpleNamespace(ExecutePlcDebugCommand=lambda *_args: None))
    calls = []

    def lose_reply(method, request, _timeout, token, _owner, **_kwargs):
        calls.append((method, request))
        token.markDispatched()
        raise RuntimeClientError("StatusCode.UNAVAILABLE", "reply lost after submission")

    monkeypatch.setattr(client, "_displayCall", lose_reply)
    try:
        with pytest.raises(RuntimeClientError) as failure:
            client.executePlcDebugCommand("session", "instance", "write", {"values": [3], "lockGeneration": 0}, "write-once")
        assert failure.value.code == "E_PLC_WRITE_UNKNOWN" and len(calls) == 1
        request = calls[0][1]
        assert request.request_id == "write-once" and request.runtime_instance_id == "instance"
        assert json.loads(request.params_json) == {"values": [3], "lockGeneration": 0}
    finally:
        client.close()


@pytest.mark.parametrize("operator", [READ_OPERATOR, WRITE_OPERATOR])
def test_editor_context_forwards_identity_cancellation_and_copied_params_without_local_plc_io(operator):
    calls = []
    reply = pb.PlcDebugReply(ok=True, session_id="remote-session", runtime_instance_id="remote-runtime")

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return reply

    client = SimpleNamespace(_runtimeTarget="runtime.example:50051", openPlcDebugSession=record,
                            executePlcDebugCommand=record, closePlcDebugSession=record)
    context = editor_context(SimpleNamespace(loadedProjectId="project"), client, operator=operator)
    params = {"host": "plc.example", "port": 12345}
    cancellation = threading.Event()
    assert context.plcDebugLocation() == "runtime.example:50051"
    assert context.openPlcDebugSession(params, "open-id", cancellation) is reply
    assert calls[-1][0][:4] == ("project", "main", "read", operator)
    assert calls[-1][0][4] == params and calls[-1][0][4] is not params
    assert calls[-1][1]["cancellation"] is cancellation
    assert context.executePlcDebugCommand("remote-session", "remote-runtime", "read", params,
                                         "read-id", cancellation) is reply
    assert calls[-1][0][:3] == ("remote-session", "remote-runtime", "read")
    assert calls[-1][0][3] == params and calls[-1][0][3] is not params
    assert context.closePlcDebugSession("remote-session", "remote-runtime", cancellation) is reply
    assert calls[-1] == (("remote-session", "remote-runtime"), {"cancellation": cancellation})


@pytest.mark.parametrize("operator,client", [
    (READ_OPERATOR, SimpleNamespace()),
    ("vision.compare.number", SimpleNamespace(openPlcDebugSession=lambda *_args, **_kwargs: pytest.fail("non-PLC opened"))),
])
def test_editor_context_rejects_old_runtime_and_non_plc_operator(operator, client):
    context = editor_context(SimpleNamespace(loadedProjectId="project"), client, operator=operator)
    for call in (lambda: context.openPlcDebugSession({}, "r"),
                 lambda: context.executePlcDebugCommand("s", "r", "read", {}, "r"),
                 lambda: context.closePlcDebugSession("s", "r")):
        with pytest.raises(EditorContextError) as failure:
            call()
        assert failure.value.code == "E_PLC_UNSUPPORTED"
