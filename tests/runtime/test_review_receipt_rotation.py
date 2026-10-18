from copy import deepcopy

import pytest

from emo_master.apps.runtime.project_config.coordinator import ConfigurationCoordinator
from emo_master.apps.runtime.project_config.store import ConfigurationError
from tests.runtime.test_project_configuration import authority, request, finish


def testSuccessfulRequestsRotateWithoutReplayingEvictedWrites(tmp_path, monkeypatch):
    import emo_master.apps.runtime.project_config.coordinator as coordinator
    import emo_master.apps.runtime.project_config.store as store
    monkeypatch.setattr(coordinator, "REQUEST_WINDOW", 4)
    monkeypatch.setattr(store, "RECEIPT_WINDOW", 6)
    preparations = []
    c = authority(tmp_path, prepare=lambda payload, cancel: preparations.append(payload) or payload)
    try:
        first = request(c)
        for index in range(20):
            write = first if index == 0 else request(c, f"write-{index + 1}", exposure=8000 + index)
            c.start(write)
            assert finish(c, write["requestId"])["state"] == "COMMITTED"
            assert len(c.requests) <= 4
        assert len(preparations) == 20
        assert c.start(write)["state"] == "COMMITTED"
        with pytest.raises(ConfigurationError, match="content changed"):
            c.start(dict(write, localPayload={}))
        with pytest.raises(ConfigurationError, match="revision changed"):
            c.start(first)
        with c.store.sqlite.connection() as connection:
            assert connection.execute("SELECT count(*) FROM projectWriteReceipts").fetchone()[0] == 6
        restored = ConfigurationCoordinator(c.store, "restart", prepare=c.prepare, publish=lambda p, r: None)
        assert restored.start(write)["state"] == "COMMITTED"
        with pytest.raises(ConfigurationError, match="restarted"):
            restored.start(first)
        assert len(preparations) == 20
    finally:
        c.close()


def testFenceRotationRollbackDoesNotForgetCancelledAttempts(tmp_path, monkeypatch):
    from threading import Event
    import emo_master.apps.runtime.project_config.coordinator as module
    monkeypatch.setattr(module, "REQUEST_WINDOW", 2)
    entered, release = Event(), Event()
    effects = []
    def prepare(payload, cancelled):
        effects.append(payload)
        entered.set()
        assert release.wait(5)
        return payload
    c = authority(tmp_path, prepare=prepare)
    try:
        first = request(c)
        for index in range(2):
            entered.clear()
            release.clear()
            write = dict(first, requestId=f"cancelled-{index}")
            c.start(write)
            assert entered.wait(5)
            c.cancel(write["requestId"])
            release.set()
            assert finish(c, write["requestId"])["state"] == "CANCELLED"
        before = c.snapshot()
        with c.store.sqlite.connection() as connection:
            connection.execute("CREATE TRIGGER failHistory BEFORE INSERT ON onlineProjectHistory BEGIN SELECT RAISE(ABORT,'rotation rollback'); END")
        with pytest.raises(Exception, match="rotation rollback"):
            c.start(dict(first, requestId="overflow"))
        assert c.snapshot() == before and len(c.requests) == 2
        assert c.store.snapshot()["documentRevision"] == before["documentRevision"]
        assert c.start(write)["state"] == "CANCELLED" and len(effects) == 2
        with c.store.sqlite.connection() as connection:
            connection.execute("DROP TRIGGER failHistory")
        with pytest.raises(ConfigurationError, match="window rotated"):
            c.start(dict(first, requestId="overflow"))
        with pytest.raises(ConfigurationError, match="revision changed"):
            c.start(dict(first, requestId="cancelled-0"))
        assert len(effects) == 2
    finally:
        release.set()
        c.close()


def testFailedSameRevisionAttemptsRotateFenceWithoutReplayingEffects(tmp_path, monkeypatch):
    import emo_master.apps.runtime.project_config.coordinator as coordinator
    monkeypatch.setattr(coordinator, "REQUEST_WINDOW", 3)
    effects = []
    def fail(payload, cancelled):
        effects.append(payload)
        raise ValueError("failed after a simulated preparation effect")
    c = authority(tmp_path, prepare=fail)
    try:
        first = request(c)
        for index in range(3):
            write = dict(first, requestId=f"failed-{index}")
            c.start(write)
            assert finish(c, write["requestId"])["state"] == "FAILED"
        old = deepcopy(c.current)
        assert c.start(write)["state"] == "FAILED"
        with pytest.raises(ConfigurationError, match="window rotated"):
            c.start(dict(first, requestId="fresh"))
        assert c.current["payload"] == old["payload"]
        assert c.current["executionRevision"] == old["executionRevision"]
        assert c.current["documentRevision"] == old["documentRevision"] + 1
        with pytest.raises(ConfigurationError, match="revision changed"):
            c.start(dict(first, requestId="failed-0"))
        assert len(effects) == 3
        c.prepare = lambda payload, cancelled: payload
        c.start(request(c, "after-rotation"))
        assert finish(c, "after-rotation")["state"] == "COMMITTED"
    finally:
        c.close()
