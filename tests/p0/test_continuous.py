import json

from prototypes.runtime_pages_p0.watchdog import supervised


def test_long_lived_runner_to_two_network_image_consumers():
    stdout, _ = supervised("continuous", timeout=60)
    report = json.loads(stdout.strip().splitlines()[-1])
    assert report["coverage"] == [1.0, 1.0]
    for client in report["clients"]:
        assert [row["ordinal"] for row in client] == list(range(6, 46))
    assert report["stats"]["runs"] == 0
    assert report["stats"]["metadata"] <= 32
    assert report["stats"]["resources"]["used"]["cache"] > 0
