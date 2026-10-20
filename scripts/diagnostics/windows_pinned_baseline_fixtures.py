"""Explicit offline source/isolation gates; no controller or Runtime workload."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


BASE = "655eeac84d0602aa670c76af67679509782bbd7f"
EXECUTION = "ecf8bcfcf193e05807556340d670f05c0f3d9e15"
TOOLS = "0c1ac2fddf453913a29e03b2cb84043ca7d92afe"
NONCE = "diagnostic: one pinned baseline Windows trace 655eeac 19d4e683"
ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/runtime-kernel-pinned-baseline.yml"


def workflow():
    return yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)


@pytest.fixture(scope="module")
def pinned_workspace(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("pinned-source")
    for directory, pin in (("execution", EXECUTION), ("tools", TOOLS)):
        git(ROOT, "clone", "--shared", "--no-checkout", str(ROOT), str(workspace / directory))
        git(workspace / directory, "checkout", "--detach", pin)
    (workspace / "private").mkdir()
    return workspace


def provenance(workspace, *, cwd="execution", extra_env=None):
    steps = workflow()["jobs"]["trace"]["steps"]
    source = next(step["run"] for step in steps if step.get("id") == "sources")
    code = source.split("@'\n", 1)[1].split("\n'@ | python -", 1)[0]
    # This is a Linux offline fixture. No GitHub identity is synthesized, and the
    # extracted code has no control_run call or native capture capability probe.
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment.update(extra_env or {})
    return subprocess.run([sys.executable, "-", str(workspace), str(workspace / "private")],
                          input=code, text=True, cwd=workspace / cwd, env=environment,
                          capture_output=True, timeout=30)


def testOneShotBoundaryAndTwoPinnedCheckouts():
    spec = workflow()
    job = spec["jobs"]["trace"]
    assert spec["on"] == {"push": {"branches": ["agent/runtime-workflow-architecture-v1"],
                                 "paths": [".github/workflows/runtime-kernel-pinned-baseline.yml"]}}
    assert "github.run_attempt == 1" in job["if"]
    assert "github.event.before == '" + TOOLS + "'" in job["if"]
    assert "github.event.head_commit.message == '" + NONCE + "'" in job["if"]
    assert job["timeout-minutes"] == "30"
    assert spec["permissions"] == {"contents": "read"}
    checkouts = [step["with"] for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
    assert checkouts == [
        {"ref": EXECUTION, "path": "execution", "persist-credentials": "false", "fetch-depth": "2"},
        {"ref": TOOLS, "path": "tools", "persist-credentials": "false", "fetch-depth": "2"},
    ]


def testSeparateProcessesAndPrivateFailureGates():
    steps = workflow()["jobs"]["trace"]["steps"]
    sources = next(step for step in steps if step.get("id") == "sources")
    preflight = next(step for step in steps if step.get("id") == "preflight")
    capture = next(step for step in steps if step.get("id") == "original_tests")
    assert sources["working-directory"] == capture["working-directory"] == "execution"
    assert preflight["working-directory"] == "tools"
    assert preflight["if"] == "steps.sources.outputs.ready == 'true'"
    assert capture["if"] == "steps.preflight.outputs.ready == 'true'"
    assert "python -m pytest -q scripts/diagnostics/windows_commit_trace_fixtures.py" in preflight["run"]
    assert "python scripts/r3_windows_commit_trace.py --control $root $reader" in capture["run"]
    assert "provenance.private" in sources["run"]
    for stage in ("offline_fixtures", "compiler_discovery", "compiler_build", "native_preflight"):
        assert "$stage = '" + stage + "'; $reason = '" + stage + "_failed'" in preflight["run"]
    assert "native_exit=$nativeExit" in preflight["run"] and "capture_attempted=$false" in preflight["run"]
    source = WORKFLOW.read_text()
    for forbidden in ("upload-artifact", "actions/cache", "workflow_dispatch", "$env:GITHUB_SHA =",
                      "$env:GITHUB_WORKSPACE =", "$env:PYTHONPATH =", "$env:PYTEST_PLUGINS =", "-start"):
        assert forbidden not in source
    assert source.count("--control") == 1
    cleanup = steps[-1]
    assert cleanup["if"] == "always()"
    assert "start-attempted.private" in cleanup["run"]
    assert "WaitForExit(15000)" in cleanup["run"]
    assert "0xc5583000L" in cleanup["run"]


def testExactPinsAndActualImportedOriginsPass(pinned_workspace):
    result = provenance(pinned_workspace)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("environment", [
    {"PYTHONPATH": "unexpected"},
    {"PYTEST_PLUGINS": "unexpected"},
])
def testUnexpectedImportEnvironmentFailsBeforeCapture(pinned_workspace, environment):
    result = provenance(pinned_workspace, extra_env=environment)
    assert result.returncode != 0 and "unexpected_import_environment" in result.stderr


def testToolsWorkingDirectoryCannotBecomeExecution(pinned_workspace):
    result = provenance(pinned_workspace, cwd="tools")
    assert result.returncode != 0 and "checkout_location" in result.stderr


@pytest.mark.parametrize("directory,path", [
    ("execution", "src/emo_master/apps/runtime/context/sqlite_store.py"),
    ("tools", "scripts/diagnostics/windows_commit_trace_fixtures.py"),
])
def testDirtyPinnedSourcesFailBeforeCapture(pinned_workspace, directory, path):
    target = pinned_workspace / directory / path
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# offline source rejection fixture\n")
        result = provenance(pinned_workspace)
        assert result.returncode != 0 and "checkout_identity" in result.stderr
    finally:
        target.write_bytes(original)


def testWrongToolsRevisionFailsBeforeCapture(pinned_workspace):
    tools = pinned_workspace / "tools"
    try:
        git(tools, "checkout", "--detach", EXECUTION)
        result = provenance(pinned_workspace)
        assert result.returncode != 0 and "checkout_identity" in result.stderr
    finally:
        git(tools, "checkout", "--detach", TOOLS)
