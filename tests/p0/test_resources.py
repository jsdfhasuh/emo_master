import hashlib
import time

import pytest

from prototypes.runtime_pages_p0.contracts import BUDGET, Unavailable
from prototypes.runtime_pages_p0.network import CancelContext
from prototypes.runtime_pages_p0.resources import Assets, Resources


def test_two_job_hierarchical_memory_without_counter_resets():
    ledger = Resources()
    first = ledger.reserve("one", "memory", BUDGET.job_memory)
    with pytest.raises(Unavailable, match="job memory"):
        ledger.reserve("one", "memory", 1)
    second = ledger.reserve("two", "memory", BUDGET.job_memory)
    with pytest.raises(Unavailable, match="runtime memory"):
        ledger.reserve("third", "memory", 1)
    ledger.release(first)
    assert ledger.used["memory"] == BUDGET.job_memory
    ledger.release(second)
    assert not ledger.tokens and not ledger.jobs


def test_asset_takeover_history_lease_quota_expiry_and_reader_ownership(tmp_path):
    ledger = Resources()
    assets = Assets(tmp_path / "cache", ledger)
    workspace = tmp_path / "job"
    workspace.mkdir()
    data = b"x" * (6 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    references = []
    leases = []
    try:
        for index in range(35):
            staging = ledger.reserve("one", "staging", len(data))
            path = workspace / "image.png"
            path.write_bytes(data)
            reference = assets.adopt("one", str(index), path, len(data), digest, (1, 1, 3))
            ledger.release(staging)
            references.append(reference)
            if index < 10:
                leases.append(assets.pin(reference["asset_id"]))
        assert len(assets.entries) == 32 and assets.evictions == 3
        assert not list(workspace.iterdir())
        workspace.rmdir()  # adopted images survive original workspace cleanup
        with pytest.raises(Unavailable, match="lease"):
            assets.pin(references[-1]["asset_id"])
        alias = assets.pin(references[0]["asset_id"])
        before = ledger.used["lease"]
        assets.unpin(alias)
        assert ledger.used["lease"] == before
        reader = assets.read({"job": "one", "asset_id": references[0]["asset_id"]}, CancelContext())
        assert next(reader) == data[:64*1024]
        assert assets.entries[references[0]["asset_id"]].readers == 1
        with pytest.raises(AssertionError):
            assets._delete(references[0]["asset_id"])
        reader.close()
        for lease in leases:
            assets.unpin(lease)
        assert ledger.used["lease"] == 0
        lease = assets.pin(references[-1]["asset_id"], .01)
        time.sleep(.02)
        with assets.lock:
            assets._expire()
        assert lease not in assets.leases and ledger.used["lease"] == 0
        missing = references[10]["asset_id"]
        assert missing not in assets.entries
        with pytest.raises(KeyError):
            next(assets.read({"job": "one", "asset_id": missing}, CancelContext()))
    finally:
        assets.close()
    assert not ledger.tokens and not any(ledger.used.values())


def test_cache_and_lease_turnover_without_recreating_store(tmp_path):
    ledger = Resources()
    assets = Assets(tmp_path / "cache", ledger)
    data = b"x" * (6 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    plateaus = []
    try:
        for cycle in range(3):
            leases = []
            for index in range(40):
                job = "one" if index % 2 else "two"
                staging = ledger.reserve(job, "staging", len(data))
                path = tmp_path / "staging.png"
                path.write_bytes(data)
                reference = assets.adopt(job, f"{cycle}-{index}", path, len(data), digest, (1080, 1920, 3))
                ledger.release(staging)
                if index < 10:
                    leases.append(assets.pin(reference["asset_id"]))
                if index == 10:
                    with pytest.raises(Unavailable, match="lease"):
                        assets.pin(reference["asset_id"])
            for lease in leases:
                assets.unpin(lease)
            lease = assets.pin(reference["asset_id"], .01)
            time.sleep(.02)
            with assets.lock:
                assets._expire()
            assert lease not in assets.leases
            plateaus.append(dict(ledger.used))
            assert len(assets.entries) == 32 and ledger.used["lease"] == 0
        assert plateaus[0] == plateaus[1] == plateaus[2]
        assert assets.evictions == 88
    finally:
        assets.close()
    assert not ledger.tokens
