import json
import multiprocessing
import threading
from types import SimpleNamespace

from prototypes.runtime_pages_p0 import continuous

from prototypes.runtime_pages_p0.watchdog import supervised


def testResourceSamplingKeepsRetiredExporterUnavailable(monkeypatch):
    retired = multiprocessing.get_context("spawn").Process()
    retired.close()
    lock = threading.Lock()
    exporter = SimpleNamespace(lock=lock, dead={0}, slots=[(retired, None), (SimpleNamespace(pid=123), None)])
    measured = []
    def sample(pid):
        assert lock.locked()  # The owner cannot close this wrapper during sampling.
        measured.append(pid)
        return dict(rss_bytes=456)
    monkeypatch.setattr(continuous, "resources", sample)
    assert continuous.exporterResources(exporter) == [
        dict(status="UNAVAILABLE", reason="exporter process retired", slot=0), dict(rss_bytes=456)]
    assert measured == [123]


def test_long_lived_runner_to_two_network_image_consumers():
    stdout, _ = supervised("continuous", timeout=60)
    report = json.loads(stdout.strip().splitlines()[-1])
    assert report["coverage"] == [1.0, 1.0]
    for client in report["clients"]:
        assert [row["ordinal"] for row in client] == list(range(6, 46))
    assert report["stats"]["runs"] == 0
    assert report["stats"]["metadata"] <= 32
    assert report["stats"]["resources"]["used"]["cache"] > 0
