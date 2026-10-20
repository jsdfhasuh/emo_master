import json

from prototypes.runtime_pages_p0.watchdog import supervised


def test_legacy_upload_and_real_rpc_cleanup_faults():
    stdout, _ = supervised("network-faults", timeout=90)
    evidence = json.loads(stdout.strip().splitlines()[-1])
    assert evidence["compatibility_status"] == "PASS"
    assert len(evidence["rows"]) == 15


def test_two_job_async_saturation_and_asset_cancel_lifetime():
    stdout, _ = supervised("pipeline-faults", timeout=60)
    evidence = json.loads(stdout.strip().splitlines()[-1])
    assert evidence["correctness_status"] == "PASS"
    assert evidence["children_after"] == 0
