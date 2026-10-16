import json

import pytest

from prototypes.runtime_pages_p0.watchdog import supervised


@pytest.mark.parametrize("scenario", ["exports", "network"])
def test_real_spawn_faults_and_loopback_with_outer_deadline(scenario):
    stdout, stderr = supervised(scenario)
    evidence = json.loads(stdout.strip().splitlines()[-1])
    assert evidence["quota_rejected"]
    assert "leaked" not in stderr
