"""Source selection and Runtime configuration at the development entry point."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("development_launcher", ROOT / "scripts/dev.py")
assert spec is not None and spec.loader is not None
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def test_source_import_from_unrelated_cwd_and_inherited_work_copy(tmp_path, monkeypatch):
    otherSource = tmp_path / "other-source"
    otherSource.mkdir()
    (otherSource / "emo_master.py").write_text("raise RuntimeError('wrong checkout')")
    monkeypatch.setenv("PYTHONPATH", str(otherSource))
    monkeypatch.chdir(tmp_path)
    result = dev.runCommand([
        sys.executable, "-c",
        "import emo_master; from pathlib import Path; import sys; "
        "assert Path(emo_master.__file__).samefile(sys.argv[1])",
        str(ROOT / "src/emo_master/__init__.py"),
    ])
    assert result == 0
    assert os.environ["PYTHONPATH"] == str(otherSource)


def test_local_profile_isolates_child_and_keeps_parent_environment(monkeypatch):
    previous = {
        "EMO_RUNTIME_TARGET": "127.0.0.1:12345",
        "EMO_RUNTIME_DB_PATH": "external.db",
        "EMO_MASTER_RUNTIME_DB_PATH": "legacy.db",
        "EMO_RUNTIME_DATA_DIR": "external-data",
        "EMO_RUNTIME_LOG_DIR": "external-logs",
        "QT_QPA_PLATFORM": "offscreen",
    }
    for name, value in previous.items():
        monkeypatch.setenv(name, value)
    ordinary = dev.commandEnvironment()
    local = dev.commandEnvironment(localDesigner=True)
    for name, value in previous.items():
        assert ordinary[name] == value
        assert os.environ[name] == value
        if name != "EMO_RUNTIME_DATA_DIR":
            assert name not in local
    assert local["EMO_RUNTIME_DATA_DIR"] == str(
        ROOT / "manual_test_workspace/runtime-embedded"
    )


def test_child_failure_is_propagated():
    assert dev.runCommand([sys.executable, "-c", "raise SystemExit(7)"]) == 7


def test_environment_check_is_real_and_does_not_construct_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EMO_RUNTIME_DATA_DIR", str(tmp_path / "unused-data"))
    assert dev.runDesigner(check=True) == 0
    assert not (tmp_path / "unused-data").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch entry")
def test_double_click_entry_from_path_with_spaces_without_activation(tmp_path):
    # A space-containing checkout path also exercises quoting at the cmd boundary.
    checkout = tmp_path / "source work copy"
    checkout.mkdir()
    (checkout / "start_designer.cmd").write_bytes((ROOT / "start_designer.cmd").read_bytes())
    scripts = checkout / "scripts"
    scripts.mkdir()
    (scripts / "dev.py").write_bytes((ROOT / "scripts/dev.py").read_bytes())
    subprocess.run([
        "cmd.exe", "/c", "mklink", "/J", str(checkout / "src"), str(ROOT / "src")
    ], check=True, capture_output=True)
    environment = dict(os.environ, EMO_MASTER_PYTHON=sys.executable,
                       EMO_RUNTIME_TARGET="bad-target", QT_QPA_PLATFORM="offscreen")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", str(checkout / "start_designer.cmd"), "--check"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Runtime: embedded" in result.stdout
    assert "Designer startup check passed" in result.stdout
    assert not (checkout / "manual_test_workspace").exists()
