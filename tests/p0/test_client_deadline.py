"""Deterministic time-boundary tests, not performance or GUI acceptance."""
import hashlib
from types import SimpleNamespace
import threading

import cv2
import numpy as np
import pytest

from prototypes.runtime_pages_p0 import continuous
from prototypes.runtime_pages_p0.contracts import BUDGET


def fixture(monkeypatch, elapsed_ms, corrupt=False):
    pixels = np.zeros((2, 3, 3), np.uint8)
    content = cv2.imencode('.png', pixels)[1].tobytes()
    asset = dict(asset_id="asset", job="job", result_key="result", size=len(content),
                 sha256=hashlib.sha256(content).hexdigest(), shape=list(pixels.shape))
    values = dict(image=["VALID", dict(asset=asset, raw_sha256=hashlib.sha256(pixels).hexdigest())],
                  items=["VALID", [[["count", 7]]]])
    snapshot = dict(key="result", ordinal=26, status="COMMITTED", scope_end_ns=0,
                    values=list(values.items()))
    consumer = continuous.NetworkConsumer.__new__(continuous.NetworkConsumer)
    consumer.job = 'job'
    consumer.digest = hashlib.sha256(pixels).hexdigest()
    consumer.slow = 0
    timeouts = []
    def assets(request, timeout):
        assert request == {"asset_id": "asset", "job": "job"}
        timeouts.append(timeout)
        return iter([b'bad' if corrupt else content])
    consumer.assets = assets
    monkeypatch.setattr(continuous.time, 'perf_counter_ns', lambda: round(elapsed_ms * 1e6))
    return consumer, snapshot, values, timeouts


@pytest.mark.parametrize('elapsed_ms', [500, 503.2987, 505.4541, 550])
def testApprovedClientBudgetAcceptsThrough550ms(monkeypatch, elapsed_ms):
    consumer, snapshot, values, timeouts = fixture(monkeypatch, elapsed_ms)
    diagnostics = {}
    result = consumer._decode(snapshot, values, 0, diagnostics)
    assert result['decoded'] is True
    assert diagnostics['read_phases']['stage'] == 'complete'
    assert diagnostics['read_phases']['budget_ms'] == 550
    assert timeouts == [0.5]  # Separate asset RPC deadline is not relaxed.
    assert BUDGET.export_seconds == BUDGET.seal_seconds == BUDGET.read_seconds == .5


def testOver550RetainsVerifiedButRejectedEvidence(monkeypatch):
    consumer, snapshot, values, _ = fixture(monkeypatch, 550.000001)
    diagnostics = {}
    with pytest.raises(TimeoutError, match='client read/decode deadline'):
        consumer._decode(snapshot, values, 0, diagnostics)
    assert diagnostics['asset_id'] == 'asset'
    assert diagnostics['read_phases']['verified_ns'] == 550000001
    assert diagnostics['read_phases']['stage'] == 'total_deadline'
    assert diagnostics['read_phases']['finished_ns'] == 550000001
    assert 'decoded' not in diagnostics


def testCorruptImageFailsEvenWithinBudgetAndLeavesUnknownPhases(monkeypatch):
    consumer, snapshot, values, _ = fixture(monkeypatch, 100, corrupt=True)
    diagnostics = {}
    with pytest.raises(AssertionError):
        consumer._decode(snapshot, values, 0, diagnostics)
    assert diagnostics['read_phases']['stage'] == 'encoded_verify'
    assert diagnostics['read_phases']['verified_ns'] is None
    assert diagnostics['read_phases']['decoded_ns'] is None


@pytest.mark.parametrize('failure', ['deadline', 'decode', 'rpc'])
def testReceiveKeepsFailureIdentityAndInvalidatesOldLive(monkeypatch, failure):
    consumer, snapshot, _, _ = fixture(monkeypatch, 551)
    ticks = iter([0, 551000000, 551000000, 551000000, 551000000, 551000000, 551000000])
    monkeypatch.setattr(continuous.time, 'perf_counter_ns', lambda: next(ticks))
    monkeypatch.setattr(continuous.grpc, 'channel_ready_future', lambda _: SimpleNamespace(result=lambda _: None))
    consumer.channel = object()
    consumer.updates = lambda _: iter([{'latest': snapshot}])
    consumer.assets = lambda *a, **kw: Cancelable(consumer._content)
    consumer._content = cv2.imencode('.png', np.zeros((2, 3, 3), np.uint8))[1].tobytes()
    consumer.asset_call = None
    if failure == 'decode':
        def decode_error(*args):
            raise cv2.error('injected decoder failure')
        monkeypatch.setattr(continuous.cv2, 'imdecode', decode_error)
    elif failure == 'rpc':
        def rpc_error(*args, **kwargs):
            raise continuous.grpc.RpcError('injected transport failure')
        consumer.assets = rpc_error
    consumer.live = dict(ordinal=25, scope_end_ns=0, status='COMMITTED')
    consumer.max_age_ms = 0
    consumer.rows = []
    consumer.ready = threading.Event()
    consumer.stop = threading.Event()
    consumer.error = None
    consumer._receive()
    assert consumer.error is None
    assert len(consumer.rows) == 1
    row = consumer.rows[0]
    assert (row['key'], row['ordinal'], row['asset_id']) == ('result', 26, 'asset')
    assert row['status'] == 'UNAVAILABLE' and row['decoded'] is False
    expected_type, expected_stage = {'deadline': ('TimeoutError', 'total_deadline'),
                                    'decode': ('error', 'decode'), 'rpc': ('RpcError', 'transfer')}[failure]
    assert row['read_error_type'] == expected_type
    assert row['read_phases']['stage'] == expected_stage
    assert consumer.live['ordinal'] == 26 and consumer.live['status'] == 'INCOMPLETE'
    if failure != 'rpc':
        assert consumer.asset_call.cancelled


class Cancelable:
    def __init__(self, content):
        self.content, self.cancelled = content, False

    def __iter__(self):
        return iter([self.content])

    def cancel(self):
        self.cancelled = True
