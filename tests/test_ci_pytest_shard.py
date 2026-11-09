"""Exercise shard coverage with real pytest processes and a small synthetic suite."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci_shard_summary import summarize


ROOT = Path(__file__).resolve().parents[1]


def writeFile(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def syntheticSuite(tmp_path):
    writeFile(tmp_path, "pytest.ini", "[pytest]\ntestpaths = tests examples\n")
    writeFile(tmp_path, "conftest.py", """import os
from pathlib import Path

def pytest_runtest_logstart(nodeid, location):
    with Path(os.environ['SHARD_TEST_TRACE']).open('a', encoding='utf-8') as stream:
        stream.write(nodeid + '\\n')
""")
    writeFile(tmp_path, "tests/designer/test_window.py", """import pytest

@pytest.mark.parametrize('value', [1, 2])
def test_window(value):
    assert value > 0
""")
    for path in ("tests/ui/test_panel.py", "tests/runtime/nested/test_job.py",
                 "tests/ui/presentation/nested/test_results.py", "tests/ui/operator_view/test_monitor.py",
                 "tests/future_area/test_new_area.py", "examples/test_example.py"):
        writeFile(tmp_path, path, "def test_case():\n    assert True\n")
    writeFile(tmp_path, "tests/test_skip.py", """import pytest

@pytest.mark.skip(reason='Synthetic conditional skip')
def test_skipped():
    raise AssertionError('Skipped case must remain skipped')
""")
    return tmp_path


def runPytest(root, name, *options):
    trace = root / f"{name}-executed.txt"
    env = dict(os.environ, PYTHONPATH=str(ROOT), SHARD_TEST_TRACE=str(trace),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "scripts.ci_pytest_shard",
                             "-q", *options], cwd=root, env=env, capture_output=True,
                            text=True, timeout=30)
    executed = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result, executed


def testRealShardsKeepDefaultCoverageParametersNewPathsAndSkips(syntheticSuite):
    root = syntheticSuite
    baseline, complete = runPytest(root, "full")
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert len(complete) == 9
    reports = []
    for shard in ("runtime", "other"):
        path = root / f"{shard}.json"
        result, executed = runPytest(root, shard, f"--ci-shard={shard}",
                                    f"--ci-shard-report={path}")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "deselected" in result.stdout
        report = json.loads(path.read_text(encoding="utf-8"))
        assert report["collected"] == complete
        assert report["selected"] == executed == report["executed"]
        reports.append(report)
    assert reports[0]["selected"] == ["tests/runtime/nested/test_job.py::test_case",
                                      "tests/ui/operator_view/test_monitor.py::test_case",
                                      "tests/ui/presentation/nested/test_results.py::test_case"]
    assert len(reports[1]["selected"]) == 6
    assert set(reports[0]["selected"]).isdisjoint(reports[1]["selected"])
    assert set(reports[0]["selected"] + reports[1]["selected"]) == set(complete)
    assert "1 skipped" in result.stdout

    # The aggregation job can run without pytest or any installed project packages.
    summary = root / "summary.md"
    result = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/ci_shard_summary.py"),
                             str(root / "runtime.json"), str(root / "other.json")],
                            cwd=root, env=dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "9 collected cases covered" in result.stdout
    assert "runtime=3, other=6" in result.stdout
    assert "original pytest skips remain skips" in summary.read_text(encoding="utf-8")


@pytest.mark.parametrize("options", [("--ci-shard=unknown",), ("--ci-shard=runtime",)])
def testInvalidShardInvocationCannotRunOrPass(syntheticSuite, options):
    result, executed = runPytest(syntheticSuite, "invalid", *options)
    assert result.returncode != 0
    assert not executed


def testCollectOnlyCannotMasqueradeAsExecutedShard(syntheticSuite):
    report = syntheticSuite / "collected.json"
    result, executed = runPytest(syntheticSuite, "collect-only", "--collect-only",
                                "--ci-shard=runtime", f"--ci-shard-report={report}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not executed
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["selected"] and not data["executed"]
    with pytest.raises(ValueError, match="not all executed"):
        summarize(report, syntheticSuite / "unused.json")


def testRealTestFailureCannotBecomeSuccessfulManifest(syntheticSuite):
    writeFile(syntheticSuite, "tests/runtime/test_failing.py", "def test_failure():\n    assert False\n")
    report = syntheticSuite / "failed.json"
    result, _ = runPytest(syntheticSuite, "failed", "--ci-shard=runtime",
                          f"--ci-shard-report={report}")
    assert result.returncode == 1
    assert json.loads(report.read_text(encoding="utf-8"))["exitstatus"] == 1
    with pytest.raises(ValueError, match="did not finish successfully"):
        summarize(report, syntheticSuite / "unused.json")


@pytest.mark.parametrize("problem", ["missing-report", "incomplete-union", "overlap",
                                     "different-collection", "unexecuted", "duplicate"])
def testSummaryRejectsMissingOrInconsistentEvidence(tmp_path, problem):
    collected = ["tests/runtime/test_job.py::test_a", "tests/designer/test_window.py::test_b"]
    reports = [{"shard": shard, "exitstatus": 0, "collected": collected.copy(),
                "selected": [case], "executed": [case]}
               for shard, case in zip(("runtime", "other"), collected)]
    if problem == "incomplete-union":
        for report in reports:
            report["collected"].append("examples/test_new.py::test_new")
    elif problem == "overlap":
        reports[1]["selected"].append(collected[0])
        reports[1]["executed"].append(collected[0])
    elif problem == "different-collection":
        reports[1]["collected"].append("tests/test_new.py::test_new")
    elif problem == "unexecuted":
        reports[1]["executed"] = []
    elif problem == "duplicate":
        reports[1]["selected"].append(collected[1])
        reports[1]["executed"].append(collected[1])
    paths = [tmp_path / "runtime.json", tmp_path / "other.json"]
    for path, report in zip(paths, reports):
        path.write_text(json.dumps(report), encoding="utf-8")
    if problem == "missing-report":
        paths[1].unlink()
    with pytest.raises((OSError, ValueError)):
        summarize(*paths)
