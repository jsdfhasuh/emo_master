"""Coarse-clock expiry checks retain the original P0 deadline and ownership."""
import hashlib
from types import SimpleNamespace

import pytest

from prototypes.runtime_pages_p0 import resources as module
from prototypes.runtime_pages_p0.contracts import BUDGET
from prototypes.runtime_pages_p0.pipeline_faults import assert_read_expired


class CoarseClock:
    value = 0.0

    def __init__(self):
        self.waits = []

    def now(self):
        return self.value

    def pause(self, seconds):
        self.waits.append((self.value, seconds))
        self.value = .5 if self.value == 0 else .515625


@pytest.fixture
def asset(tmp_path, monkeypatch):
    clock = CoarseClock()
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=clock.now))
    ledger = module.Resources()
    assets = module.Assets(tmp_path / "assets", ledger)
    path = tmp_path / "input.bin"
    content = b"x" * (3 * 64 * 1024)
    path.write_bytes(content)
    item = assets.adopt("job", "result", path, len(content), hashlib.sha256(content).hexdigest(), (1, len(content)))
    try:
        yield clock, ledger, assets, item
    finally:
        assets.close()
        assert not ledger.tokens


def reader(assets, item):
    return assets.read({"job": "job", "asset_id": item["asset_id"]}, SimpleNamespace(is_active=lambda: True))


def testCoarseClockAtBoundaryDoesNotMeanStrictDeadlinePassed(asset):
    clock, ledger, assets, item = asset
    stream = reader(assets, item)
    try:
        assert len(next(stream)) == 64 * 1024
        clock.value = BUDGET.read_seconds
        assert len(next(stream)) == 64 * 1024  # Reproduces the old probe's invalid assumption.
        assert assets.entries[item["asset_id"]].readers == 1
    finally:
        stream.close()
    assert assets.entries[item["asset_id"]].readers == 0
    assert ledger.used["memory"] == 4096


def testProbeWaitsForSameClockToPassAndReleasesRealReadReservation(asset):
    clock, ledger, assets, item = asset
    assert_read_expired(reader(assets, item), clock=clock.now, pause=clock.pause)
    assert [value for value, _ in clock.waits] == [0, .5]
    assert clock.value > BUDGET.read_seconds == .5
    assert assets.entries[item["asset_id"]].readers == 0
    assert ledger.used["memory"] == 4096


@pytest.mark.parametrize("firstError", (False, True))
def testProbeFailureStillClosesReaderWithoutHidingFailure(firstError):
    clock = CoarseClock()
    closed = []
    failure = ValueError("first read failed")

    def chunks():
        try:
            if firstError:
                raise failure
            while True:
                yield b"ignores the deadline"
        finally:
            closed.append(True)

    expected = ValueError if firstError else AssertionError
    with pytest.raises(expected) as caught:
        assert_read_expired(chunks(), clock=clock.now, pause=clock.pause)
    if firstError:
        assert caught.value is failure
    assert closed == [True]
