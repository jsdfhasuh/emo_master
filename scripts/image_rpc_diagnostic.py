"""Opt-in adapter for the original 96-frame test and existing bounded observer.

Run only in the explicitly approved single Ubuntu diagnostic, not normal CI.
No original assertion, client deadline, quota, retry policy or product file changes.
Use only in an authorized environment that permits the original loopback test.
"""
from pathlib import Path
import json
import subprocess
import time

import pytest

TARGET = ("tests/runtime/presentation/test_live_decisions.py::"
          "testTwoNetworkConsumersAll96ChangingImagesAndExplicitSupersession")


def pytest_addoption(parser):
    parser.addoption("--asset-rpc-diagnostic-dir", default=None,
                     help="New local directory for one original-test observation")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if hasattr(item, "_asset_rpc_reports"):
        report = outcome.get_result()
        item._asset_rpc_reports.append({"when": report.when, "outcome": report.outcome,
                                        "duration_seconds": report.duration})


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    destination = item.config.getoption("--asset-rpc-diagnostic-dir")
    if item.nodeid != TARGET or not destination:
        yield
        return
    from scripts.r3_asset_split_trace import AssetSplitTrace, installed
    from scripts.r3_measure import patches

    root = Path(str(item.config.rootpath))
    output = Path(destination).resolve()
    output.mkdir(parents=True, exist_ok=False)
    trace = AssetSplitTrace(passive_markers=True)
    item._asset_rpc_reports = []
    manifest = {"source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                "nodeid": item.nodeid,
                "role": "Original unchanged test plus opt-in existing stage observer; no performance verdict",
                "network_execution": "Run only when loopback is authorized"}
    setup_started = time.perf_counter_ns()
    # Protocol scope starts before fixture setup and exits after fixture cleanup,
    # so existing AssetStore lock and server/session owners are observed safely.
    with patches() as patch:
        with installed(trace, patch, root):
            setup_finished = time.perf_counter_ns()
            yield
            teardown_started = time.perf_counter_ns()
    teardown_finished = time.perf_counter_ns()
    save_started = time.perf_counter_ns()
    try:
        manifest["diagnostic"] = trace.save(output / "asset-split.json")
    except Exception as error:
        # Diagnostic failure must not turn the original failing test into a pass,
        # nor replace its error. Retain only the exception type, not private data.
        manifest["diagnostic"] = {"complete": False, "error_type": type(error).__name__}
    manifest["test_reports"] = item._asset_rpc_reports
    manifest["expected_asset_calls_if_test_reaches_all_frames"] = 192
    diagnostic = manifest["diagnostic"]
    manifest["full_call_coverage"] = (diagnostic.get("complete") is True
        and diagnostic.get("client_calls_started") == diagnostic.get("client_calls_finished") == 192)
    manifest["original_test_passed"] = (len(item._asset_rpc_reports) == 3
        and all(report["outcome"] == "passed" for report in item._asset_rpc_reports))
    manifest["observer_overhead"] = {
        "installation_wall_ms": (setup_finished - setup_started) / 1e6,
        "restoration_wall_ms": (teardown_finished - teardown_started) / 1e6,
        "trace_save_wall_ms": (time.perf_counter_ns() - save_started) / 1e6,
        "during_test_effect": "NOT_IDENTIFIED: no paired uninstrumented run was authorized",
        "interpretation": "Setup/restoration/save are outside the original test protocol. "
            "In-test observer callbacks and locks can affect scheduling; recorded phase spans "
            "include such interference and must not be presented as zero-overhead timings."}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
